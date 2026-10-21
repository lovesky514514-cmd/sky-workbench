"""One-click RC2 launcher with explicit product preflight.

This executable never downloads software or models. It validates the sibling
payload, records READY/DEGRADED/BLOCKED, then starts the desktop shell. The
desktop shell owns the scientific runtime and packaged Ollama process tree.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


VERSION = "1.0.0-rc2"
STANDARD_MODEL = "qwen3:8b"
LOW_VRAM_MODEL = "qwen3:4b"
STANDARD_VRAM_THRESHOLD_MIB = 7168
MODEL_FILES = {
    "qwen3:4b": ("sha256-3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f", 2_497_280_480),
    "qwen3:8b": ("sha256-a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f", 5_225_374_496),
}


def _root() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def _message(title: str, body: str, *, error: bool = False) -> None:
    flags = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(None, body, title, flags)


def _vram_mib() -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["nvidia-smi.exe", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        rows = []
        for line in result.stdout.splitlines():
            name, raw = line.rsplit(",", 1)
            rows.append((int(float(raw.strip())), name.strip()))
        return max(rows, default=(0, "No NVIDIA GPU detected"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0, "GPU telemetry unavailable"


def _candidate_comsol_roots() -> list[Path]:
    values: list[Path] = []
    for name in ("COMSOL_ROOT", "COMSOL_INSTALL_PATH"):
        if os.getenv(name):
            values.append(Path(os.environ[name]))
    program_files = Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "COMSOL"
    values.extend([program_files, program_files / "COMSOL64"])
    if program_files.is_dir():
        values.extend(path for path in program_files.glob("**/Multiphysics") if path.is_dir())
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        values.append(Path(f"{letter}:\\comsol"))
    unique: list[Path] = []
    seen: set[str] = set()
    for value in values:
        try:
            resolved = value.resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if key not in seen:
            unique.append(resolved)
            seen.add(key)
    return unique


def _detect_comsol() -> dict[str, Any]:
    for root in _candidate_comsol_roots():
        jars = sorted((root / "plugins").glob("com.comsol.api_*.jar")) if root.is_dir() else []
        java = root / "java" / "win64" / "jre" / "bin" / "java.exe"
        mphserver = root / "bin" / "win64" / "comsolmphserver.exe"
        if not jars or not java.is_file() or not mphserver.is_file():
            continue
        version = "COMSOL 6.x"
        about = root / "about.txt"
        if about.is_file():
            match = re.search(r"COMSOL\s+(\d+\.\d+)", about.read_text(encoding="utf-8", errors="ignore"))
            if match:
                version = f"COMSOL {match.group(1)}"
        return {
            "passed": version == "COMSOL 6.3",
            "status": "READY" if version == "COMSOL 6.3" else "BLOCKED",
            "version": version,
            "root": str(root),
            "api_jar": str(jars[-1]),
            "mphserver": str(mphserver),
        }
    return {"passed": False, "status": "BLOCKED", "version": "", "detail": "COMSOL 6.3 Model API installation not found"}


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".rc2-write-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _loopback_available() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


def preflight(root: Path | None = None) -> dict[str, Any]:
    product_root = (root or _root()).resolve()
    app_data = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "COMSOLScientificWorkbenchRC2"
    diagnostics = app_data / "diagnostics"
    workspace = app_data / "workspace"
    vram_mib, gpu_name = _vram_mib()
    low_vram = vram_mib < STANDARD_VRAM_THRESHOLD_MIB
    selected_model = LOW_VRAM_MODEL if low_vram else STANDARD_MODEL
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str, path: Path | None = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail, "path": str(path) if path else None})

    desktop = product_root / "Workbench.exe"
    runtime = product_root / "scientific-runtime.exe"
    ollama = product_root / "runtime" / "ollama" / "ollama.exe"
    models = product_root / "models" / "ollama"
    check("Desktop application", desktop.is_file(), "Packaged Workbench desktop shell", desktop)
    check("Scientific Runtime", runtime.is_file(), "Frozen Python runtime; no Python installation required", runtime)
    check("Packaged Ollama", ollama.is_file(), "Owned local model runtime 0.30.9", ollama)
    for model, (blob, size) in MODEL_FILES.items():
        tag = model.rsplit(":", 1)[1]
        manifest = models / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / tag
        weight = models / "blobs" / blob
        passed = manifest.is_file() and weight.is_file() and weight.stat().st_size == size
        check(model, passed, f"Frozen packaged weight blob; expected {size} bytes", weight)
    comsol = _detect_comsol()
    check("COMSOL 6.3", bool(comsol.get("passed")), str(comsol.get("version") or comsol.get("detail")), Path(comsol["root"]) if comsol.get("root") else None)
    check("Writable workspace", _writable(workspace), "User workspace is writable", workspace)
    check("Required loopback ports", _loopback_available(), "Dynamic loopback port allocation is available")

    blocking = [item for item in checks if not item["passed"]]
    status = "BLOCKED" if blocking else "DEGRADED" if low_vram else "READY"
    result = {
        "schema_version": "1.0",
        "product": "COMSOL Scientific Workbench RC2",
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
        f"Status: {status}\n"
        f"Model: {result['selected_model']} ({result['model_mode']})\n"
        f"GPU: {result['hardware']['gpu']} / {result['hardware']['vram_mib']} MiB VRAM\n"
        f"COMSOL: {result['comsol'].get('version') or 'not found'}\n\n"
        f"Diagnostics: {diagnostics / 'preflight-latest.json'}"
    )
    if status == "BLOCKED":
        failures = "\n".join(f"- {item['name']}: {item['detail']}" for item in result["checks"] if not item["passed"])
        _message("COMSOL Scientific Workbench RC2 — BLOCKED", f"{summary}\n\n{failures}", error=True)
        return 3
    if status == "DEGRADED" or not first_launch_marker.exists():
        detail = "\n\nLOW_VRAM_MODE is visible in the application; no silent fallback occurred." if status == "DEGRADED" else ""
        _message(f"COMSOL Scientific Workbench RC2 — {status}", summary + detail)
        first_launch_marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    process = subprocess.Popen([str(root / "Workbench.exe")], cwd=root)
    return int(process.wait())


if __name__ == "__main__":
    raise SystemExit(main())
