from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.app_paths import AppPaths
from app.knowledge.integrity import MANIFEST_NAME, composite_sha256, verify_knowledge_base
from app.main import app


def test_app_paths_packaged_mode_separates_resources_and_user_data(tmp_path: Path) -> None:
    install = tmp_path / "Installed App"
    bundle = tmp_path / "Bundle Resources"
    local = tmp_path / "Local App Data"
    paths = AppPaths.discover(
        {"LOCALAPPDATA": str(local), "COMSOL_WORKBENCH_DESKTOP_MODE": "1"},
        frozen=True,
        executable=install / "scientific-runtime.exe",
        bundle_root=bundle,
    )
    assert paths.install_dir == install.resolve()
    assert paths.resource_dir == bundle.resolve()
    assert paths.app_data_dir == (local / "COMSOLScientificWorkbench").resolve()
    assert paths.workspace_dir == paths.app_data_dir / "workspace"
    assert paths.kb_dir == bundle.resolve() / "resources" / "knowledge_base"
    assert paths.models_dir.is_relative_to(paths.workspace_dir)
    assert not paths.models_dir.is_relative_to(paths.install_dir)


def test_app_paths_environment_override_supports_non_c_drive_workspace(tmp_path: Path) -> None:
    selected = tmp_path / "User Selected Workspace"
    paths = AppPaths.discover(
        {
            "COMSOL_WORKBENCH_DESKTOP_MODE": "1",
            "COMSOL_WORKBENCH_WORKSPACE_DIR": str(selected),
            "COMSOL_WORKBENCH_APP_DATA_DIR": str(tmp_path / "AppData"),
        },
        frozen=False,
    )
    assert paths.workspace_dir == selected.resolve()
    assert paths.logs_dir == selected.resolve() / "logs"


def test_ensure_writable_never_creates_packaged_kb(tmp_path: Path) -> None:
    kb = tmp_path / "read-only-resource" / "knowledge_base"
    paths = AppPaths.discover(
        {
            "COMSOL_WORKBENCH_DESKTOP_MODE": "1",
            "COMSOL_WORKBENCH_APP_DATA_DIR": str(tmp_path / "data"),
            "COMSOL_WORKBENCH_WORKSPACE_DIR": str(tmp_path / "workspace"),
            "COMSOL_WORKBENCH_KB_DIR": str(kb),
        },
        frozen=False,
    )
    paths.ensure_writable()
    assert paths.workspace_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert not kb.exists()


def test_kb_manifest_verification_detects_success_and_corruption(tmp_path: Path) -> None:
    files = ["a.md", "b.json"]
    (tmp_path / "a.md").write_text("audited knowledge", encoding="utf-8")
    (tmp_path / "b.json").write_text('{"units": 14210}', encoding="utf-8")
    manifest = {
        "version": "0.4.0-beta",
        "unit_count": 14210,
        "document_count": 108,
        "source_reference_count": 27523,
        "files": files,
        "sha256": composite_sha256(tmp_path, files),
    }
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_knowledge_base(tmp_path)["status"] == "verified"
    (tmp_path / "a.md").write_text("corrupt", encoding="utf-8")
    result = verify_knowledge_base(tmp_path)
    assert result["status"] == "corrupt"
    assert result["valid"] is False


def test_missing_kb_is_nonfatal(tmp_path: Path) -> None:
    result = verify_knowledge_base(tmp_path / "missing")
    assert result["status"] == "missing"
    assert result["valid"] is False


def test_desktop_session_token_protects_runtime_api(monkeypatch, tmp_path: Path) -> None:
    from app.session.store import session_store

    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", "phase4-test-token")
    monkeypatch.setattr(session_store, "path", tmp_path / "sessions.db")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 401
        response = client.get("/api/health", headers={"X-Workbench-Token": "phase4-test-token"})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    monkeypatch.delenv("COMSOL_WORKBENCH_SESSION_TOKEN", raising=False)


def test_current_process_is_detected_for_parent_monitor() -> None:
    from desktop_runtime import _parent_alive

    assert _parent_alive(os.getpid()) is True


def test_desktop_java_bridge_uses_shared_ephemeral_loopback_url(monkeypatch) -> None:
    from urllib.parse import urlparse

    from app.comsol.bridge_process import JavaBridgeProcessManager
    from app.comsol.java_bridge import JavaBridgeClient

    monkeypatch.setenv("COMSOL_WORKBENCH_DESKTOP_MODE", "1")
    monkeypatch.delenv("COMSOL_WORKBENCH_JAVA_BRIDGE_URL", raising=False)
    manager = JavaBridgeProcessManager()
    url = manager.status()["base_url"]
    parsed = urlparse(url)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port not in {None, 8765}
    assert JavaBridgeClient().configured_url() == url
