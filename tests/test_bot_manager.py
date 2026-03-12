import pytest
import queue
from unittest.mock import MagicMock
from bot.manager import BotManager

@pytest.fixture
def mock_bot_manager(mocker):
    # Mock threading locking mechanisms inside the class
    mocker.patch("threading.Lock")
    mocker.patch("threading.Thread")
    mocker.patch("bot.dispatcher.ScoutDispatcher.wait_for_dates", return_value=True)

    log_queue = queue.Queue()
    bot_manager = BotManager(log_queue=log_queue)
    return bot_manager

def test_bot_manager_start_single(mock_bot_manager, mocker):
    mock_repo = mocker.patch("data.repositories.UserRepository.get_by_id")
    mock_repo.return_value = {
        "id": 1, 
        "first_name": "Test User", 
        "vfs_email": "test@test.com", 
        "is_active": True,
        "is_scout": False,
        "max_concurrent_logins": 1
    }
    
    # Try starting a user that doesn't exist in threads
    mock_bot_manager.start_single(1)
    
    assert 1 in mock_bot_manager.threads
    assert mock_bot_manager.active_worker_count == 1
    assert mock_bot_manager.threads[1].running is True

def test_bot_manager_stop_all(mock_bot_manager):
    # Add fake workers to the threads dictionary
    mock_worker1 = MagicMock()
    mock_worker2 = MagicMock()
    
    mock_bot_manager.threads = {
        1: mock_worker1,
        2: mock_worker2
    }
    
    mock_bot_manager.stop_all()
    
    assert mock_worker1.stop.called
    assert mock_worker2.stop.called
    
    assert len(mock_bot_manager.threads) == 0
