import json
import logging
import mimetypes
import os

import epicbox
import pkg_resources
import pytz
import six
from common.djangoapps.student.models import user_by_anonymous_id
from contextlib import closing
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files import File
from django.core.files.storage import default_storage
from openedx.core.djangoapps.course_groups.cohorts import get_cohort, is_course_cohorted, get_course_cohorts
from submissions import api as submissions_api
from web_fragments.fragment import Fragment
from zipfile import ZipFile
from webob.response import Response
from xblock.completable import CompletableXBlockMixin
from xblock.core import XBlock
from xblock.exceptions import JsonHandlerError
from xblock.fields import Float, Scope, String
from xblock.scorable import ScorableXBlockMixin, Score
from xblockutils.resources import ResourceLoader
from xblockutils.studio_editable import StudioEditableXBlockMixin
from xmodule.contentstore.content import StaticContent

from nand2tetris.utils import (file_contents_iter, get_file_modified_time_utc,
                               get_file_storage_path, get_sha1)
from nand2tetris.tasks import (get_zip_file_name, get_zip_file_path,
                               zip_student_submissions)

log = logging.getLogger(__name__)
loader = ResourceLoader(__name__)

ITEM_TYPE = "nand2tetrisxblock"

epicbox.configure(
    profiles=[
        epicbox.Profile('nand2tetris', 'tcarreira/nand2tetris-autograder:2.6.1-epicbox')
    ]
)
limits = {'cputime': 1, 'memory': 64}


def reify(meth):
    """
    Decorator which caches value so it is only computed once.
    Keyword arguments:
    inst
    """

    def getter(inst):
        """
        Set value to meth name in dict and returns value.
        """
        value = meth(inst)
        inst.__dict__[meth.__name__] = value
        return value

    return property(getter)


class Nand2TetrisXBlock(XBlock, ScorableXBlockMixin, CompletableXBlockMixin, StudioEditableXBlockMixin):
    project = String(display_name="project",
                     default="00",
                     scope=Scope.settings,
                     help="Um dos projects <a href=\"https://hub.docker.com/r/tcarreira/nand2tetris-autograder\">desta tabela</a>.")

    student_score = Float(display_name="student_score",
                          default=-1,
                          scope=Scope.user_state)

    display_name = String(display_name="display_name",
                          default="Submissão projeto Nand2Tetris",
                          scope=Scope.settings,
                          help="Nome do componente na plataforma")

    cohort = String(display_name="cohort",
                    default="",
                    scope=Scope.preferences,
                    help="Turma selecionada para visualização de submissões")

    editable_fields = ('display_name', 'project')
    icon_class = 'problem'
    block_type = 'problem'
    has_score = True
    has_author_view = True
    STUDENT_FILEUPLOAD_MAX_SIZE = 4 * 1000 * 1000

    # ----------- Views -----------
    def author_view(self, _context):
        return Fragment("Clica em preview ou live para veres o conteúdo deste bloco. Para configurares o número do projeto, clica em \"Edit\".")

    def student_view(self, _context):
        # pylint: disable=no-member
        """
        The primary view of the StaffGradedAssignmentXBlock, shown to students
        when viewing courses.
        """
        data = self.get_student_view_base_data()

        if self.is_course_staff():
            data['is_course_staff'] = True
            data['is_course_cohorted'] = is_course_cohorted(self.course_id)
            data['cohorts'] = [group.name for group in get_course_cohorts(course_id=self.course_id)]
            data['cohort'] = self.cohort
            data['submissions'] = self.get_sorted_submissions()

        html = loader.render_django_template('templates/nand2tetris_student.html', data)
        frag = Fragment(html)

        if self.is_course_staff():
            frag.add_css(resource_string("static/css/theme.blue.min.css"))
            frag.add_javascript(resource_string("static/js/jquery.tablesorter.combined.min.js"))

        frag.add_javascript(resource_string("static/js/nand2tetris_student.js"))
        frag.initialize_js('Nand2TetrisXBlock', data)

        frag.add_css(resource_string("static/css/nand2tetris.css"))
        return frag

    # ----------- Handlers -----------
    @XBlock.handler
    def load_student_submission(self, request, suffix=''):
        require(self.is_course_staff())
        require('student_id' in request.params)
        student_id = request.params['student_id']
        data = self.get_student_view_base_data(student_id)
        return Response(body=loader.render_django_template('templates/submission_status.html', data))

    @XBlock.json_handler
    def change_cohort(self, data, _suffix):
        self.cohort = data["cohort"]
        return {
            'result': 'success'
        }

    @XBlock.handler
    def upload_assignment(self, request, suffix=''):
        # pylint: disable=unused-argument
        """
        Save a students submission file.
        """
        user = self.get_real_user()
        require(user)
        upload = request.params['assignment']
        sha1 = get_sha1(upload.file)
        if self.file_size_over_limit(upload.file):
            raise JsonHandlerError(
                413, 'Não foi possível fazer upload do ficheiro. Tamanho máximo é {size}'.format(
                    size=self.student_upload_max_size()
                )
            )
        path = self.file_storage_path(sha1, upload.file.name)
        uploaded_file = File(upload.file)
        file_data = uploaded_file.open('rb')
        files = [{'name': 'submissao.zip', 'content': file_data.read()}]
        result = epicbox.run('nand2tetris', self.project + ".test", files=files,
                             limits=limits)
        output = result["stdout"]
        stderr = result["stderr"]
        try:
            output = output.decode('utf-8')
            stderr = stderr.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            pass

        score = 0
        max_score = 0
        self.student_score = 0.0
        try:
            output = json.loads(output)["tests"]
            for test in output:
                score += int(test["score"])
                max_score += int(test["max_score"])
            if max_score > 0:
                self.student_score = score / max_score
        except:
            pass
        self.emit_completion(self.student_score)
        self._publish_grade(self.get_score(), False)

        answer = {
            "sha1": sha1,
            "filename": upload.file.name,
            "mimetype": mimetypes.guess_type(upload.file.name)[0],
            "result": json.dumps({"output": output, "stderr": stderr}),
            "score": json.dumps({"final": int(self.student_score * 100), "score": score, "max_score": max_score})
        }
        student_item_dict = self.get_student_item_dict()
        submissions_api.create_submission(student_item_dict, answer)
        log.info("Saving file: %s at path: %s for user: %s", upload.file.name, path, user.username)
        if default_storage.exists(path):
            # save latest submission
            default_storage.delete(path)
        default_storage.save(path, uploaded_file)
        return Response(json_body=answer)

    @XBlock.handler
    def download_assignment(self, request, suffix=''):
        # pylint: disable=unused-argument
        """
        Fetch student assignment from storage and return it.
        """
        answer = self.get_submission()['answer']
        path = self.file_storage_path(answer['sha1'], answer['filename'])
        return self.download(path, answer['mimetype'], answer['filename'])

    @XBlock.handler
    def staff_download(self, request, suffix=''):
        # pylint: disable=unused-argument
        """
        Return an assignment file requested by staff.
        """
        require(self.is_course_staff())
        submission = self.get_submission(request.params['student_id'])
        answer = submission['answer']
        path = self.file_storage_path(answer['sha1'], answer['filename'])
        return self.download(
            path,
            answer['mimetype'],
            answer['filename'],
            require_staff=True
        )

    @XBlock.handler
    def prepare_download_submissions(self, request, suffix=''):  # pylint: disable=unused-argument
        """
        Runs a async task that collects submissions in background and zip them.
        """
        # pylint: disable=no-member
        require(self.is_course_staff())
        user = self.get_real_user()
        require(user)
        zip_file_ready = False
        location = str(self.location)

        if self.is_zip_file_available(user):
            log.info("Zip file already available for block: %s for instructor: %s", location, user.username)
            assignments = self.get_sorted_submissions()
            if assignments:
                last_assignment_date = assignments[0]['timestamp'].astimezone(pytz.utc)
                zip_file_path = get_zip_file_path(
                    user.username,
                    self.block_course_id,
                    self.block_id,
                    self.location
                )
                zip_file_time = get_file_modified_time_utc(zip_file_path)
                log.info(
                    "Zip file modified time: %s, last zip file time: %s for block: %s for instructor: %s",
                    last_assignment_date,
                    zip_file_time,
                    location,
                    user.username
                )
                # if last zip file is older the last submission then recreate task
                if zip_file_time >= last_assignment_date:
                    zip_file_ready = True

                # check if some one reset submission. If yes the recreate zip file
                assignment_count = len(assignments)
                if self.count_archive_files(user) != assignment_count:
                    zip_file_ready = False

        if not zip_file_ready:
            log.info("Creating new zip file for block: %s for instructor: %s", location, user.username)
            zip_student_submissions.delay(
                self.block_course_id,
                self.block_id,
                location,
                user.username
            )

        return Response(json_body={
            "downloadable": zip_file_ready
        })

    @XBlock.handler
    def download_submissions(self, request, suffix=''):  # pylint: disable=unused-argument
        """
        Api for downloading zip file which consist of all students submissions.
        """
        # pylint: disable=no-member
        require(self.is_course_staff())
        user = self.get_real_user()
        require(user)
        try:
            zip_file_path = get_zip_file_path(
                user.username,
                self.block_course_id,
                self.block_id,
                self.location
            )
            zip_file_name = get_zip_file_name(
                user.username,
                self.block_course_id,
                self.block_id
            )
            return Response(
                app_iter=file_contents_iter(zip_file_path),
                content_type='application/zip',
                content_disposition="attachment; filename=" + zip_file_name
            )
        except OSError:
            return Response(
                "Sorry, submissions cannot be found. Press Collect ALL Submissions button or"
                " contact {} if you issue is consistent".format(settings.TECH_SUPPORT_EMAIL),
                status_code=404
            )

    @XBlock.handler
    def download_submissions_status(self, request, suffix=''):  # pylint: disable=unused-argument
        """
        returns True if zip file is available for download
        """
        require(self.is_course_staff())
        user = self.get_real_user()
        require(user)
        return Response(
            json_body={
                "zip_available": self.is_zip_file_available(user)
            }
        )

    # ----------- Submissions -----------
    def get_student_view_base_data(self, student_id=None):
        data = {
            'xblock_id': self._get_xblock_loc(),
            "max_file_size": self.student_upload_max_size(),
            "base_asset_url": StaticContent.get_base_url_path_for_course_assets(self.location.course_key)
        }
        submission = self.get_submission(student_id)
        if submission:
            data["filename"] = submission['answer']['filename']
            data["result"] = json.loads(submission['answer']['result'])
            data["score"] = json.loads(submission['answer']['score'])
        return data

    def get_sorted_submissions(self):
        """returns student recent assignments sorted on date"""
        assignments = []
        submissions = submissions_api.get_all_submissions(
            self.block_course_id,
            self.block_id,
            ITEM_TYPE
        )

        for submission in submissions:
            student = user_by_anonymous_id(submission['student_id'])
            sub = {
                'submission_id': submission['uuid'],
                'username': student.username,
                'student_id': submission['student_id'],
                'fullname': student.profile.name,
                'timestamp': submission['submitted_at'] or submission['created_at'],
                'filename': submission['answer']["filename"],
                'score': json.loads(submission['answer']['score']) if 'score' in submission['answer'] else 0,
                'result': json.loads(submission['answer']['result'])
            }
            if is_course_cohorted(self.course_id):
                group = get_cohort(student, self.course_id, assign=False, use_cached=True)
                sub['cohort'] = group.name if group else '(não atribuído)'
            assignments.append(sub)

        assignments.sort(
            key=lambda assignment: assignment['timestamp'], reverse=True
        )
        return assignments

    def get_student_item_dict(self, student_id=None):
        # pylint: disable=no-member
        """
        Returns dict required by the submissions app for creating and
        retrieving submissions for a particular student.
        """
        if student_id is None:
            student_id = self.xmodule_runtime.anonymous_student_id
        return {
            "student_id": student_id,
            "course_id": self.block_course_id,
            "item_id": self.block_id,
            "item_type": ITEM_TYPE,
        }

    def _get_xblock_loc(self):
        """Returns trailing number portion of self.location"""
        return str(self.location).split('@')[-1]

    def is_course_staff(self):
        """
        Return if current user is staff and not in studio.
        """
        return getattr(self.xmodule_runtime, 'user_is_staff', False)

    #  ----------- ScorableXBlockMixin -----------
    def has_submitted_answer(self):
        return self.student_score != -1

    def max_score(self):
        return 1

    def get_score(self):
        return Score(raw_earned=max(self.student_score, 0.0), raw_possible=1.0)

    def set_score(self, score):
        self.student_score = score.raw_earned / score.raw_possible

    def calculate_score(self):
        return self.get_score()

    def clear_student_state(self, *args, **kwargs):
        # pylint: disable=unused-argument
        """
        For a given user, clears submissions and uploaded files for this XBlock.
        Staff users are able to delete a learner's state for a block in LMS. When that capability is
        used, the block's "clear_student_state" function is called if it exists.
        """
        student_id = kwargs['user_id']
        for submission in submissions_api.get_submissions(
            self.get_student_item_dict(student_id)
        ):
            submission_file_sha1 = submission['answer'].get('sha1')
            submission_filename = submission['answer'].get('filename')
            submission_file_path = self.file_storage_path(submission_file_sha1, submission_filename)
            if default_storage.exists(submission_file_path):
                default_storage.delete(submission_file_path)
            submissions_api.reset_score(
                student_id,
                self.block_course_id,
                self.block_id,
                clear_state=True
            )

    def get_submission(self, student_id=None):
        """
        Get student's most recent submission.
        """
        submissions = submissions_api.get_submissions(
            self.get_student_item_dict(student_id)
        )
        if submissions:
            # If I understand docs correctly, most recent submission should
            # be first
            return submissions[0]

    def download(self, path, mime_type, filename, require_staff=False):
        """
        Return a file from storage and return in a Response.
        """
        try:
            content_disposition = "attachment; filename*=UTF-8''"
            content_disposition += six.moves.urllib.parse.quote(filename.encode('utf-8'))
            output = Response(
                app_iter=file_contents_iter(path),
                content_type=mime_type,
                content_disposition=content_disposition
            )
            return output
        except OSError:
            if require_staff:
                return Response(
                    "Sorry, assignment {} cannot be found at"
                    " {}. Please contact {}".format(
                        filename.encode('utf-8'), path, settings.TECH_SUPPORT_EMAIL
                    ),
                    status_code=404
                )
            return Response(
                "Sorry, the file you uploaded, {}, cannot be"
                " found. Please try uploading it again or contact"
                " course staff".format(filename.encode('utf-8')),
                status_code=404
            )

    def file_storage_path(self, file_hash, original_filename):
        # pylint: disable=no-member
        """
        Helper method to get the path of an uploaded file
        """
        return get_file_storage_path(self.location, file_hash, original_filename)

    def is_zip_file_available(self, user):
        """
        returns True if zip file exists.
        """
        # pylint: disable=no-member
        zip_file_path = get_zip_file_path(
            user.username,
            self.block_course_id,
            self.block_id,
            self.location
        )
        return default_storage.exists(zip_file_path)

    def count_archive_files(self, user):
        """
        returns number of files archive in zip.
        """
        # pylint: disable=no-member
        zip_file_path = get_zip_file_path(
            user.username,
            self.block_course_id,
            self.block_id,
            self.location
        )
        with default_storage.open(zip_file_path, 'rb') as zip_file:
            with closing(ZipFile(zip_file)) as archive:
                return len(archive.infolist())

    def get_real_user(self):
        """returns session user"""
        # pylint: disable=no-member
        return self.runtime.get_real_user(self.xmodule_runtime.anonymous_student_id)

    @reify
    def block_id(self):
        """
        Return the usage_id of the block.
        """
        return str(self.scope_ids.usage_id)

    @reify
    def block_course_id(self):
        """
        Return the course_id of the block.
        """
        return str(self.course_id)

    @classmethod
    def file_size_over_limit(cls, file_obj):
        """
        checks if file size is under limit.
        """
        file_obj.seek(0, os.SEEK_END)
        return file_obj.tell() > cls.student_upload_max_size()

    @classmethod
    def student_upload_max_size(cls):
        """
        returns max file size limit in system
        """
        return getattr(
            settings,
            "STUDENT_FILEUPLOAD_MAX_SIZE",
            cls.STUDENT_FILEUPLOAD_MAX_SIZE
        )


# Utils
def resource_string(path):
    """Handy helper for getting resources from our kit."""
    data = pkg_resources.resource_string(__name__, path)
    return data.decode("utf8")


def require(assertion):
    """
    Raises PermissionDenied if assertion is not true.
    """
    if not assertion:
        raise PermissionDenied
