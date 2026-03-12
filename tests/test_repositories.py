import pytest
from unittest.mock import MagicMock
from data.repositories import GlobalSettingsRepository, UserRepository

def test_global_settings_get(mocker):
    # Mocking the fetch_one db response
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mocker.patch("data.database.db.get_connection", return_value=mock_conn)
    
    # Simulate DB returning a setting
    mock_cursor.fetchone.return_value = ["test_value"]
    val = GlobalSettingsRepository.get("my_key", default="fallback")
    assert val == "test_value"
    
    # Simulate DB returning nothing (fallback should be used)
    mock_cursor.fetchone.return_value = None
    val2 = GlobalSettingsRepository.get("missing_key", default="fallback")
    assert val2 == "fallback"

def test_user_repository_update_status(mocker):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mocker.patch("data.database.db.get_connection", return_value=mock_conn)
    
    UserRepository.update_status(user_id=1, status="Running", error_msg=None, last_check="12:00")
    
    assert mock_cursor.execute.called
    args, kwargs = mock_cursor.execute.call_args
    assert "UPDATE users SET" in args[0]
    expected_params = ("Running", "12:00", 1)  # error_msg is None, so it's stripped from updates!
    assert args[1] == expected_params
