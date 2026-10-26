"""Lightweight local-model lifecycle; large operations require explicit confirmation."""

from __future__ import annotations

import asyncio
import ctypes
import os
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

from app.app_paths import app_paths
from app.config import settings_store


def ollama_executable() -> str | None:
    """Find a user-installed Ollama without assuming it was added to PATH."""
    found = shutil.which("ollama")
    if found:
        return found
    if os.name != "nt":
        return None
    roots = [os.getenv("LOCALAPPDATA", ""), os.getenv("ProgramFiles", ""), os.getenv("ProgramFiles(x86)", "")]
    for root in filter(None, roots):
        for relative in ("Programs/Ollama/ollama.exe", "Ollama/ollama.exe"):
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return None


def pull_percent(payload: dict[str, Any]) -> int | None:
    """Calculate progress only from byte counts reported by Ollama."""
    total, completed = payload.get("total"), payload.get("completed")
    if not isinstance(total, (int, float)) or not isinstance(completed, (int, float)) or not math.isfinite(total) or not math.isfinite(completed) or total <= 0 or completed < 0:
        return None
    return min(100, round(100 * completed / total))


def _ram_bytes() -> int:
    if os.name != "nt":
        return 0
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    return int(status.total) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 0


def _gpu() -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"name": "Not detected", "vram_bytes": 0}
    try:
        result = subprocess.run([executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        name, memory = (result.stdout.strip().splitlines()[0].rsplit(",", 1) if result.stdout.strip() else ("Not detected", "0"))
        return {"name": name.strip(), "vram_bytes": int(float(memory.strip()) * 1024 * 1024)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"name": "Detection failed", "vram_bytes": 0}


class LocalModelManager:
    def __init__(self) -> None:
        self._pull_task: asyncio.Task[None] | None = None
        self._pull_state: dict[str, Any] = {"state": "idle", "model": "", "detail": "No download running"}

    async def status(self) -> dict[str, Any]:
        executable = ollama_executable()
        settings = settings_store.load()
        base_url = str(settings["providers"]["ollama"].get("base_url", "http://127.0.0.1:11434")).rstrip("/")
        models: list[dict[str, Any]] = []
        detail = "Ollama is not installed"
        state = "Not Installed"
        if executable:
            state, detail = "Installed but Stopped", "Ollama is installed but its local service is not responding"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1.5, connect=0.5)) as client:
                response = await client.get(f"{base_url}/api/tags")
                response.raise_for_status()
                models = list(response.json().get("models", []))
            state = "Ready" if models else "No Model"
            detail = f"{len(models)} installed model(s)" if models else "Ollama is running; no local model is installed"
        # A broken certificate path may fail during client construction. Treat
        # this as unavailable Ollama, not a fatal desktop initialization error.
        except (httpx.HTTPError, OSError, ValueError, KeyError):
            pass
        ram = _ram_bytes()
        gpu = _gpu()
        free_disk = shutil.disk_usage(app_paths.models_dir).free if app_paths.models_dir.exists() else shutil.disk_usage(app_paths.workspace_dir).free
        recommendation = "Light" if ram and ram < 16 * 1024**3 else "Balanced" if ram < 32 * 1024**3 else "Strong"
        return {
            "state": state, "detail": detail, "executable": executable, "base_url": base_url, "models": models,
            "hardware": {"ram_bytes": ram, "gpu": gpu, "free_disk_bytes": free_disk},
            "recommendation": recommendation,
            "model_storage": os.getenv("OLLAMA_MODELS", "Ollama default storage"),
            "pull": dict(self._pull_state),
            "product_mode": os.getenv("COMSOL_WORKBENCH_PRODUCT_MODE", "") == "rc2",
            "selected_model": os.getenv("COMSOL_WORKBENCH_PRODUCT_MODEL", ""),
            "model_mode": os.getenv("COMSOL_WORKBENCH_MODEL_MODE", ""),
            "low_vram_mode": os.getenv("COMSOL_WORKBENCH_LOW_VRAM_MODE", "") == "1",
            "detected_vram_mib": int(os.getenv("COMSOL_WORKBENCH_DETECTED_VRAM_MIB", "0") or 0),
        }

    async def start(self) -> dict[str, Any]:
        executable = ollama_executable()
        if not executable:
            return {"started": False, "state": "Not Installed", "detail": "Install Ollama before starting it"}
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
        subprocess.Popen([executable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        for _ in range(20):
            await asyncio.sleep(0.25)
            current = await self.status()
            if current["state"] in {"No Model", "Ready"}:
                return {"started": True, **current}
        return {"started": False, "state": "Error", "detail": "Ollama did not become ready within 5 seconds"}

    async def pull(self, model: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            return {"state": "confirmation_required", "model": model, "estimated_download": "Model-dependent; commonly multiple GB", "storage": (await self.status())["model_storage"]}
        current = await self.status()
        if current["state"] not in {"Ready", "No Model"}:
            raise RuntimeError("Ollama service is unavailable; install or start Ollama first")
        if self._pull_task and not self._pull_task.done():
            raise RuntimeError("A model download is already running")
        self._pull_state = {"state": "running", "model": model, "detail": "Download started", "percent": None}
        self._pull_task = asyncio.create_task(self._watch_pull(model, current["base_url"]))
        return dict(self._pull_state)

    async def _watch_pull(self, model: str, base_url: str) -> None:
        """Use Ollama's streaming API so a service-only installation can pull."""
        try:
            success = False
            async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0)) as client:
                async with client.stream("POST", f"{base_url}/api/pull", json={"name": model, "stream": True}) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        payload = json.loads(line)
                        if not isinstance(payload, dict):
                            continue
                        if payload.get("error"):
                            raise RuntimeError("Ollama reported a model download error")
                        if payload.get("status") == "success":
                            success = True
                        self._pull_state = {"state": "running", "model": model, "detail": str(payload.get("status", "Downloading"))[:240], "percent": pull_percent(payload)}
            if not success:
                raise RuntimeError("Ollama closed the download stream without success")
            self._pull_state = {"state": "completed", "model": model, "detail": "Model installed", "percent": 100}
        except asyncio.CancelledError:
            self._pull_state = {"state": "cancelled", "model": model, "detail": "Download request cancelled; Ollama may retain partial layers", "percent": None}
            raise
        except (httpx.HTTPError, ValueError, RuntimeError):
            self._pull_state = {"state": "failed", "model": model, "detail": "Ollama model download failed; check service and logs", "percent": None}

    async def cancel_pull(self) -> dict[str, Any]:
        if self._pull_task and not self._pull_task.done():
            self._pull_task.cancel()
            try:
                await self._pull_task
            except asyncio.CancelledError:
                pass
        return dict(self._pull_state)

    async def remove(self, model: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            return {"state": "confirmation_required", "model": model}
        executable = ollama_executable()
        if not executable:
            raise RuntimeError("Ollama is not installed")
        process = await asyncio.create_subprocess_exec(executable, "rm", model, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        output = (await process.communicate())[0].decode(errors="replace").strip()
        if process.returncode:
            raise RuntimeError(output or f"ollama rm exited with {process.returncode}")
        return {"state": "removed", "model": model, "detail": output}

    def semantic_pack(self) -> dict[str, Any]:
        from app.knowledge.semantic_runtime import semantic_runtime

        runtime = semantic_runtime.status()
        settings = settings_store.load().get("rag", {})
        semantic_dir = app_paths.models_dir / "semantic"
        embedding = runtime["embedding"]
        reranker = runtime["reranker"]
        installed = bool(embedding["files_present"] and reranker["files_present"])
        ready = embedding["status"] == "Ready" and reranker["status"] == "Ready"
        return {
            "state": "Ready" if ready else "Loading" if installed else "Not Installed",
            "embedding": embedding["status"],
            "reranker": reranker["status"],
            "fallback": "Active" if not ready else "Available",
            "fallback_model": "local-hash-384",
            "models": [settings.get("dense", {}).get("model", "Qwen/Qwen3-Embedding-0.6B"), settings.get("reranker", {}).get("model", "Qwen/Qwen3-Reranker-0.6B")],
            "install_path": str(semantic_dir),
            "estimated_download": ">500 MB plus optional runtime dependencies",
            "install": "not installed" if not installed else "verified" if ready else "inference verification pending",
            "runtime": runtime,
        }


local_model_manager = LocalModelManager()
