"""Lightweight local-model lifecycle; large operations require explicit confirmation."""

from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

from app.app_paths import app_paths
from app.config import settings_store


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
        self._pull_process: asyncio.subprocess.Process | None = None
        self._pull_state: dict[str, Any] = {"state": "idle", "model": "", "detail": "No download running"}

    async def status(self) -> dict[str, Any]:
        executable = shutil.which("ollama")
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
            except (httpx.HTTPError, ValueError, KeyError):
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
        executable = shutil.which("ollama")
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
        executable = shutil.which("ollama")
        if not executable:
            raise RuntimeError("Ollama is not installed")
        if self._pull_process and self._pull_process.returncode is None:
            raise RuntimeError("A model download is already running")
        self._pull_state = {"state": "running", "model": model, "detail": "Download started"}
        self._pull_process = await asyncio.create_subprocess_exec(executable, "pull", model, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        asyncio.create_task(self._watch_pull(model))
        return dict(self._pull_state)

    async def _watch_pull(self, model: str) -> None:
        assert self._pull_process is not None
        last = "Downloading"
        assert self._pull_process.stdout is not None
        async for line in self._pull_process.stdout:
            last = line.decode(errors="replace").strip()[-240:] or last
            self._pull_state = {"state": "running", "model": model, "detail": last}
        code = await self._pull_process.wait()
        self._pull_state = {"state": "completed" if code == 0 else "failed", "model": model, "detail": "Model installed" if code == 0 else f"ollama pull exited with {code}"}

    async def cancel_pull(self) -> dict[str, Any]:
        if self._pull_process and self._pull_process.returncode is None:
            self._pull_process.terminate()
            await self._pull_process.wait()
            self._pull_state = {"state": "cancelled", "model": self._pull_state.get("model", ""), "detail": "Download cancelled"}
        return dict(self._pull_state)

    async def remove(self, model: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            return {"state": "confirmation_required", "model": model}
        executable = shutil.which("ollama")
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
