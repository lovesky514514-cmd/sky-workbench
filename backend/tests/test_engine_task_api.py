from fastapi.testclient import TestClient

from app import main
from app.engines.manager import EngineTaskManager


def test_optional_engine_api_requires_session_auth_before_execution(tmp_path, monkeypatch):
    manager = EngineTaskManager(tmp_path, lambda engine: None)
    monkeypatch.setattr(main, "engine_tasks", manager)
    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", "engine-api-test")
    client = TestClient(main.app)
    payload = {"inputs": {"kind": "lammps_lj"}, "confirmed": True}
    assert client.post("/api/engine-tasks", json=payload).status_code == 401
    headers = {"X-Workbench-Token": "engine-api-test"}
    response = client.post("/api/engine-tasks", json=payload, headers=headers)
    assert response.status_code == 409 and "Configure" in response.json()["detail"]
    assert not manager.tasks


def test_preview_and_configuration_do_not_execute_an_engine(tmp_path, monkeypatch):
    manager = EngineTaskManager(tmp_path, lambda engine: None)
    monkeypatch.setattr(main, "engine_tasks", manager)
    monkeypatch.delenv("COMSOL_WORKBENCH_SESSION_TOKEN", raising=False)
    client = TestClient(main.app)
    result = client.post("/api/engine-tasks/preview", json={"inputs": {"kind": "lammps_lj"}})
    assert result.status_code == 200
    assert result.json()["assumptions"] and "in.lammps" in result.json()["files"]
    assert len(result.json()["structure"]["atoms"]) == 108
    assert result.json()["structure"]["units"] == "LJ reduced length"
    assert client.put("/api/engine-tasks/configuration", json={"engine": "LAMMPS", "executable": ""}).status_code == 403
    assert client.post("/api/engine-tasks", json={"inputs": {"kind": "lammps_lj"}, "confirmed": True}).status_code == 403
    assert not manager.tasks


def test_invalid_task_input_and_run_identity_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "engine_tasks", EngineTaskManager(tmp_path))
    monkeypatch.delenv("COMSOL_WORKBENCH_SESSION_TOKEN", raising=False)
    client = TestClient(main.app)
    assert client.post("/api/engine-tasks/preview", json={"inputs": {"kind": "lammps_lj", "steps": 0}}).status_code == 422
    assert client.get("/api/engine-tasks/not-a-run").status_code == 403
    assert client.post("/api/simulation-adapters/wolfram/probe").status_code == 403
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN','fixture')
    assert client.get('/api/engine-tasks/not-a-run',headers={'X-Workbench-Token':'fixture'}).status_code==404
    assert client.post('/api/simulation-adapters/wolfram/probe',headers={'X-Workbench-Token':'fixture'}).status_code==404
