"""Missing installations and unavailable modules must never create a successful run."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main
from app.engines.manager import EngineTaskManager
from app.script_workspace import ScriptRunManager


@pytest.mark.parametrize(
    "engine,inputs",
    [
        ("LAMMPS", {"kind": "lammps_lj"}),
        (
            "Materials Studio",
            {
                "kind": "ms_forcite_energy",
                "atoms": [{"element": "H", "x": 0, "y": 0, "z": 0}],
            },
        ),
        (
            "VASP",
            {
                "kind": "vasp_files",
                "files": {
                    "INCAR": "NSW=0\n",
                    "POSCAR": "Input-schema fixture\n1\n1 0 0\n0 1 0\n0 0 1\nSi\n1\nDirect\n0 0 0\n",
                    "KPOINTS": "fixture\n0\nGamma\n1 1 1\n",
                    "POTCAR": "TITEL = SKY_TEST_FIXTURE Si\n",
                },
            },
        ),
    ],
)
def test_missing_native_engine_rejected_by_api_without_launch(
    tmp_path, monkeypatch, engine, inputs
):
    # The POTCAR above is a schema marker only. No fixture can reach an engine.
    manager = EngineTaskManager(tmp_path / "runs", lambda name: None)
    monkeypatch.setattr(main, "engine_tasks", manager)
    monkeypatch.setattr(
        main.project_service, "projects", lambda: [{"id": "isolated", "active": True}]
    )
    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", "missing-engine-fixture")
    with TestClient(
        main.app, headers={"X-Workbench-Token": "missing-engine-fixture"}
    ) as api:
        response = api.post(
            "/api/engine-tasks", json={"inputs": inputs, "confirmed": True}
        )
        assert response.status_code == 409 and "installed" in response.json()["detail"]
        handshake = api.post(
            "/api/engine-tasks/connect", json={"engine": engine, "executable": ""}
        )
        assert handshake.status_code == 409
        assert api.get("/api/engine-tasks").json()["runs"] == []
        assert not manager.tasks and not manager.processes and not manager.active_runs


@pytest.mark.parametrize("engine", ["MATLAB", "Mathics3"])
def test_missing_script_engine_creates_no_run_or_completion(
    tmp_path, monkeypatch, engine
):
    manager = ScriptRunManager(tmp_path, lambda name: None)
    with pytest.raises(FileNotFoundError, match="unavailable"):
        asyncio.run(manager.start(engine, "1+1"))
    monkeypatch.setattr(main, "script_runs", manager)
    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", "missing-engine-fixture")
    with TestClient(
        main.app, headers={"X-Workbench-Token": "missing-engine-fixture"}
    ) as api:
        response = api.post(
            "/api/script-runs",
            json={"engine": engine, "source": "1+1", "confirmed": True},
        )
        assert response.status_code == 409
    assert not manager.runs


@pytest.mark.parametrize("units", ["metal", "real"])
def test_water_or_copper_units_cannot_fall_back_to_lj(tmp_path, monkeypatch, units):
    manager = EngineTaskManager(tmp_path, lambda name: None)
    monkeypatch.setattr(main, "engine_tasks", manager)
    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", "missing-engine-fixture")
    with TestClient(
        main.app, headers={"X-Workbench-Token": "missing-engine-fixture"}
    ) as api:
        response = api.post(
            "/api/engine-tasks/preview",
            json={"inputs": {"kind": "lammps_lj", "unit_system": units}},
        )
        assert response.status_code == 422 and not manager.tasks


def test_ms_optimization_cannot_be_labeled_as_supported_single_point(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", "missing-engine-fixture")
    with TestClient(
        main.app, headers={"X-Workbench-Token": "missing-engine-fixture"}
    ) as api:
        response = api.post(
            "/api/engine-tasks/preview",
            json={
                "inputs": {
                    "kind": "ms_forcite_optimization",
                    "atoms": [{"element": "O", "x": 0, "y": 0, "z": 0}],
                }
            },
        )
        assert response.status_code == 422
