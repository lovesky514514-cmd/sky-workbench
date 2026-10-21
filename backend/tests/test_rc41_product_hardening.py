from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _script_module(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _minimal_product(root: Path) -> None:
    for relative in (
        "Workbench.exe",
        "scientific-runtime.exe",
        "resources/java_bridge/src/main/java/workbench/bridge/ComsolBridgeServer.java",
        "resources/semantic_runtime/semantic_runtime_worker.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"required")


def test_rc41_lite_preflight_is_degraded_not_blocked_when_local_ai_is_missing(monkeypatch, tmp_path: Path) -> None:
    launcher = _script_module("rc41_launcher")
    root = tmp_path / "product"
    _minimal_product(root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(launcher.platform_probe, "_vram_mib", lambda: (8192, "test GPU"))
    monkeypatch.setattr(
        launcher.platform_probe,
        "_detect_comsol",
        lambda: {"passed": True, "version": "COMSOL 6.3", "root": str(tmp_path / "comsol")},
    )
    monkeypatch.setattr(launcher.platform_probe, "_writable", lambda _: True)
    monkeypatch.setattr(launcher.platform_probe, "_loopback_available", lambda: True)
    monkeypatch.setattr(launcher, "_ollama_probe", lambda _: {"state": "Missing", "models": [], "version": "", "latency_ms": 0})

    result = launcher.preflight(root)

    assert result["overall_status"] == "DEGRADED"
    assert result["ai_provider"]["state"] == "Missing"
    assert result["local_model"]["state"] == "Missing"
    assert result["selected_model"] == "qwen3:8b"
    assert result["fallback_allowed"] is False
    assert result["distribution"] == "lite"


def test_rc41_lite_preflight_honors_ollama_models_and_exact_model(monkeypatch, tmp_path: Path) -> None:
    launcher = _script_module("rc41_launcher")
    root = tmp_path / "product"
    _minimal_product(root)
    models_root = tmp_path / "models"
    models_root.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("OLLAMA_MODELS", str(models_root))
    monkeypatch.setattr(launcher.platform_probe, "_vram_mib", lambda: (8192, "test GPU"))
    monkeypatch.setattr(launcher.platform_probe, "_detect_comsol", lambda: {"passed": True, "version": "COMSOL 6.3", "root": "C:/COMSOL"})
    monkeypatch.setattr(launcher.platform_probe, "_writable", lambda _: True)
    monkeypatch.setattr(launcher.platform_probe, "_loopback_available", lambda: True)
    monkeypatch.setattr(
        launcher,
        "_ollama_probe",
        lambda _: {"state": "Detected", "models": ["qwen3:8b"], "version": "0.30.9", "latency_ms": 2},
    )

    result = launcher.preflight(root)

    assert result["overall_status"] == "READY"
    assert result["local_model"]["state"] == "Detected"
    assert result["local_model"]["identity"] == "qwen3:8b"
    assert result["local_model"]["storage"] == str(models_root)


def test_rc41_setup_rejects_payload_path_escape(tmp_path: Path) -> None:
    setup = _script_module("rc41_setup")
    source = tmp_path / "payload"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x")

    try:
        setup._verify_payload(
            source,
            {"entries": [{"path": "../outside.txt", "bytes": 1, "sha256": setup._sha256(outside)}]},
        )
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("path escape was accepted")


def test_rc41_packager_excludes_models_ollama_and_development_payloads() -> None:
    script = (ROOT / "scripts" / "build_product_rc41.ps1").read_text(encoding="utf-8")
    assert 'distribution="lite"' in script
    assert 'model_weights_included=$false' in script
    assert 'ollama_runtime_included=$false' in script
    assert 'runtime\\ollama' not in script
    assert 'models\\ollama\\blobs' not in script
    for forbidden in ("screenshots", "node_modules", "src-tauri\\target", "research", "tests"):
        assert f'Join-Path $payloadRoot "{forbidden}' not in script

