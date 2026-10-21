from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.session.execution_eta import estimate_remaining_time
from app.session.store import SessionStore


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _script_module(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _store(tmp_path: Path) -> SessionStore:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    store.initialize()
    return store


def _execution(
    store: SessionStore,
    session_id: str,
    *,
    workflow_id: str,
    duration_ms: int,
    status: str = "success",
) -> None:
    item_id = f"execution-{session_id}"
    store.append_event(
        session_id,
        "timeline.started",
        {
            "id": item_id,
            "category": "SYSTEM",
            "phase": "Execution",
            "label": "Executing saved ScientificPlan",
            "status": "running",
            "tool": "runtime.execution_protocol",
            "input_summary": {"workflow_id": workflow_id},
        },
    )
    store.append_event(
        session_id,
        "timeline.completed",
        {
            "id": item_id,
            "status": status,
            "duration_ms": duration_ms,
        },
    )


def test_eta_is_unknown_without_three_successful_same_workflow_samples(tmp_path: Path) -> None:
    store = _store(tmp_path)
    current = store.create("workbench")
    store.set_plan(current["id"], {"workflow_id": "heat_transfer_2d_stationary"})
    store.set_state(current["id"], "SOLVING")
    for seconds in (10, 20):
        historic = store.create("workbench")
        _execution(store, historic["id"], workflow_id="heat_transfer_2d_stationary", duration_ms=seconds * 1000)

    result = estimate_remaining_time(store, current["id"])

    assert result["status"] == "unknown"
    assert result["estimated_remaining_seconds"] is None
    assert result["sample_count"] == 2
    assert result["source"] == "execution_ledger"


def test_eta_uses_only_successful_same_workflow_execution_ledger_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    current = store.create("workbench")
    store.set_plan(current["id"], {"workflow_id": "heat_transfer_2d_stationary"})
    store.set_state(current["id"], "SOLVING")
    now = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)

    for seconds in (10, 20, 40):
        historic = store.create("workbench")
        _execution(store, historic["id"], workflow_id="heat_transfer_2d_stationary", duration_ms=seconds * 1000)
    failed = store.create("workbench")
    _execution(store, failed["id"], workflow_id="heat_transfer_2d_stationary", duration_ms=1_000, status="error")
    unrelated = store.create("workbench")
    _execution(store, unrelated["id"], workflow_id="tds_2d_stationary_diffusion", duration_ms=999_000)

    store.append_event(
        current["id"],
        "timeline.started",
        {
            "id": "active-execution",
            "category": "SYSTEM",
            "phase": "Execution",
            "label": "Executing saved ScientificPlan",
            "status": "running",
            "tool": "runtime.execution_protocol",
            "input_summary": {"workflow_id": "heat_transfer_2d_stationary"},
        },
    )
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE events SET created_at=? WHERE session_id=? AND event_type='timeline.started'",
            ((now - timedelta(seconds=5)).isoformat(), current["id"]),
        )

    result = estimate_remaining_time(store, current["id"], now=now)

    assert result["status"] == "estimated"
    assert result["estimated_remaining_seconds"] == 15
    assert result["sample_count"] == 3
    assert result["basis"] == "same_workflow_successful_execution_median"
    assert result["precision"] == "coarse"


def test_eta_reports_complete_without_inventing_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    current = store.create("workbench")
    store.set_plan(current["id"], {"workflow_id": "heat_transfer_2d_stationary"})
    store.set_state(current["id"], "COMPLETED")

    result = estimate_remaining_time(store, current["id"])

    assert result["status"] == "complete"
    assert result["estimated_remaining_seconds"] == 0
    assert result["sample_count"] == 0


def test_rc4_preflight_preserves_explicit_model_and_bridge_gates(monkeypatch, tmp_path: Path) -> None:
    launcher = _script_module("rc4_launcher")
    root = tmp_path / "product"
    for relative in (
        "Workbench.exe",
        "scientific-runtime.exe",
        "runtime/ollama/ollama.exe",
        "resources/java_bridge/src/main/java/workbench/bridge/ComsolBridgeServer.java",
        "resources/semantic_runtime/semantic_runtime_worker.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    monkeypatch.setattr(
        launcher,
        "MODEL_FILES",
        {"qwen3:4b": ("weight-4", 1), "qwen3:8b": ("weight-8", 1)},
    )
    for model, (blob, _) in launcher.MODEL_FILES.items():
        tag = model.rsplit(":", 1)[1]
        manifest = root / "models/ollama/manifests/registry.ollama.ai/library/qwen3" / tag
        weight = root / "models/ollama/blobs" / blob
        manifest.parent.mkdir(parents=True, exist_ok=True)
        weight.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_bytes(b"x")
        weight.write_bytes(b"x")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(launcher, "_ollama_version", lambda _: "ollama version is 0.30.9")
    monkeypatch.setattr(launcher.platform_probe, "_vram_mib", lambda: (8192, "test GPU"))
    monkeypatch.setattr(
        launcher.platform_probe,
        "_detect_comsol",
        lambda: {"passed": True, "version": "COMSOL 6.3", "root": str(tmp_path / "comsol")},
    )
    monkeypatch.setattr(launcher.platform_probe, "_writable", lambda _: True)
    monkeypatch.setattr(launcher.platform_probe, "_loopback_available", lambda: True)

    result = launcher.preflight(root)

    assert result["overall_status"] == "READY"
    assert result["selected_model"] == "qwen3:8b"
    assert result["fallback_allowed"] is False
    assert result["product"] == "COMSOL Scientific Workbench RC4"
    assert result["version"] == "1.1.0-rc4"


def test_rc4_setup_rejects_payload_path_escape(tmp_path: Path) -> None:
    setup = _script_module("rc4_setup")
    source = tmp_path / "payload"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x")
    payload = {
        "entries": [
            {"path": "../outside.txt", "bytes": 1, "sha256": setup._sha256(outside)}
        ]
    }

    try:
        setup._verify_payload(source, payload)
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("path escape was accepted")


def test_rc4_packager_preserves_java_bridge_src_main_root() -> None:
    script = (ROOT / "scripts" / "build_product_rc4.ps1").read_text(encoding="utf-8")
    create_destination = (
        'New-Item -ItemType Directory -Force -Path $bridgeDestination | Out-Null'
    )
    copy_children = 'Copy-Item -LiteralPath $_.FullName -Destination $bridgeDestination -Recurse'

    assert create_destination in script
    assert copy_children in script
    assert script.index(create_destination) < script.index(copy_children)
