"""
Utility functions for the SGA XBlock
"""
import datetime
import hashlib
import os
import time
from functools import partial

import pytz
from django.conf import settings
from django.core.files.storage import default_storage

BLOCK_SIZE = 2 ** 10 * 8  # 8kb


def utcnow():
    """
    Get current date and time in UTC
    """
    return datetime.datetime.now(tz=pytz.utc)


def is_finalized_submission(submission_data):
    """
    Helper function to determine whether or not a Submission was finalized by the student
    """
    if submission_data and submission_data.get('answer') is not None:
        return submission_data['answer'].get('finalized', True)
    return False


def get_file_modified_time_utc(file_path):
    return default_storage.get_modified_time(file_path)


def get_sha1(file_descriptor):
    """
    Get file hex digest (fingerprint).
    """
    sha1 = hashlib.sha1()
    for block in iter(partial(file_descriptor.read, BLOCK_SIZE), b''):
        sha1.update(block)
    file_descriptor.seek(0)
    return sha1.hexdigest()


def get_file_storage_path(locator, file_hash, original_filename):
    """
    Returns the file path for an uploaded SGA submission file
    """
    return (
        '{loc.org}/{loc.course}/{loc.block_type}/{loc.block_id}/{file_hash}{ext}'.format(
            loc=locator,
            file_hash=file_hash,
            ext=os.path.splitext(original_filename)[1]
        )
    )


def file_contents_iter(file_path):
    """
    Returns an iterator over the contents of a file located at the given file path
    """
    file_descriptor = default_storage.open(file_path)
    return iter(partial(file_descriptor.read, BLOCK_SIZE), b'')
