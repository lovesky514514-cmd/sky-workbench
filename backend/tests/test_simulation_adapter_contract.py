"""Discovery can identify an engine, but cannot invent execution evidence."""

from pathlib import Path

from app.comsol.discovery import _inspect_root
from app.comsol.bridge_process import bridge_build_dir
from app import simulation_adapters


def test_versioned_comsol_discovery_is_explicitly_unverified(tmp_path):
    for version in ("6.0", "6.1", "6.2", "6.3", "6.4"):
        root = tmp_path / version / "Multiphysics"
        (root / "plugins").mkdir(parents=True)
        (root / "plugins" / "com.comsol.api_1.jar").write_bytes(b"fixture")
        java = root / "java" / "win64" / "jre" / "bin"
        java.mkdir(parents=True)
        (java / "java.exe").write_bytes(b"fixture")
        (java / "javac.exe").write_bytes(b"fixture")
        (root / "bin" / "win64").mkdir(parents=True)
        (root / "lib" / "win64").mkdir(parents=True)
        (root / "about.txt").write_text(f"COMSOL {version}\n", encoding="utf-8")
        environment = _inspect_root(root)
        assert environment is not None and environment.version == f"COMSOL {version}"
        if version == "6.3":
            assert "validate" in environment.compatibility
        else:
            assert "test" in environment.compatibility or "Unverified" in environment.compatibility


def test_external_engine_executable_never_enables_execution(monkeypatch):
    monkeypatch.setattr(simulation_adapters, "discover_comsol_java", lambda: type("E", (), {
        "found": False, "java": "", "compatibility": "unverified",
    })())
    monkeypatch.setattr(simulation_adapters.shutil, "which", lambda name: str(Path("C:/fake") / name))
    records = simulation_adapters.capabilities()
    assert {item["engine"] for item in records} == {"COMSOL", "MATLAB", "Wolfram", "Materials Studio"}
    for item in records[1:]:
        assert item["installed"] is True
        assert item["readback"] == "UNVERIFIED"
        assert item["execute"] == "UNAVAILABLE"


def test_java_bridge_cache_isolated_by_comsol_api_jar(tmp_path):
    from app.comsol.discovery import ComsolJavaEnvironment

    jar_a = tmp_path / "comsol62" / "api.jar"
    jar_b = tmp_path / "comsol64" / "api.jar"
    jar_a.parent.mkdir()
    jar_b.parent.mkdir()
    jar_a.write_bytes(b"api62")
    jar_b.write_bytes(b"api64")
    build_a = bridge_build_dir(ComsolJavaEnvironment(True, "ready", "", root=str(jar_a.parent), version="COMSOL 6.2", api_jar=str(jar_a)))
    build_b = bridge_build_dir(ComsolJavaEnvironment(True, "ready", "", root=str(jar_b.parent), version="COMSOL 6.4", api_jar=str(jar_b)))
    assert build_a != build_b
