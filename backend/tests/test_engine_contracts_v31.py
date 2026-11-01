import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from app.engines.capabilities import CapabilityRegistry
from app.engines.manager import EngineTaskManager
from app.engines.models import EngineTaskRequest, VaspInput
from app.engines.results import poscar_structure, vasp_result
from app.execution.files import bounded_read


POSCAR = "Synthetic Si fixture\n1\n1 0 0\n0 1 0\n0 0 1\nSi\n1\nDirect\n0 0 0\n"


def files():
    # These markers are input-schema fixtures, not a usable or redistributed POTCAR.
    return {"POSCAR": POSCAR, "INCAR": "ENCUT=400; EDIFF=1E-6; NSW=0\r\n",
            "KPOINTS": "fixture\n0\nGamma\n1 1 1\n", "POTCAR": "TITEL = SKY_TEST_FIXTURE Si\n"}


@pytest.mark.parametrize("tag", ["ENCUT=-1", "NSW=-2", "NELM=1.5", "EDIFF=nan"])
def test_vasp_input_rejects_invalid_numeric_controls(tag):
    payload = files(); payload["INCAR"] = tag
    with pytest.raises(ValueError):
        VaspInput(files=payload)


def test_vasp_species_and_mesh_are_checked_without_exposing_potcar():
    payload = files(); payload["POTCAR"] = "TITEL = SKY_TEST_FIXTURE O\n"
    with pytest.raises(ValueError, match="element order"):
        VaspInput(files=payload)
    payload = files(); payload["KPOINTS"] = "fixture\n0\nGamma\n0 1 1\n"
    with pytest.raises(ValueError, match="KPOINTS"):
        VaspInput(files=payload)


def test_vasp4_requires_explicit_species_for_preview():
    vasp4 = POSCAR.replace("Si\n1\nDirect", "1\nDirect")
    with pytest.raises(ValueError, match="VASP4"):
        poscar_structure(vasp4)
    assert poscar_structure(vasp4, ["Si"])["atoms"][0]["element"] == "Si"


def test_single_point_and_ionic_scf_evidence_are_separate(tmp_path):
    (tmp_path / "INCAR").write_text("NSW=0; EDIFF=1E-6")
    (tmp_path / "OSZICAR").write_text("1 F= -1.5 E0= -1.5\n")
    marker = "aborting loop because EDIFF is reached\n"
    (tmp_path / "OUTCAR").write_text(marker + "General timing and accounting\n")
    result = vasp_result(tmp_path)
    assert result["files_collected"] and result["scf_converged"]
    assert result["ionic_convergence_applicable"] is False and result["ionic_converged"] is None
    (tmp_path / "INCAR").write_text("NSW=10; IBRION=2; EDIFF=1E-6")
    (tmp_path / "OSZICAR").write_text("1 F= -1.5\n2 F= -1.6\n")
    result = vasp_result(tmp_path)
    assert result["ionic_convergence_applicable"] and not result["scf_converged"]
    assert not result["ionic_converged"] and result["scientific_validation"] == "NEEDS_REVIEW"


def test_executable_detection_cannot_bypass_handshake(tmp_path):
    manager = EngineTaskManager(tmp_path, lambda engine: sys.executable)
    with pytest.raises(ValueError, match="test the installed connector"):
        asyncio.run(manager.start(EngineTaskRequest(inputs={"kind": "lammps_lj"}, confirmed=True)))
    assert not manager.tasks


def test_matlab_script_entry_cannot_bypass_handshake(tmp_path):
    from app.script_workspace import ScriptRunManager

    manager = ScriptRunManager(tmp_path / "scripts", lambda engine: sys.executable)
    with pytest.raises(RuntimeError, match="Test the installed MATLAB connector"):
        asyncio.run(manager.start("MATLAB", "disp(1)"))
    assert not manager.runs


def test_matlab_changed_during_probe_is_not_connected(tmp_path, monkeypatch):
    from app import simulation_adapters as adapters

    executable = tmp_path / "matlab.exe"
    executable.write_bytes(b"before handshake")
    registry = CapabilityRegistry(tmp_path / "connections")
    monkeypatch.setattr(adapters, "capabilities", lambda: [{
        "engine": "MATLAB", "installed": True, "executable": str(executable),
        "probe": "AVAILABLE_ON_REQUEST",
    }])

    async def changed_binary(command):
        executable.write_bytes(b"after handshake")
        return "HANDSHAKE_PASSED", 1

    monkeypatch.setattr(adapters, "_handshake", changed_binary)
    result = asyncio.run(adapters.probe_engine("matlab", registry=registry))
    assert result["status"] == "EXECUTABLE_CHANGED"
    assert registry.capability_of("MATLAB", "script", str(executable))["level"] == "DETECTED"


def test_connection_is_invalidated_by_executable_change_or_wrong_module(tmp_path):
    exe = tmp_path / "fixture.exe"; exe.write_bytes(b"version one")
    registry = CapabilityRegistry(tmp_path / "records")
    registry.connected("LAMMPS", str(exe), "fixture")
    registry.require("LAMMPS", "lammps_lj", str(exe))
    with pytest.raises(ValueError):
        registry.require("LAMMPS", "unsupported_module", str(exe))
    exe.write_bytes(b"version two")
    with pytest.raises(ValueError):
        registry.require("LAMMPS", "lammps_lj", str(exe))


def test_record_contains_file_identities_instead_of_input_bodies(tmp_path, monkeypatch):
    from app.engines import manager as module
    monkeypatch.setattr(module, "command_for", lambda *args: [sys.executable, "-c", "print('fixture')"])
    payload = files(); payload["INCAR"] += "# synthetic comment\r\n" * 10000
    manager = EngineTaskManager(tmp_path, lambda engine: sys.executable, execution_guard=lambda *args: None)
    async def exercise():
        record = await manager.start(EngineTaskRequest(inputs={"kind": "vasp_files", "files": payload}, confirmed=True))
        task = manager.tasks[record["run_id"]]
        await task
        await asyncio.sleep(0)
        folder = tmp_path / record["run_id"]
        persisted = json.loads((folder / "record.json").read_bytes())
        assert (folder / "record.json").stat().st_size < 10000
        assert "files" not in persisted["request"]["inputs"]
        assert not manager.tasks and not manager.active_runs
        artifact = next(item for item in persisted["artifacts"] if item["name"] == "INCAR")
        assert persisted["input_hashes"]["INCAR"] == artifact["sha256"]
        assert artifact["sha256"] == hashlib.sha256(payload["INCAR"].encode()).hexdigest()
        assert "POTCAR" not in {item["name"] for item in persisted["artifacts"]}
        assert persisted["model_state_verified"] is False
        assert persisted["code_revision"]["kind"] == "source_snapshot"
    asyncio.run(exercise())


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction")
def test_run_directory_junction_is_rejected(tmp_path):
    root = tmp_path / "runs"; root.mkdir()
    target = tmp_path / "outside"; target.mkdir()
    run_id = str(uuid.uuid4())
    subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(root / run_id), str(target)],
                   check=True, capture_output=True)
    with pytest.raises(ValueError, match="reparse"):
        EngineTaskManager(root).status(run_id)
    with pytest.raises(ValueError, match="reparse"):
        bounded_read(root / run_id, "record.json", 1024)
