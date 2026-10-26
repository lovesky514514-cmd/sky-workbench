"""Bounded lifecycle manager for the real COMSOL Java Bridge process."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock, Event
from typing import Any

from app.comsol.discovery import ComsolJavaEnvironment, comsol_java_environment, discover_comsol_java
from app.comsol.bridge_auth import BRIDGE_TOKEN_ENV, BRIDGE_TOKEN_HEADER, new_bridge_token
from app.config import CACHE_ROOT, JAVA_BRIDGE_ROOT, PROJECT_ROOT, WORKSPACE_ROOT, settings_store
from app.session.store import session_store
from app.comsol.owned_process import OwnedProcess, attach_windows_job


SOURCE = JAVA_BRIDGE_ROOT / "src" / "main" / "java" / "workbench" / "bridge" / "ComsolBridgeServer.java"
BUILD = CACHE_ROOT / "java_bridge_build11"
LOG_ROOT = WORKSPACE_ROOT / "logs"


def bridge_build_dir(environment: ComsolJavaEnvironment) -> Path:
    """Key compiled classes to the exact installed API jar's local identity."""
    jar = Path(environment.api_jar)
    identity = f"{environment.root}|{environment.version}|{jar.name}|{jar.stat().st_size}|{jar.stat().st_mtime_ns}"
    return BUILD / hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


class JavaBridgeProcessManager:
    """Start one standalone COMSOL-backed Bridge; never touch unrelated processes."""

    def __init__(self, max_events: int = 100) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._sequence = 0
        self._stdout = None
        self._stderr = None
        self._stop_requested = Event()
        self._owner_id = None
        self._job = None
        self._auth_token: str | None = None
        self._state: dict[str, Any] = {
            "state": "idle",
            "mode": "standalone",
            "base_url": self._configured_url(),
            "environment": comsol_java_environment.as_dict(),
        }

    def _configured_url(self) -> str:
        desktop_url = os.getenv("COMSOL_WORKBENCH_JAVA_BRIDGE_URL", "").strip()
        if desktop_url:
            return desktop_url.rstrip("/")
        if os.getenv("COMSOL_WORKBENCH_DESKTOP_MODE") == "1":
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            desktop_url = f"http://127.0.0.1:{port}"
            os.environ["COMSOL_WORKBENCH_JAVA_BRIDGE_URL"] = desktop_url
            return desktop_url
        config = settings_store.load().get("comsol", {}).get("java_bridge", {})
        return str(config.get("base_url", "http://127.0.0.1:8765")).rstrip("/")

    async def start(self, model_path: str | None = None, *, owner_id: str | None = None) -> dict[str, Any]:
        selected = self._validate_model_path(model_path)
        selected_owner = owner_id or 'bridge'
        session_store.acquire_bridge_owner(selected_owner)
        self._owner_id = selected_owner
        if self._task and not self._task.done():
            if self._stop_requested.is_set(): raise RuntimeError('Bridge stop in progress')
            return self.status()
        self._stop_requested.clear()
        if self._process and self._process.poll() is None:
            current = Path(str(self._state.get("model_path", ""))).resolve()
            if current == selected:
                return self.status()
            await asyncio.to_thread(self._stop_sync)
        if self._process:
            await asyncio.to_thread(self._stop_sync)
        environment = discover_comsol_java()
        self._state = {
            "state": "starting",
            "mode": "standalone",
            "model_path": str(selected),
            "base_url": self._configured_url(),
            "environment": environment.as_dict(),
            "started_at": _now(),
        }
        self._append("command", "bridge", "starting", f"Starting real COMSOL Java Bridge for {selected.name}")
        self._task = asyncio.create_task(self._run_start(selected), name="real-comsol-java-bridge-start")
        return self.status()

    async def stop(self) -> dict[str, Any]:
        self._stop_requested.set()
        await asyncio.to_thread(self._stop_sync)
        # Do not cancel to_thread: its synchronous compile/start would keep running.
        if self._task and not self._task.done():
            await self._task
        if self._owner_id == 'bridge': session_store.release_bridge_owner('bridge')
        self._owner_id = None
        self._state = {
            **self._state,
            "state": "stopped",
            "stopped_at": _now(),
            "pid": None,
        }
        self._append("command", "bridge", "stopped", "Workbench-owned Java Bridge stopped")
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            running = bool(process and process.poll() is None)
            state = dict(self._state)
            if process and not running and state.get("state") == "ready":
                self._auth_token = None
                state.update({"state": "crashed", "returncode": process.returncode, "pid": None})
                self._state = dict(state)
                self._append("critical_failure", "bridge", "crashed", f"Java Bridge exited with code {process.returncode}")
            return {
                **state,
                "running": running,
                "owner_id": self._owner_id if running else None,
                "pid": process.pid if running else state.get("pid"),
                "latest_sequence": self._sequence,
            }

    def events_after(self, sequence: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events if int(event["sequence"]) > sequence]

    def request_token(self) -> str | None:
        """Expose the credential only to the in-process Python HTTP client."""
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                return None
            return self._auth_token

    async def _run_start(self, model_path: Path) -> None:
        try:
            result = await asyncio.to_thread(self._start_sync, model_path)
            self._state = {**self._state, **result, "state": "ready", "ready_at": _now()}
            self._append("model_change", "bridge", "ready", "Real COMSOL ModelUtil and model connected")
        except asyncio.CancelledError:
            self._stop_requested.set()
            await asyncio.to_thread(self._stop_sync)
            raise
        except Exception as exc:
            await asyncio.to_thread(self._stop_sync)
            if self._owner_id == 'bridge':
                session_store.release_bridge_owner('bridge')
                self._owner_id = None
            detail = f"{type(exc).__name__}: {exc}"
            self._state = {**self._state, "state": "failed", "error": detail, "failed_at": _now(), "pid": None}
            self._append("critical_failure", "bridge", "error", detail)

    def _start_sync(self, model_path: Path) -> dict[str, Any]:
        if self._stop_requested.is_set(): raise RuntimeError('Bridge startup canceled')
        # Re-discover on each start: settings may point to a different licensed
        # COMSOL installation since Runtime launch. Never reuse a class that
        # was compiled against another installation's API jar.
        environment = discover_comsol_java()
        if not environment.found:
            raise RuntimeError("REAL_COMSOL_JAVA_CLASSPATH_NOT_FOUND")
        compile_started = time.perf_counter()
        build = bridge_build_dir(environment)
        class_file = build / "workbench" / "bridge" / "ComsolBridgeServer.class"
        build.mkdir(parents=True, exist_ok=True)
        if not class_file.is_file() or class_file.stat().st_mtime_ns < SOURCE.stat().st_mtime_ns:
            compiled = subprocess.run(
                [environment.javac, "-encoding", "UTF-8", "-d", str(build), str(SOURCE)],
                cwd=JAVA_BRIDGE_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
                # javac is another console executable. Preserve diagnostics in
                # captured streams without opening a transient desktop window.
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if compiled.returncode:
                raise RuntimeError(f"Java Bridge compile failed: {compiled.stderr[-4000:]}")
        compile_ms = round((time.perf_counter() - compile_started) * 1000, 3)

        base_url = self._configured_url()
        host, port = _host_port(base_url)
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("Java Bridge base_url must stay on loopback")
        if not _port_free(port):
            raise RuntimeError(f"Java Bridge port {port} is already in use")

        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        stdout_path = LOG_ROOT / "java_bridge.stdout.log"
        stderr_path = LOG_ROOT / "java_bridge.stderr.log"
        progress_path = LOG_ROOT / f"java_bridge_progress_{int(time.time() * 1000)}.log"
        self._stdout = stdout_path.open("a", encoding="utf-8")
        self._stderr = stderr_path.open("a", encoding="utf-8")
        native_path = ";".join(environment.native_libraries)
        classpath = f"{build};{Path(environment.root) / 'plugins' / '*'}"
        poll_ms = int(settings_store.load().get("comsol", {}).get("java_bridge", {}).get("poll_ms", 1000))
        command = [
            environment.java,
            f"-Djava.library.path={native_path}",
            f"-Dcs.root={environment.root}",
            "-cp",
            classpath,
            "workbench.bridge.ComsolBridgeServer",
            "--mode",
            "standalone",
            "--port",
            str(port),
            "--model-tag",
            "workbench_heat_live",
            "--model-path",
            str(model_path),
            "--poll-ms",
            str(max(500, min(2000, poll_ms))),
            "--progress-log",
            str(progress_path),
        ]
        creationflags = (subprocess.CREATE_NO_WINDOW | 0x4) if os.name == "nt" else 0
        cold_started = time.perf_counter()
        token = new_bridge_token()
        child_env = _bridge_child_environment(token)
        with self._lock:
            if self._stop_requested.is_set(): raise RuntimeError('Bridge startup canceled')
            self._auth_token = token
            try:
                self._process = subprocess.Popen(command, cwd=JAVA_BRIDGE_ROOT, env=child_env,
                    stdin=subprocess.DEVNULL, stdout=self._stdout, stderr=self._stderr,
                    text=True, creationflags=creationflags)
            finally:
                # The child owns its inherited copy; no temporary token enters os.environ.
                child_env.pop(BRIDGE_TOKEN_ENV, None)
            if os.name == 'nt': self._job = attach_windows_job(self._process.pid)
            session_store.record_bridge_process(self._owner_id, OwnedProcess.identify(self._process.pid))
            if self._owner_id and self._owner_id != 'bridge':
                run = session_store.get_run(self._owner_id)
                session_store.update_run(self._owner_id, run['attempt_id'], worker=OwnedProcess.identify(self._process.pid), cleanup={'reaped': False, 'status': 'bridge_owned'})
        deadline = time.monotonic() + 90
        health: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            if self._stop_requested.is_set(): raise RuntimeError('Bridge startup canceled')
            if self._process.poll() is not None:
                raise RuntimeError(f"Java Bridge exited during startup with code {self._process.returncode}")
            try:
                health = _json_get(f"{base_url}/health", timeout=2, token=token)
                if health.get("comsol_api_loaded") and health.get("model_connected"):
                    break
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(0.2)
        if not health or not health.get("model_connected"):
            raise RuntimeError(f"Real COMSOL model connection timed out: {health}")
        cold_start_ms = round((time.perf_counter() - cold_started) * 1000, 3)
        return {
            "pid": self._process.pid,
            "base_url": base_url,
            "model_path": str(model_path),
            "model_tag": health.get("model_tag"),
            "comsol_version": health.get("comsol_version"),
            "connection_mode": health.get("connection_mode"),
            "detection_method": health.get("detection_method"),
            "progress_log": str(progress_path),
            "compile_ms": compile_ms,
            "cold_start_ms": cold_start_ms,
        }

    def _stop_sync(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        process = self._process
        if self._job:
            import ctypes
            from app.comsol.owned_process import kernel, Accounting, _check
            _check(kernel.TerminateJobObject(self._job, 1))
            deadline = time.monotonic() + 3
            while True:
                accounting = Accounting()
                _check(kernel.QueryInformationJobObject(self._job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
                if not accounting.active: break
                if time.monotonic() >= deadline: raise RuntimeError('Bridge descendants orphaned; ownership retained')
                time.sleep(0.01)
            kernel.CloseHandle(self._job)
            self._job = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process and self._owner_id and self._owner_id != 'bridge':
            run = session_store.get_run(self._owner_id)
            session_store.update_run(self._owner_id, run['attempt_id'], cleanup={'reaped': process.poll() is not None, 'status': 'bridge_shutdown'})
        self._process = None
        self._auth_token = None
        for handle_name in ("_stdout", "_stderr"):
            handle = getattr(self, handle_name)
            if handle:
                handle.close()
                setattr(self, handle_name, None)

    def _validate_model_path(self, value: str | None) -> Path:
        models_root = (WORKSPACE_ROOT / "models").resolve()
        if value:
            selected = Path(value).expanduser().resolve()
        else:
            candidates = sorted(models_root.glob("*.mph"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
            if not candidates:
                raise FileNotFoundError("No verified Workbench MPH model is available")
            selected = candidates[0].resolve()
        selected.relative_to(models_root)
        if selected.suffix.lower() != ".mph" or not selected.is_file():
            raise ValueError("Bridge model must be an existing .mph under workspace/models")
        return selected

    def _append(self, event_type: str, phase: str, status: str, detail: str) -> None:
        with self._lock:
            self._sequence += 1
            self._events.append(
                {
                    "sequence": self._sequence,
                    "event_type": event_type,
                    "phase": phase,
                    "status": status,
                    "detail": detail,
                    "created_at": _now(),
                }
            )


def _host_port(base_url: str) -> tuple[str, int]:
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.scheme != "http" or not parsed.hostname or not parsed.port:
        raise ValueError("Java Bridge base_url must be an explicit loopback http URL with a port")
    return parsed.hostname, parsed.port


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _json_get(url: str, timeout: float, token: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={BRIDGE_TOKEN_HEADER: token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Bridge response must be a JSON object")
    return payload


def _bridge_child_environment(token: str) -> dict[str, str]:
    """Pass only OS launch essentials and this bridge credential to the Java child."""
    names = ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE",
             "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA")
    child_env = {name: os.environ[name] for name in names if name in os.environ}
    child_env[BRIDGE_TOKEN_ENV] = token
    return child_env


def _now() -> str:
    return datetime.now(UTC).isoformat()


java_bridge_process_manager = JavaBridgeProcessManager()
