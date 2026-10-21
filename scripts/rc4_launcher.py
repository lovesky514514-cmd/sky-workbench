"""One-click RC4 launcher with fail-closed product preflight."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rc2_launcher as platform_probe


VERSION = "1.1.0-rc4"
STANDARD_MODEL = "qwen3:8b"
LOW_VRAM_MODEL = "qwen3:4b"
STANDARD_VRAM_THRESHOLD_MIB = 7168
MODEL_FILES = {
    "qwen3:4b": ("sha256-3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f", 2_497_280_480),
    "qwen3:8b": ("sha256-a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f", 5_225_374_496),
}


def _root() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def _ollama_version(executable: Path) -> str:
    try:
        result = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW, check=False,
        )
        return (result.stdout or result.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def preflight(root: Path | None = None) -> dict[str, Any]:
    product_root = (root or _root()).resolve()
    app_data = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "COMSOLScientificWorkbenchRC4"
    diagnostics = app_data / "diagnostics"
    workspace = app_data / "workspace"
    vram_mib, gpu_name = platform_probe._vram_mib()
    low_vram = vram_mib < STANDARD_VRAM_THRESHOLD_MIB
    selected_model = LOW_VRAM_MODEL if low_vram else STANDARD_MODEL
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str, path: Path | None = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail, "path": str(path) if path else None})

    desktop = product_root / "Workbench.exe"
    runtime = product_root / "scientific-runtime.exe"
    ollama = product_root / "runtime" / "ollama" / "ollama.exe"
    models = product_root / "models" / "ollama"
    bridge = product_root / "resources" / "java_bridge" / "src" / "main" / "java" / "workbench" / "bridge" / "ComsolBridgeServer.java"
    semantic_worker = product_root / "resources" / "semantic_runtime" / "semantic_runtime_worker.py"
    check("Desktop application", desktop.is_file(), "Packaged React/Tauri desktop shell", desktop)
    check("Scientific Runtime", runtime.is_file(), "Packaged Python runtime; no development Python required", runtime)
    version = _ollama_version(ollama) if ollama.is_file() else ""
    check("Packaged Ollama", ollama.is_file() and "0.30.9" in version, version or "Ollama 0.30.9 executable not available", ollama)
    check("Java Bridge source", bridge.is_file(), "Bridge is compiled against the discovered COMSOL Model API at runtime", bridge)
    check("Semantic runtime", semantic_worker.is_file(), "Local semantic worker payload", semantic_worker)
    for model, (blob, size) in MODEL_FILES.items():
        tag = model.rsplit(":", 1)[1]
        manifest = models / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / tag
        weight = models / "blobs" / blob
        passed = manifest.is_file() and weight.is_file() and weight.stat().st_size == size
        check(model, passed, f"Frozen packaged weight; expected {size} bytes", weight)
    comsol = platform_probe._detect_comsol()
    check("COMSOL 6.3", bool(comsol.get("passed")), str(comsol.get("version") or comsol.get("detail")), Path(comsol["root"]) if comsol.get("root") else None)
    check("Writable workspace", platform_probe._writable(workspace), "Per-user workspace is writable", workspace)
    check("Required loopback ports", platform_probe._loopback_available(), "Dynamic loopback allocation is available")
    blocking = [item for item in checks if not item["passed"]]
    status = "BLOCKED" if blocking else "DEGRADED" if low_vram else "READY"
    result = {
        "schema_version": "1.1",
        "product": "COMSOL Scientific Workbench RC4",
        "version": VERSION,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "overall_status": status,
        "selected_model": selected_model,
        "model_mode": "LOW_VRAM_MODE" if low_vram else "STANDARD",
        "fallback_allowed": False,
        "hardware": {"gpu": gpu_name, "vram_mib": vram_mib, "standard_threshold_mib": STANDARD_VRAM_THRESHOLD_MIB},
        "comsol": comsol,
        "workspace": str(workspace),
        "diagnostics": str(diagnostics),
        "checks": checks,
    }
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "preflight-latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    root = _root()
    result = preflight(root)
    status = result["overall_status"]
    diagnostics = Path(result["diagnostics"])
    first_launch_marker = diagnostics / ".first-launch-preflight-shown"
    summary = (
        f"Status: {status}\nModel: {result['selected_model']} ({result['model_mode']})\n"
        f"GPU: {result['hardware']['gpu']} / {result['hardware']['vram_mib']} MiB VRAM\n"
        f"COMSOL: {result['comsol'].get('version') or 'not found'}\n\nDiagnostics: {diagnostics / 'preflight-latest.json'}"
    )
    if status == "BLOCKED":
        failures = "\n".join(f"- {item['name']}: {item['detail']}" for item in result["checks"] if not item["passed"])
        platform_probe._message("COMSOL Scientific Workbench RC4 — BLOCKED", f"{summary}\n\n{failures}", error=True)
        return 3
    if status == "DEGRADED" or not first_launch_marker.exists():
        detail = "\n\nLOW_VRAM_MODE is visible; no silent fallback occurred." if status == "DEGRADED" else ""
        platform_probe._message(f"COMSOL Scientific Workbench RC4 — {status}", summary + detail)
        first_launch_marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    process = subprocess.Popen([str(root / "Workbench.exe")], cwd=root)
    return int(process.wait())


if __name__ == "__main__":
    raise SystemExit(main())
