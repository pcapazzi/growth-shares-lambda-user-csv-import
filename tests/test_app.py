import json
import pytest

from braze_user_csv_import import app


def test_lambda_handler_fails_assert_event_logged(mocker, capsys):
    headers = ['header1', 'header2']
    offset = 7256
    event = {"Records": [
        {"s3": {"bucket": {"name": "test"}, "object": {"key": "test"}}}]}

    mock_processor = mocker.MagicMock(headers=headers, total_offset=offset,
                                      processed_users=999)
    # Set off fatal exception during file processing
    mock_processor.process_file.side_effect = app.FatalAPIError("Test error")
    mock_processor = mocker.patch('braze_user_csv_import.app.CsvProcessor',
                                  return_value=mock_processor)
    with pytest.raises(Exception):
        app.lambda_handler(event, None)

    # Confirm that event gets logged
    logs, _ = capsys.readouterr()
    new_event = json.dumps({**event, "offset": offset, "headers": headers})
    assert 'Encountered error "Test error"' in logs
    assert f"{new_event}" in logs


def test_successful_import_offset_progresses(mocker, users, csv_processor):
    mocker.patch('braze_user_csv_import.app._post_users', return_value=75)
    chunks = [users] * 5
    csv_processor.processing_offset = 100

    assert csv_processor.total_offset == 0
    csv_processor.post_users(chunks)
    assert csv_processor.total_offset == 100


def test_failed_import_offset_does_not_progress(mocker, users, csv_processor):
    mocker.patch('braze_user_csv_import.app._post_users',
                 side_effect=RuntimeError)
    chunks = [users] * 5
    csv_processor.processing_offset = 100

    assert csv_processor.total_offset == 0
    with pytest.raises(RuntimeError):
        csv_processor.post_users(chunks)
    assert csv_processor.total_offset == 0


def test__handle_braze_response_success(mocker, users):
    mocker.patch('json.loads', return_value={"message": "success"})
    res = mocker.Mock(status_code=201)

    error_users = app._handle_braze_response(res)
    assert error_users == 0


def test__handle_braze_response_some_processed(mocker, users):
    res = mocker.Mock(status_code=201)
    mocker.patch('json.loads', return_value={
                 "errors": [{"there were some errors with index 1"}]})

    error_users = app._handle_braze_response(res)
    assert error_users == 1


def test__handle_braze_response_server_error_max_retries_not_reached_raises_non_fatal_api_error(mocker, users):
    res = mocker.Mock(status_code=429)
    mocker.patch('json.loads', return_value={"errors": {"too many requests"}})
    mocker.patch('braze_user_csv_import.app._delay', return_value=None)

    with pytest.raises(app.APIRetryError):
        app._handle_braze_response(res)


def test__handle_braze_response_server_error_max_retries_raises_fatal_api_error(mocker, users):
    res = mocker.Mock(status_code=500)
    mocker.patch('json.loads', return_value={
        "errors": {"some server errors"}})
    mocker.patch('braze_user_csv_import.app.RETRIES', app.MAX_API_RETRIES)

    with pytest.raises(app.FatalAPIError):
        app._handle_braze_response(res)


def test__process_row_empty_string_should_ignore():
    row = {'external_id': 'user1', 'attribute1': '', 'attribute2': 'value'}
    processed_row = app._process_row(row)
    assert len(processed_row) == 2
    assert 'attribute1' not in processed_row


def test__process_row_null_string_should_convert_to_none():
    row = {'external_id': 'user1', 'attribute1': 'null'}
    processed_row = app._process_row(row)
    assert len(processed_row) == 2
    assert processed_row['attribute1'] == None


def test__process_list_string_should_deconstruct():
    row = {'external_id': 'user1', 'attribute1': "['value1', 'value2']"}
    processed_row = app._process_row(row)
    assert len(processed_row) == 2
    assert isinstance(processed_row['attribute1'], list)