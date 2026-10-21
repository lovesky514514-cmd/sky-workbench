from __future__ import annotations

import importlib.util
from pathlib import Path

from app.config import SettingsStore


def _launcher_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "rc2_launcher.py"
    spec = importlib.util.spec_from_file_location("rc2_launcher_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_runtime_pins_ollama_and_disables_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("COMSOL_WORKBENCH_OLLAMA_BASE_URL", "http://127.0.0.1:48123")
    monkeypatch.setenv("COMSOL_WORKBENCH_PRODUCT_MODEL", "qwen3:8b")
    settings = SettingsStore(path=tmp_path / "settings.json").load()
    assert settings["active_provider"] == "ollama"
    assert settings["providers"]["ollama"]["base_url"] == "http://127.0.0.1:48123"
    assert settings["providers"]["ollama"]["model"] == "qwen3:8b"
    assert all(
        not provider["enabled"]
        for name, provider in settings["providers"].items()
        if name != "ollama"
    )


def _minimal_payload(module, root: Path) -> None:
    for relative in ("Workbench.exe", "scientific-runtime.exe", "runtime/ollama/ollama.exe"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"test")
    module.MODEL_FILES = {
        "qwen3:4b": ("sha256-four", 4),
        "qwen3:8b": ("sha256-eight", 8),
    }
    models = root / "models" / "ollama"
    for model, (blob, size) in module.MODEL_FILES.items():
        tag = model.rsplit(":", 1)[1]
        manifest = models / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / tag
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{}", encoding="utf-8")
        weight = models / "blobs" / blob
        weight.parent.mkdir(parents=True, exist_ok=True)
        weight.write_bytes(b"x" * size)


def test_preflight_selects_standard_8b_without_fallback(monkeypatch, tmp_path: Path):
    module = _launcher_module()
    product = tmp_path / "product"
    _minimal_payload(module, product)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(module, "_vram_mib", lambda: (8192, "test gpu"))
    monkeypatch.setattr(module, "_detect_comsol", lambda: {"passed": True, "status": "READY", "version": "COMSOL 6.3", "root": str(tmp_path)})
    result = module.preflight(product)
    assert result["overall_status"] == "READY"
    assert result["selected_model"] == "qwen3:8b"
    assert result["fallback_allowed"] is False


def test_preflight_low_vram_is_visible_degraded(monkeypatch, tmp_path: Path):
    module = _launcher_module()
    product = tmp_path / "product"
    _minimal_payload(module, product)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(module, "_vram_mib", lambda: (4096, "test gpu"))
    monkeypatch.setattr(module, "_detect_comsol", lambda: {"passed": True, "status": "READY", "version": "COMSOL 6.3", "root": str(tmp_path)})
    result = module.preflight(product)
    assert result["overall_status"] == "DEGRADED"
    assert result["selected_model"] == "qwen3:4b"
    assert result["model_mode"] == "LOW_VRAM_MODE"


def test_preflight_missing_required_payload_is_blocked(monkeypatch, tmp_path: Path):
    module = _launcher_module()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(module, "_vram_mib", lambda: (8192, "test gpu"))
    monkeypatch.setattr(module, "_detect_comsol", lambda: {"passed": True, "status": "READY", "version": "COMSOL 6.3", "root": str(tmp_path)})
    result = module.preflight(tmp_path / "missing")
    assert result["overall_status"] == "BLOCKED"
    assert any(not item["passed"] for item in result["checks"])
