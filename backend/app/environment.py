"""Non-fatal local scientific environment detection."""

from __future__ import annotations

import importlib.util
import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.app_paths import app_paths
from app.config import LEGACY_ROOT
from app.comsol.java_bridge import java_bridge_client
from app.comsol.bridge_process import java_bridge_process_manager
from app.comsol.discovery import discover_comsol_java, scan_comsol_java
from app.knowledge.retrieval import retriever
from app.knowledge.integrity import verify_knowledge_base
from app.providers.manager import provider_manager
from app.services.model_manager import local_model_manager, ollama_executable
from app.simulation_adapters import capabilities as adapter_capabilities


class InstallationScan:
    """A read-only scan whose visible stages follow completed real checks."""

    _step_ids = ("comsol", "matlab", "wolfram", "materials_studio", "ollama_executable", "ollama_service")

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._state: dict[str, Any] = self._initial_state("idle")

    @classmethod
    def _initial_state(cls, state: str) -> dict[str, Any]:
        return {"state": state, "checked": 0, "total": None,
                "steps": [{"id": name, "state": "pending"} for name in cls._step_ids]}

    def _step(self, name: str, state: str, **detail: Any) -> None:
        # Replace the snapshot rather than mutating a response already being
        # serialized by FastAPI; no raw installation path enters the timeline.
        steps = [({**entry, "state": state, **detail} if entry["id"] == name else entry)
                 for entry in self._state["steps"]]
        self._state = {**self._state, "steps": steps}

    def status(self) -> dict[str, Any]:
        return {**self._state, "steps": [dict(step) for step in self._state["steps"]]}

    async def start(self) -> dict[str, Any]:
        if self._task and not self._task.done():
            return self.status()
        self._state = self._initial_state("running")
        self._task = asyncio.create_task(self._run())
        return self.status()

    async def _run(self) -> None:
        def before_candidate(current: int, total: int) -> None:
            self._state = {**self._state, "total": total}
            self._step("comsol", "running", current=current)

        def progress(checked: int, total: int) -> None:
            self._state = {**self._state, "checked": checked, "total": total}
        try:
            self._step("comsol", "running")
            comsol = await asyncio.to_thread(scan_comsol_java, progress, before_candidate)
            self._step("comsol", "detected" if comsol.found else "unavailable")
            # Optional-engine discovery is read-only. Its callbacks advance
            # each visible step only when that bounded check actually runs.
            step_names = {"MATLAB": "matlab", "Wolfram": "wolfram", "Materials Studio": "materials_studio"}
            def optional_progress(engine: str, state: str) -> None:
                self._step(step_names[engine], state)
            adapters = await asyncio.to_thread(adapter_capabilities, comsol, optional_progress)
            self._step("ollama_executable", "running")
            ollama = await asyncio.to_thread(ollama_executable)
            self._step("ollama_executable", "detected" if ollama else "unavailable")
            self._step("ollama_service", "running")
            try:
                service = await local_model_manager.status()
                service_ready = service.get("state") in {"Ready", "No Model"}
                self._step("ollama_service", "detected" if service_ready else "unavailable")
            except (OSError, RuntimeError, ValueError) as exc:
                # A failed optional service check cannot erase completed
                # installation evidence. Keep its failure explicit and omit
                # exception text, which may contain a private local path.
                service = {"state": "Check Failed"}
                self._step("ollama_service", "failed")
                self._state = {**self._state, "partial_failure": type(exc).__name__}
            self._state = {**self._state, "state": "completed", "comsol": comsol.as_dict(),
                           "adapters": [{key: record[key] for key in ("engine", "installed", "readback", "execute", "detail", "probe") if key in record}
                                        for record in adapters],
                           "ollama_executable": ollama, "ollama_state": service.get("state", "Unknown")}
        except Exception as exc:
            # A failed check must never leave a permanent animated RUNNING
            # state or be transformed into a success label.
            steps = [({**step, "state": "failed"} if step["state"] == "running" else step)
                     for step in self._state["steps"]]
            self._state = {**self._state, "state": "failed", "steps": steps,
                           "detail": type(exc).__name__}


installation_scan = InstallationScan()


def item(name: str, status: str, detail: str, path: str | None = None) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "path": path}


def command_version(command: str, args: list[str], timeout: float = 3) -> tuple[str | None, str]:
    path = shutil.which(command)
    if not path:
        return None, "Not detected"
    try:
        completed = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output = (completed.stdout or completed.stderr).strip().splitlines()
        return path, output[0] if output else "Detected"
    except (OSError, subprocess.SubprocessError):
        return path, "Detected; version probe failed"


def legacy_config() -> dict[str, Any]:
    path = LEGACY_ROOT / "config" / "local_paths.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def comsol_executables() -> dict[str, str]:
    found: dict[str, str] = {}
    # A user-selected installation takes precedence over old migration data.
    java_environment = discover_comsol_java()
    if java_environment.found:
        bin_dir = Path(java_environment.root) / "bin" / "win64"
        for name in ("comsol.exe", "comsolmphserver.exe", "comsolbatch.exe"):
            candidate = bin_dir / name
            if candidate.is_file():
                found[name] = str(candidate)
    config = legacy_config().get("comsol", {}).get("selected") or {}
    for name, value in (config.get("executables") or {}).items():
        if value and Path(value).exists():
            found.setdefault(name, value)
    for root in filter(None, [os.getenv("COMSOL_HOME"), r"E:\comsol", r"C:\Program Files\COMSOL"]):
        root_path = Path(root)
        for name in ("comsol.exe", "comsolmphserver.exe", "comsolbatch.exe"):
            candidate = root_path / "bin" / "win64" / name
            if candidate.exists():
                found.setdefault(name, str(candidate))
    return found


def server_reachable(host: str = "127.0.0.1", port: int = 2036) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def kb_integrity() -> dict[str, Any]:
    return verify_knowledge_base(app_paths.kb_dir)


def webview2_status() -> tuple[str, str]:
    if os.name != "nt":
        return "Warning", "WebView2 applies to Windows desktop builds"
    try:
        import winreg

        product_key = r"Software\Microsoft\EdgeUpdate\Clients\{F1E7E34A-E21B-4020-AB91-9BF2B6602DDB}"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for registry_view in (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                try:
                    with winreg.OpenKey(
                        hive,
                        product_key,
                        0,
                        winreg.KEY_READ | registry_view,
                    ) as key:
                        version = str(winreg.QueryValueEx(key, "pv")[0])
                    if version and version != "0.0.0.0":
                        return "Ready", f"WebView2 Runtime {version}"
                except OSError:
                    continue
    except (ImportError, OSError):
        pass

    # Some current-user and embedded-bootstrapper installations do not publish
    # the EdgeUpdate product key to a frozen desktop child process.  Tauri can
    # still use them, so verify the executable in Microsoft's documented install
    # locations before presenting a false warning.
    roots = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        value = os.getenv(variable)
        if value:
            roots.append(Path(value) / "Microsoft" / "EdgeWebView" / "Application")
    for root in roots:
        try:
            versions = sorted(
                (entry for entry in root.iterdir() if entry.is_dir()),
                key=lambda entry: entry.name,
                reverse=True,
            )
        except OSError:
            continue
        for version_dir in versions:
            if (version_dir / "msedgewebview2.exe").is_file():
                return "Ready", f"WebView2 Runtime {version_dir.name}"
    return "Warning", "WebView2 Runtime not detected; the official installer bootstrapper is required"


async def detect_environment() -> dict[str, Any]:
    python_ok = sys.version_info >= (3, 10)
    java_path, java_version = command_version("java", ["-version"])
    # Detection must not consume a MATLAB license or launch user software.
    matlab_path = shutil.which("matlab.exe") or shutil.which("matlab")
    matlab_version = "Executable detected; adapter unverified" if matlab_path else "Not detected"
    comsol_environment = discover_comsol_java()
    executables = comsol_executables()
    comsol_path = executables.get("comsol.exe")
    server_path = executables.get("comsolmphserver.exe")
    mcp_available = bool(importlib.util.find_spec("mcp"))
    mph_available = bool(importlib.util.find_spec("mph"))
    providers = await provider_manager.detect()
    java_bridge = await java_bridge_client.health()
    java_bridge_process = java_bridge_process_manager.status()
    any_provider = any(provider["status"] == "Ready" for provider in providers)
    try:
        knowledge = retriever.status()
    except Exception as exc:
        knowledge = {
            "status": "Warning",
            "active_mode": "unavailable",
            "indexed_units": 0,
            "compiled_units": 0,
            "basic_chunks": 0,
            "detail": f"Knowledge Base unavailable: {type(exc).__name__}: {exc}",
        }
    integrity = kb_integrity()
    webview_status, webview_detail = webview2_status()
    ollama = next((provider for provider in providers if provider.get("name") == "ollama"), None)
    entries = [
        item("Python", "Ready" if python_ok else "Error", platform.python_version(), sys.executable),
        item("Scientific Runtime", "Ready", "Packaged desktop sidecar" if app_paths.desktop_mode else "Development runtime", sys.executable),
        item("WebView2", webview_status, webview_detail),
        item("Java", "Ready" if java_path else "Warning", java_version, java_path),
        item("COMSOL", "Ready" if comsol_environment.found and comsol_path else "Warning",
             f"{comsol_environment.version} installation detected; live Bridge still requires verification" if comsol_environment.found
             else "COMSOL Model API installation not detected or incomplete", comsol_path),
        item("COMSOL Server", "Ready" if server_reachable() else "Warning", "Listening on 127.0.0.1:2036" if server_reachable() else ("Executable available; server is not running" if server_path else "Server executable not detected"), server_path),
        item("MATLAB", "Warning", matlab_version, matlab_path),
        item("MCP", "Ready" if mcp_available else "Warning", "Legacy COMSOL MCP bridge available" if mcp_available else "Optional mcp package is not installed"),
        item("MPh", "Ready" if mph_available else "Warning", "COMSOL Python automation available" if mph_available else "Optional mph package is not installed"),
        item(
            "Java Bridge",
            "Ready" if java_bridge["connected"] else "Warning",
            (
                f"{java_bridge['detail']}; {java_bridge.get('comsol_version') or comsol_environment.version}; "
                f"process {java_bridge_process.get('state', 'idle')}"
            ),
            java_bridge["base_url"],
        ),
        item(
            "Knowledge Base",
            "Warning" if integrity["status"] in {"missing", "corrupt"} else knowledge["status"],
            (
                f"{knowledge.get('indexed_units', 0)}/{knowledge.get('compiled_units', 0)} audited Units; "
                f"{knowledge.get('basic_chunks', 0)} project chunks; {knowledge.get('active_mode', 'unavailable')} mode; "
                f"integrity {integrity['status']}"
            ),
            str(app_paths.kb_dir),
        ),
        item(
            "Ollama",
            "Ready" if ollama and ollama.get("status") == "Ready" else "Warning",
            "Running on localhost" if ollama and ollama.get("status") == "Ready" else "Not installed or not running; no automatic download",
            ollama_executable(),
        ),
        item("AI Provider", "Ready" if any_provider else "Warning", "Provider ready" if any_provider else "No local AI provider detected"),
    ]
    return {
        "platform": platform.platform(),
        "items": entries,
        "providers": providers,
        "knowledge": knowledge,
        "java_bridge": java_bridge,
        "java_bridge_process": java_bridge_process,
        "comsol_java_environment": comsol_environment.as_dict(),
        "legacy_root": str(LEGACY_ROOT),
        "knowledge_base_integrity": integrity,
        "app_paths": {
            "install_dir": str(app_paths.install_dir),
            "app_data_dir": str(app_paths.app_data_dir),
            "workspace_dir": str(app_paths.workspace_dir),
            "models_dir": str(app_paths.models_dir),
            "outputs_dir": str(app_paths.outputs_dir),
            "reports_dir": str(app_paths.reports_dir),
            "logs_dir": str(app_paths.logs_dir),
            "cache_dir": str(app_paths.cache_dir),
            "kb_dir": str(app_paths.kb_dir),
            "rag_index_dir": str(app_paths.rag_index_dir),
        },
        "features": {
            "ask": "available-with-fallback" if not any_provider else "available",
            "plan": "available",
            "heat_run": "available" if comsol_environment.version == "COMSOL 6.3" else "unverified" if comsol_environment.found else "unavailable",
            "tds_run": "available" if comsol_environment.version == "COMSOL 6.3" else "unverified" if comsol_environment.found else "unavailable",
            "semantic_rag": "limited" if knowledge.get("embedding_backend") == "local-hash-384" else "available",
            "local_ai": "available" if any_provider else "unavailable",
        },
    }
