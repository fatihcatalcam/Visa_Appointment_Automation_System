import pytest
from fastapi.testclient import TestClient
from api.main import app

# We override the bot_manager dependency in API routes to prevent needing a real one
# and avoid spinning up threads in tests.
from unittest.mock import MagicMock
from api.routers.workers import get_bot_manager

@pytest.fixture
def mock_bot_manager():
    manager = MagicMock()
    manager.active_worker_count = 5
    manager._max_workers = 15
    manager.threads = {1: MagicMock(), 2: MagicMock()}
    return manager

@pytest.fixture
def client(mock_bot_manager):
    app.dependency_overrides[get_bot_manager] = lambda: mock_bot_manager
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_api_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_system_telemetry(client, mocker, mock_bot_manager):
    mocker.patch("data.repositories.GlobalSettingsRepository.get_all", return_value={"test": "val"})
    client.app.state.bot_manager = mock_bot_manager
    response = client.get("/api/v1/system/telemetry")
    assert response.status_code == 200
    data = response.json()
    assert "timestamp" in data
    assert "active_workers" in data
    assert "bot_ram_mb" in data
    assert data["active_workers"] == 5

def test_get_all_workers_endpoint(client, mocker):
    user_repo_mock = mocker.patch("data.repositories.UserRepository.get_all")
    user_repo_mock.return_value = [
        {"id": 1, "first_name": "John", "is_active": 1, "status": "Running", "worker_state": "Browsing"},
        {"id": 2, "first_name": "Jane", "is_active": 0, "status": "Idle", "worker_state": "None"}
    ]
    
    from api.auth import verify_api_key
    app.dependency_overrides[verify_api_key] = lambda: "admin_test"
    
    response = client.get("/api/v1/workers", headers={"X-API-Key": "admin_test"})
    assert response.status_code == 200
    assert len(response.json()["workers"]) == 2
    
    app.dependency_overrides.pop(verify_api_key, None)
    
def test_unauthorized_access(client):
    response = client.get("/api/v1/workers", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401
    assert "detail" in response.json()
