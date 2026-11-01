import asyncio
import os
import sys

import pytest
from pydantic import ValidationError

from app.engines.inputs import command_for, prepare_inputs
from app.engines.manager import EngineTaskManager
from app.engines.models import EngineTaskRequest, LammpsInput, MaterialsStudioInput, VaspInput
from app.engines.results import lammps_result, poscar_structure, vasp_result
from app.comsol.owned_process import OwnedProcess


DUMP = """ITEM: TIMESTEP
100
ITEM: NUMBER OF ATOMS
1
ITEM: BOX BOUNDS pp pp pp
0 2
0 2
0 2
ITEM: ATOMS id type x y z
1 1 0.5 1.0 1.5
"""
LOG = "LAMMPS fixture\nStep Temp PotEng TotEng Press Atoms\n0 1 -5 -3.5 0.1 1\n100 1.1 -4.9 -3.4 0.2 1\n"
POSCAR = "Si\n1\n2 0 0\n0 2 0\n0 0 2\nSi\n1\nDirect\n0.25 0.5 0.75\n"


def test_inputs_are_typed_and_no_user_script_enters_generated_commands():
    with pytest.raises(ValidationError):
        LammpsInput(temperature=float("nan"))
    with pytest.raises(ValidationError):
        LammpsInput(steps=100000000)
    with pytest.raises(ValidationError):
        LammpsInput(command="shell dangerous")
    with pytest.raises(ValidationError):
        MaterialsStudioInput(atoms=[{"element": 'H");system("bad', "x": 0, "y": 0, "z": 0}])
    inputs = prepare_inputs(LammpsInput(steps=100))
    assert "units lj" in inputs["in.lammps"]
    assert command_for("LAMMPS", "C:/engine path/lmp.exe")[0] == "C:/engine path/lmp.exe"
    with pytest.raises(ValueError):
        command_for("Materials Studio", "C:/bad&path/RunMatScript.bat")


def test_actual_result_formats_distinguish_data_from_scientific_acceptance(tmp_path):
    (tmp_path / "trajectory.dump").write_text(DUMP)
    (tmp_path / "log.lammps").write_text(LOG)
    result = lammps_result(tmp_path, 100)
    assert result["frames"][0]["atoms"][0]["position"] == [0.5, 1, 1.5]
    assert result["scientific_validation"] == "NEEDS_REVIEW"
    assert all(result["checks"].values())  # Readback checks are independent of scientific validation.
    assert lammps_result(tmp_path, 200)["scientific_validation"] == "NEEDS_REVIEW"
    (tmp_path / "log.lammps").write_text(LOG.replace("1.1", "nan"))
    with pytest.raises(ValueError):
        lammps_result(tmp_path, 100)
    assert poscar_structure(POSCAR)["atoms"][0]["position"] == [0.5, 1, 1.5]
    (tmp_path / "OSZICAR").write_text("1 F= -10.2 E0= -10.2\n")
    (tmp_path / "OUTCAR").write_text("General timing and accounting\n")
    vasp = vasp_result(tmp_path)
    assert vasp["checks"]["normal_termination"] is True
    assert vasp["scientific_validation"] == "NEEDS_REVIEW"


def test_owned_lifecycle_persists_artifacts_and_rejects_changed_outputs(tmp_path, monkeypatch):
    # This Python producer tests ownership and persistence, never a real solver claim.
    producer = f"from pathlib import Path; Path('log.lammps').write_text({LOG!r}); Path('trajectory.dump').write_text({DUMP!r})"
    monkeypatch.setattr("app.engines.manager.command_for", lambda *args: [sys.executable, "-c", producer])
    manager = EngineTaskManager(tmp_path, lambda engine: sys.executable, execution_guard=lambda *args: None)
    request = EngineTaskRequest(inputs=LammpsInput(steps=100), confirmed=True)

    async def exercise():
        started = await manager.start(request)
        await manager.tasks[started["run_id"]]
        record = manager.status(started["run_id"])
        assert record["state"] == "COLLECTED"
        assert record["cleanup"]["reaped"]
        assert manager.artifact(record["run_id"], "log.lammps").decode().replace("\r\n", "\n") == LOG
        assert EngineTaskManager(tmp_path).list_runs("scratch")[0]["run_id"] == record["run_id"]
        (tmp_path / record["run_id"] / "log.lammps").write_text(LOG + "changed")
        with pytest.raises(ValueError, match="changed"):
            manager.artifact(record["run_id"], "log.lammps")

    asyncio.run(exercise())


def test_cancel_and_restart_do_not_fabricate_success(tmp_path, monkeypatch):
    monkeypatch.setattr("app.engines.manager.command_for", lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"])
    manager = EngineTaskManager(tmp_path, lambda engine: sys.executable, execution_guard=lambda *args: None)

    async def exercise():
        request = EngineTaskRequest(inputs=LammpsInput(steps=100), confirmed=True)
        started = await manager.start(request)
        record = await manager.cancel(started["run_id"])
        assert record["state"] == "CANCELED" and record["command_success"] is False
        started = await manager.start(request)
        for _ in range(100):
            if manager.status(started["run_id"])["state"] == "RUNNING":
                break
            await asyncio.sleep(0.01)
        assert (await manager.cancel(started["run_id"]))["state"] == "CANCELED"
        record = manager.status(started["run_id"])
        assert record["cleanup"]["reaped"]
        record["state"] = "RUNNING"
        manager._write(record)
        recovered = EngineTaskManager(tmp_path).list_runs("scratch")[0]
        assert recovered["state"] == "INTERRUPTED"
        assert recovered["command_success"] is False

    asyncio.run(exercise())


def test_unconfirmed_tasks_and_bad_structures_never_launch(tmp_path):
    manager = EngineTaskManager(tmp_path, lambda engine: sys.executable, execution_guard=lambda *args: None)
    with pytest.raises(ValueError, match="authorize"):
        asyncio.run(manager.start(EngineTaskRequest(inputs=LammpsInput())))
    files = {"INCAR": "ENCUT=400", "POSCAR": "invalid", "KPOINTS": "Gamma", "POTCAR": "local test fixture"}
    with pytest.raises(ValueError):
        manager.preview(EngineTaskRequest(inputs=VaspInput(files=files)))
    assert not manager.tasks


@pytest.mark.skipif(os.name != "nt", reason="Windows vendor batch launcher contract")
def test_materialsscript_launcher_preserves_spaces_and_parentheses(tmp_path):
    vendor = tmp_path / "Vendor folder (2026)"
    vendor.mkdir()
    launcher = vendor / "RunMatScript.bat"
    launcher.write_text("@echo off\necho %~1> launcher-argument.txt\nexit /b 0\n")

    async def exercise():
        owned = await OwnedProcess.spawn(command_for("Materials Studio", str(launcher)), cwd=str(tmp_path))
        try:
            await asyncio.wait_for(owned.process.communicate(), timeout=10)
            assert owned.process.returncode == 0
            assert (tmp_path / "launcher-argument.txt").read_text().strip() == "main"
        finally:
            await owned.terminate()

    asyncio.run(exercise())
