import asyncio
from pathlib import Path

import pytest

from app.engines.capabilities import CapabilityRegistry
from app.engines.manager import EngineTaskManager
from app.engines.native_installations import materials_server
from app.script_workspace import child_environment


def test_matlab_vendor_license_allowlist_is_limited_to_matlab(monkeypatch):
    for name in ("MLM_LICENSE_FILE", "LM_LICENSE_FILE", "CUSTOM_LICENSE_SECRET", "COMSOL_WORKBENCH_SESSION_TOKEN"):
        monkeypatch.setenv(name, "synthetic-sensitive-value")
    for engine in (None, "Python", "Mathics3", "Materials Studio"):
        child = child_environment(engine)
        assert "MLM_LICENSE_FILE" not in child and "LM_LICENSE_FILE" not in child
    child = child_environment("MATLAB")
    assert child["MLM_LICENSE_FILE"] == child["LM_LICENSE_FILE"] == "synthetic-sensitive-value"
    assert "CUSTOM_LICENSE_SECRET" not in child and "COMSOL_WORKBENCH_SESSION_TOKEN" not in child


def test_registered_custom_ms_layout_is_discovered_without_running_it(tmp_path, monkeypatch):
    from app import simulation_adapters as adapters
    launcher = tmp_path / "Materials Studio 99.2 x64 Server" / "etc" / "Scripting" / "bin" / "RunMatScript.bat"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("fixture")
    monkeypatch.delenv("SKY_MS_EXECUTABLE", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setattr(adapters.shutil, "which", lambda _: None)
    monkeypatch.setattr(adapters, "registered_install_roots", lambda _: [tmp_path])
    assert adapters._find_executable("Materials Studio", ()) == str(launcher)


def test_ms_connection_is_invalidated_when_server_changes(tmp_path):
    root = tmp_path / "Vendor"
    launcher = root / "etc" / "Scripting" / "bin" / "RunMatScript.bat"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"fixed launcher")
    server = root / "bin" / "MatServer.exe"
    server.parent.mkdir()
    server.write_bytes(b"version one")
    assert materials_server(str(launcher)) == server
    registry = CapabilityRegistry(tmp_path / "connections")
    registry.connected("Materials Studio", str(launcher), "99.2")
    registry.require("Materials Studio", "ms_forcite_energy", str(launcher))
    server.write_bytes(b"version two")
    with pytest.raises(ValueError, match="test the installed connector"):
        registry.require("Materials Studio", "ms_forcite_energy", str(launcher))


@pytest.mark.parametrize("receipt", ["valid", "missing", "oversized"])
def test_ms_redirected_probe_requires_bounded_owned_receipt(tmp_path, monkeypatch, receipt):
    import app.engines.manager as module
    class FakeTask:
        def __init__(self, argv, *, cwd, **kwargs):
            self.folder = Path(cwd)
        async def start(self):
            (self.folder / "main.pl.out").write_bytes(b"vendor prefix\nSKY_MATERIALSSCRIPT_CONNECTED\n")
            if receipt != "missing":
                (self.folder / "sky_probe.txt").write_bytes(
                    b"SKY_MATERIALSSCRIPT_CONNECTED\n" if receipt == "valid" else b"x" * 129)
        async def consume(self, timeout, callback, **kwargs):
            callback(b"locale warning only\n")
            return 0
        async def close(self):
            return {"reaped": True}
    launcher = tmp_path / "RunMatScript.bat"
    launcher.write_bytes(b"fixture")
    monkeypatch.setattr(module, "OwnedTask", FakeTask)
    monkeypatch.setattr(module, "file_product_version", lambda _: "99.2.1.1")
    manager = EngineTaskManager(tmp_path / "runs", lambda _: str(launcher))
    result = asyncio.run(manager.connect("Materials Studio"))
    assert result["status"] == ("HANDSHAKE_PASSED" if receipt == "valid" else "HANDSHAKE_FAILED")
    level = manager.capabilities.capability_of("Materials Studio", "ms_forcite_energy", str(launcher))["level"]
    assert level == ("INPUT_SUPPORTED" if receipt == "valid" else "DETECTED")
