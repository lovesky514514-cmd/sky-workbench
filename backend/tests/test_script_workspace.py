"""Script output is local scratch work, never a scientific verdict."""

import asyncio
import subprocess
import sys

import pytest

from app.script_workspace import ScriptRun, ScriptRunManager, child_environment, command_for, image_kind, mathics_worker_path


def test_command_contract_never_embeds_user_code_or_uses_cloud():
    assert command_for("Python", "python", "main.py") == ["python", "-I", "main.py"]
    assert command_for("Wolfram", "wolframscript", "main.wl") == ["wolframscript", "-file", "main.wl", "-local"]
    assert command_for("MATLAB", "matlab", "main.m") == ["matlab", "-batch", "run('main.m')"]
    assert command_for("Mathics3", "python", "main.wl") == ["python", "-I", str(mathics_worker_path()), "main.wl"]
    with pytest.raises(ValueError):
        command_for("Materials Studio", "studio", "main.pl")


def test_mathics_has_no_wolfram_fallback(monkeypatch, tmp_path):
    from app.script_workspace import executable_for

    monkeypatch.setenv("SKY_MATHICS_PYTHON", str(tmp_path / "missing-python.exe"))
    assert executable_for("Mathics3") is None
    assert ScriptRunManager(tmp_path, resolver=lambda engine: None).availability()["Mathics3"] is False


def test_one_unreadable_vendor_probe_does_not_hide_other_engines(tmp_path):
    def resolve(engine):
        if engine == "MATLAB":
            raise PermissionError("synthetic inaccessible installation")
        return "independent-python" if engine == "Mathics3" else None

    available = ScriptRunManager(tmp_path, resolver=resolve).availability()
    assert available["MATLAB"] is False
    assert available["Mathics3"] is True


def test_mathics_result_is_never_a_scientific_verdict(tmp_path):
    import tempfile

    workspace = tempfile.TemporaryDirectory(dir=tmp_path)
    try:
        run = ScriptRun("synthetic-id", "Mathics3", workspace, state="COMPLETED",
                        mathics_result={"status": "success", "text": "4", "latex": "4", "mathml": "<math><mn>4</mn></math>",
                                        "messages": [], "verification": "not_requested"})
        assert run.public()["model_state_verified"] is False
        assert run.public()["scientific_validation_passed"] is False
    finally:
        workspace.cleanup()


def test_mathics_worker_rejects_nonfixed_filename(tmp_path):
    (tmp_path / "main.wl").write_text("2+2", encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", str(mathics_worker_path()), "another.wl"],
                            cwd=tmp_path, capture_output=True, timeout=15, check=False)
    assert result.returncode == 2
    assert not (tmp_path / "mathics_result.json").exists()


def test_image_kind_rejects_spoofed_images():
    assert image_kind("plot.png", b"\x89PNG\r\n\x1a\n" + b"x" * 8) == "image/png"
    assert image_kind("plot.jpg", b"\xff\xd8\xff" + b"x" * 8) == "image/jpeg"
    assert image_kind("plot.webp", b"RIFFxxxxWEBP" + b"x" * 4) == "image/webp"
    assert image_kind("plot.png", b"<script>alert(1)</script>") is None
    assert image_kind("plot.svg", b"<svg/>") is None


def test_child_environment_does_not_inherit_desktop_or_api_credentials(monkeypatch):
    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", "synthetic-token")
    monkeypatch.setenv("SYNTHETIC_PROVIDER_API_KEY", "synthetic-key")
    monkeypatch.setenv("PATH", "synthetic-path")
    child = child_environment()
    assert "COMSOL_WORKBENCH_SESSION_TOKEN" not in child
    assert "SYNTHETIC_PROVIDER_API_KEY" not in child
    assert child["PATH"] == "synthetic-path"


def test_python_run_exports_image_without_scientific_certification(tmp_path):
    async def scenario():
        manager = ScriptRunManager(tmp_path, resolver=lambda engine: sys.executable if engine == "Python" else None)
        run = await manager.start("Python", "print('hello')\nopen('figure.png','wb').write(b'\\x89PNG\\r\\n\\x1a\\n' + b'x'*8)")
        await asyncio.wait_for(manager.wait(run["run_id"]), 15)
        result = manager.status(run["run_id"])
        assert result["state"] == "COMPLETED"
        assert "hello" in result["output"]
        assert result["figures"][0]["name"] == "figure.png"
        assert result["scientific_validation_passed"] is False
        assert result["model_state_verified"] is False
        assert manager.figure(run["run_id"], "figure.png")[0] == "image/png"
        (tmp_path / next(tmp_path.iterdir()).name / "figure.png").write_bytes(b"modified")
        with pytest.raises(FileNotFoundError):
            manager.figure(run["run_id"], "figure.png")
        await manager.shutdown()

    asyncio.run(scenario())


def test_script_routes_require_the_existing_desktop_token(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", "synthetic-session-token")
    monkeypatch.setattr(main, "script_runs", ScriptRunManager(tmp_path, resolver=lambda engine: None))
    client = TestClient(main.app)
    assert client.get("/api/script-runs/availability").status_code == 401
    authorized = {"X-Workbench-Token": "synthetic-session-token"}
    assert client.get("/api/script-runs/availability", headers=authorized).status_code == 200
    assert client.post("/api/script-runs", headers=authorized,
                       json={"engine": "Python", "source": "print(1)"}).status_code == 422
    assert client.post("/api/script-runs", headers=authorized,
                       json={"engine": "Materials Studio", "source": "print 1", "confirmed": True}).status_code == 422


def test_script_execution_is_disabled_without_desktop_authentication(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.delenv("COMSOL_WORKBENCH_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(main, "script_runs", ScriptRunManager(tmp_path, resolver=lambda engine: sys.executable))
    response = TestClient(main.app).post("/api/script-runs", json={
        "engine": "Python", "source": "print(1)", "confirmed": True,
    })
    assert response.status_code == 403
    assert not list(tmp_path.iterdir())


def test_cancel_terminates_only_owned_child(tmp_path):
    async def scenario():
        manager = ScriptRunManager(tmp_path, resolver=lambda engine: sys.executable if engine == "Python" else None)
        run = await manager.start("Python", "import time\ntime.sleep(60)")
        assert (await asyncio.wait_for(manager.cancel(run["run_id"]), 15))["state"] == "CANCELED"
        assert manager.status(run["run_id"])["state"] == "CANCELED"
        await manager.shutdown()

    asyncio.run(scenario())


def test_output_limit_stops_the_owned_process_without_a_false_pass(tmp_path):
    async def scenario():
        manager = ScriptRunManager(tmp_path, resolver=lambda engine: sys.executable)
        run = await manager.start("Python", "print('x' * 70000)")
        await asyncio.wait_for(manager.wait(run["run_id"]), 15)
        result = manager.status(run["run_id"])
        assert result["state"] == "OUTPUT_LIMIT"
        assert result["script_exit_success"] is False
        assert len(result["output"]) <= 65_536
        await manager.shutdown()

    asyncio.run(scenario())


def test_license_failure_does_not_display_vendor_private_details(tmp_path):
    async def scenario():
        manager = ScriptRunManager(tmp_path, resolver=lambda engine: sys.executable)
        run = await manager.start("Python", "print('License activation required for private-user@example.test')\nraise SystemExit(1)")
        await asyncio.wait_for(manager.wait(run["run_id"]), 15)
        result = manager.status(run["run_id"])
        assert result["state"] == "FAILED"
        assert result["output"] == "The local engine requires a valid license or activation."
        await manager.shutdown()

    asyncio.run(scenario())


def test_invalid_source_and_unavailable_engine_do_not_launch(tmp_path):
    async def scenario():
        manager = ScriptRunManager(tmp_path, resolver=lambda engine: None)
        with pytest.raises(ValueError):
            await manager.start("Python", " ")
        with pytest.raises(ValueError):
            await manager.start("Materials Studio", "print 1")
        with pytest.raises(FileNotFoundError):
            await manager.start("MATLAB", "disp(1)")

    asyncio.run(scenario())


def test_parallel_start_requests_admit_only_one_owned_run(tmp_path):
    async def scenario():
        manager = ScriptRunManager(tmp_path, resolver=lambda engine: sys.executable)
        first, second = await asyncio.gather(
            manager.start("Python", "import time\ntime.sleep(30)"),
            manager.start("Python", "print('second')"), return_exceptions=True,
        )
        assert isinstance(first, dict)
        assert isinstance(second, RuntimeError)
        await manager.cancel(first["run_id"])
        await manager.shutdown()

    asyncio.run(scenario())
