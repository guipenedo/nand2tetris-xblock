function Nand2TetrisXBlock(runtime, element, context) {
    let id = context.xblock_id;

    function xblock($, _) {
        const uploadUrl = runtime.handlerUrl(element, 'upload_assignment');
        const downloadUrl = runtime.handlerUrl(element, 'download_assignment');
        const staffDownloadUrl = runtime.handlerUrl(element, 'staff_download');
        const downloadSubmissionsUrl = runtime.handlerUrl(element, 'download_submissions');
        const prepareDownloadSubmissionsUrl = runtime.handlerUrl(element, 'prepare_download_submissions');
        const downloadSubmissionsStatusUrl = runtime.handlerUrl(element, 'download_submissions_status');
        const loadStudentSubmissionUrl = runtime.handlerUrl(element, 'load_student_submission');
        const preparingSubmissionsMsg = 'Started preparing student submissions zip file. This may take a while.';

        // add download url
        if (context.filename)
            $(element).find("#download_link_" + id).prop("href", downloadUrl);

        if (context.is_course_staff) {
            // add download links
            $(element).find(".download_student_assignment_" + id).each(function () {
                $(this).prop("href", staffDownloadUrl + "?student_id=" + $(this).data("student_id"));
            });

            $(element).find('.view_submission_button_' + id)
                .leanModal()
                .on('click', function () {
                    let row = $(this).parents("tr");
                    $.ajax({
                        url: loadStudentSubmissionUrl,
                        type: "GET",
                        data: {"student_id": row.data('student_id')},
                        dataType: "html",
                        success: function (data) {
                            let submission = $(element).find("#view_submission_inside_" + id);
                            submission.html(data);
                            submission.find(".download_link").prop("href", staffDownloadUrl + "?student_id=" + row.data("student_id"));
                        },
                    });
                });

            $(element).find('#download-init-button_' + id).click(function (e) {
                e.preventDefault();
                const self = this;
                $.get(prepareDownloadSubmissionsUrl).then(
                    function (data) {
                        if (data["downloadable"]) {
                            window.location = downloadSubmissionsUrl;
                            $(self).removeClass("disabled");
                        } else {
                            $(self).addClass("disabled");
                            $(element).find('.task-message')
                                .show()
                                .html(preparingSubmissionsMsg)
                                .removeClass("ready-msg")
                                .addClass("preparing-msg");
                            pollSubmissionDownload();
                        }
                    }
                ).fail(
                    function () {
                        $(self).removeClass("disabled");
                        $(element).find('.task-message')
                            .show()
                            .html(
                                'The download file was not created. Please try again.'
                            )
                            .removeClass("preparing-msg")
                            .addClass("ready-msg");
                    }
                );
            });

            function pollSubmissionDownload() {
                pollUntilSuccess(downloadSubmissionsStatusUrl, checkResponse, 10000, 100).then(function () {
                    $(element).find('#download-init-button_' + id).removeClass("disabled");
                    $(element).find('.task-message')
                        .show()
                        .html("Student submission file ready for download")
                        .removeClass("preparing-msg")
                        .addClass("ready-msg");
                }).fail(function () {
                    $(element).find('#download-init-button_' + id).removeClass("disabled");
                    $(element).find('.task-message')
                        .show()
                        .html(
                            'The download file was not created. Please try again or contact %(support_email)s'
                        );
                });
            }

            $.tablesorter.addParser({
                id: "data_pt",
                is: function (_s) {
                    return false;
                },
                format: function (s, _table, _cell, _cellIndex) {
                    const mesHash = {
                        'Janeiro': 1,
                        'Fevereiro': 2,
                        'Março': 3,
                        'Abril': 4,
                        'Maio': 5,
                        'Junho': 6,
                        'Julho': 7,
                        'Agosto': 8,
                        'Setembro': 9,
                        'Outubro': 10,
                        'Novembro': 11,
                        'Dezembro': 12
                    };
                    let matches = s.match(/(de)\s+(\w*)\s+(de)/);
                    if (!matches)
                        return 0;
                    let mes = matches[2];
                    s = s.replace(mes, mesHash[mes])
                        // replace separators
                        .replace(/\s+(de)\s+/g, "/").replace(/\s+(às)\s+/g, " ")
                        // reformat dd/mm/yy to mm/dd/yy
                        .replace(/(\d{1,2})[\/\s](\d{1,2})[\/\s](\d{2})/, "$2/$1/$3");
                    return (new Date(s)).getTime();
                },
                type: "numeric"
            });

            let table_options = {
                theme: 'blue',
                headers: {
                    2: {
                        sorter: "data_pt"
                    }
                }
            };
            if (context.is_course_cohorted) {
                let turmas_filter = $('#turmas_filter_' + id);
                table_options = {
                    widgets: ['zebra', 'filter'],
                    widgetOptions: {
                        filter_columnFilters: false,
                        filter_external: turmas_filter
                    },
                    ...table_options
                }
                turmas_filter.on('change', function () {
                    const change_cohort_handlerurl = runtime.handlerUrl(element, 'change_cohort');
                    $.post(change_cohort_handlerurl, JSON.stringify({
                        'cohort': this.value
                    }));
                });
            }
            $("#submissions_" + id).tablesorter(table_options);
        }

        // Set up file upload
        const fileUpload = $(element).find('#fileupload_' + id).fileupload({
            url: uploadUrl,
            add: function (e, data) {
                const do_upload = $(element).find('#upload_' + id).html('');
                error('');
                do_upload.text('Uploading...');
                const data_max_size = context.max_file_size;
                const size = data.files[0].size;
                //if file size is larger max file size define in env(django)
                if (size >= data_max_size) {
                    error('The file you are trying to upload is too large.');
                    return;
                }
                data.submit();
            },
            progressall: function (e, data) {
                const percent = parseInt(data.loaded / data.total * 100, 10);
                $(element).find('#upload_' + id).text(
                    'Uploading... ' + percent + '%');
            },
            fail: function (e, data) {
                /**
                 * Nginx and other sanely implemented servers return a
                 * "413 Request entity too large" status code if an
                 * upload exceeds its limit.  See the 'done' handler for
                 * the not sane way that Django handles the same thing.
                 */
                if (data.jqXHR.status === 413) {
                    /* I guess we have no way of knowing what the limit is
                     * here, so no good way to inform the user of what the
                     * limit is.
                     */
                    error('The file you are trying to upload is too large.');
                } else {
                    // Suitably vague
                    error('There was an error uploading your file.');

                    // Dump some information to the console to help someone
                    // debug.
                    console.log('There was an error with file upload.');
                    console.log('event: ', e);
                    console.log('data: ', data);
                }
            },
            done: function (e, data) {
                /* When you try to upload a file that exceeds Django's size
                 * limit for file uploads, Django helpfully returns a 200 OK
                 * response with a JSON payload of the form:
                 *
                 *   {'success': '<error message'}
                 *
                 * Thanks Obama!
                 */
                if (data.result.success !== undefined) {
                    // Actually, this is an error
                    error(data.result.success);
                } else {
                    // The happy path, no errors
                    window.location.reload(false)
                }
            }
        });

        updateChangeEvent(fileUpload);

        function error(msg) {
            let el = $(element).find('p#error_' + id);
            el.text(msg);
            if (msg)
                el.focus();
        }


        function updateChangeEvent(fileUploadObj) {
            fileUploadObj.off('change').on('change', function (e) {
                const that = $(this).data('blueimpFileupload'),
                    data = {
                        fileInput: $(e.target),
                        form: $(e.target.form)
                    };

                that._getFileInputFiles(data.fileInput).always(function (files) {
                    data.files = files;
                    if (that.options.replaceFileInput) {
                        that._replaceFileInput(data.fileInput);
                    }
                    that._onAdd(e, data);
                });
            });
        }

    }
    function checkResponse(response) {
        return response["zip_available"];
    }

    function pollUntilSuccess(url, checkSuccessFn, intervalMs, maxTries) {
        const deferred = $.Deferred();
        let tries = 1;

        function makeLoopingRequest() {
            $.get(url).success(function (response) {
                if (checkSuccessFn(response)) {
                    deferred.resolve(response);
                } else if (tries < maxTries) {
                    tries++;
                    setTimeout(makeLoopingRequest, intervalMs);
                } else {
                    deferred.reject('Max tries exceeded.');
                }
            }).fail(function (err) {
                deferred.reject('Request failed:\n' + err.responseText);
            });
        }

        makeLoopingRequest();

        return deferred.promise();
    }

    function loadjs(url) {
        $('<script>')
            .attr('type', 'text/javascript')
            .attr('src', url)
            .appendTo(element);
    }

    if (require === undefined) {
        /**
         * The LMS does not use require.js (although it loads it...) and
         * does not already load jquery.fileupload.  (It looks like it uses
         * jquery.ajaxfileupload instead.  But our XBlock uses
         * jquery.fileupload.
         */
        loadjs('/static/js/vendor/jQuery-File-Upload/js/jquery.iframe-transport.js');
        loadjs('/static/js/vendor/jQuery-File-Upload/js/jquery.fileupload.js');
        xblock($, _);
    } else {
        /**
         * Studio, on the other hand, uses require.js and already knows about
         * jquery.fileupload.
         */
        require(['jquery', 'underscore', 'jquery.fileupload'], xblock);
    }

}
