"""RC4.1 Lite launcher with explicit local-provider discovery and no silent downloads."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rc2_launcher as platform_probe


VERSION = "1.1.0-rc4.1"
STANDARD_MODEL = "qwen3:8b"
LOW_VRAM_MODEL = "qwen3:4b"
STANDARD_VRAM_THRESHOLD_MIB = 7168


def _root() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def _ollama_endpoint() -> str:
    raw = os.getenv("COMSOL_WORKBENCH_OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if not raw.startswith("http://"):
        raw = f"http://{raw}"
    return raw.rstrip("/")


def _json_request(url: str, *, payload: dict[str, Any] | None = None, timeout: float = 5) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _ollama_probe(base_url: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        tags = _json_request(f"{base_url}/api/tags", timeout=3)
        version = _json_request(f"{base_url}/api/version", timeout=3).get("version", "")
        models = sorted({str(item.get("name") or item.get("model")) for item in tags.get("models", []) if item.get("name") or item.get("model")})
        return {"state": "Detected", "models": models, "version": str(version), "latency_ms": round((time.perf_counter() - started) * 1000, 3)}
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"state": "Missing", "models": [], "version": "", "latency_ms": round((time.perf_counter() - started) * 1000, 3), "detail": str(exc)}


def preflight(root: Path | None = None) -> dict[str, Any]:
    product_root = (root or _root()).resolve()
    app_data = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "COMSOLScientificWorkbenchRC41"
    diagnostics = app_data / "diagnostics"
    workspace = app_data / "workspace"
    vram_mib, gpu_name = platform_probe._vram_mib()
    low_vram = vram_mib < STANDARD_VRAM_THRESHOLD_MIB
    selected_model = LOW_VRAM_MODEL if low_vram else STANDARD_MODEL
    base_url = _ollama_endpoint()
    ollama = _ollama_probe(base_url)
    model_detected = selected_model in ollama["models"]
    models_root = os.getenv("OLLAMA_MODELS", "Ollama default storage")
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str, *, required: bool, path: Path | None = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "required": required, "detail": detail, "path": str(path) if path else None})

    desktop = product_root / "Workbench.exe"
    runtime = product_root / "scientific-runtime.exe"
    bridge = product_root / "resources" / "java_bridge" / "src" / "main" / "java" / "workbench" / "bridge" / "ComsolBridgeServer.java"
    semantic_worker = product_root / "resources" / "semantic_runtime" / "semantic_runtime_worker.py"
    check("Desktop application", desktop.is_file(), "Packaged React/Tauri desktop shell", required=True, path=desktop)
    check("Scientific Runtime", runtime.is_file(), "Packaged Python runtime", required=True, path=runtime)
    check("Java Bridge", bridge.is_file(), "Required bridge source is compiled against the discovered COMSOL API", required=True, path=bridge)
    check("Semantic runtime", semantic_worker.is_file(), "Required local semantic worker", required=True, path=semantic_worker)
    check("Writable workspace", platform_probe._writable(workspace), "Per-user workspace is writable", required=True, path=workspace)
    check("Required loopback ports", platform_probe._loopback_available(), "Dynamic loopback allocation is available", required=True)
    comsol = platform_probe._detect_comsol()
    check("COMSOL 6.3", bool(comsol.get("passed")), str(comsol.get("version") or comsol.get("detail") or "Missing"), required=False, path=Path(comsol["root"]) if comsol.get("root") else None)
    check("AI Provider", ollama["state"] == "Detected", f"Ollama {ollama.get('version') or 'service missing'} at {base_url}", required=False)
    check("Local Model", model_detected, f"Exact required identity: {selected_model}", required=False)
    blocked = [item for item in checks if item["required"] and not item["passed"]]
    degraded = [item for item in checks if not item["required"] and not item["passed"]]
    status = "BLOCKED" if blocked else "DEGRADED" if degraded else "READY"
    result = {
        "schema_version": "1.2",
        "product": "COMSOL Scientific Workbench RC4.1",
        "version": VERSION,
        "distribution": "lite",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "overall_status": status,
        "selected_model": selected_model,
        "model_mode": "LOW_VRAM_MODE" if low_vram else "STANDARD",
        "fallback_allowed": False,
        "silent_download_allowed": False,
        "hardware": {"gpu": gpu_name, "vram_mib": vram_mib, "standard_threshold_mib": STANDARD_VRAM_THRESHOLD_MIB},
        "comsol": comsol,
        "ai_provider": {"provider": "Ollama", "state": ollama["state"], "base_url": base_url, "version": ollama.get("version", ""), "latency_ms": ollama.get("latency_ms")},
        "local_model": {"state": "Detected" if model_detected else "Missing", "identity": selected_model, "available_models": ollama["models"], "storage": models_root},
        "workspace": str(workspace),
        "diagnostics": str(diagnostics),
        "checks": checks,
    }
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "preflight-latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def inference_probe(result: dict[str, Any]) -> dict[str, Any]:
    if result["ai_provider"]["state"] != "Detected" or result["local_model"]["state"] != "Detected":
        return {"status": "BLOCKED_BY_ENVIRONMENT", "model": result["selected_model"], "fallback_used": False}
    started = time.perf_counter()
    try:
        response = _json_request(
            f"{result['ai_provider']['base_url']}/api/generate",
            payload={"model": result["selected_model"], "prompt": "Reply with exactly: RC41_OK", "stream": False, "options": {"temperature": 0, "seed": 41}},
            timeout=180,
        )
        text = str(response.get("response", "")).strip()
        status = "PASS" if "RC41_OK" in text else "FAIL"
        return {"status": status, "model": str(response.get("model") or result["selected_model"]), "response": text, "latency_ms": round((time.perf_counter() - started) * 1000, 3), "fallback_used": False}
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "model": result["selected_model"], "detail": str(exc), "latency_ms": round((time.perf_counter() - started) * 1000, 3), "fallback_used": False}


def _show(title: str, body: str, *, error: bool = False) -> None:
    if os.getenv("COMSOL_WORKBENCH_HEADLESS") == "1":
        return
    platform_probe._message(title, body, error=error)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--smoke-inference", action="store_true")
    parser.add_argument("--no-launch", action="store_true")
    options = parser.parse_args(argv)
    root = _root()
    result = preflight(root)
    diagnostics = Path(result["diagnostics"])
    if options.smoke_inference:
        probe = inference_probe(result)
        (diagnostics / "inference-smoke-latest.json").write_text(json.dumps(probe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if probe["status"] != "PASS":
            return 4
    if result["overall_status"] == "BLOCKED":
        failures = "\n".join(f"- {item['name']}: {item['detail']}" for item in result["checks"] if item["required"] and not item["passed"])
        _show("COMSOL Scientific Workbench RC4.1 — BLOCKED", failures, error=True)
        return 3
    if options.preflight_only or options.no_launch:
        return 0
    if result["overall_status"] == "DEGRADED":
        missing = ", ".join(item["name"] for item in result["checks"] if not item["passed"])
        _show("COMSOL Scientific Workbench RC4.1 — DEGRADED", f"Missing: {missing}\n\nThe Workbench will open. Missing capabilities remain unavailable; no model is downloaded silently.")
    process = subprocess.Popen([str(root / "Workbench.exe")], cwd=root)
    return int(process.wait())


if __name__ == "__main__":
    raise SystemExit(main())
