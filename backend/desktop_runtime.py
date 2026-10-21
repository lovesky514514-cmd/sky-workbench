"""Frozen and development entry point for the complete Scientific Runtime."""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.redaction import RedactingLogFilter


def _apply_environment(arguments: argparse.Namespace) -> None:
    values = {
        "COMSOL_WORKBENCH_DESKTOP_MODE": "1" if arguments.desktop else "0",
        "COMSOL_WORKBENCH_SESSION_TOKEN": arguments.session_token or os.getenv("COMSOL_WORKBENCH_SESSION_TOKEN", ""),
        "COMSOL_WORKBENCH_INSTALL_DIR": arguments.install_dir,
        "COMSOL_WORKBENCH_RESOURCE_DIR": arguments.resource_dir,
        "COMSOL_WORKBENCH_APP_DATA_DIR": arguments.app_data_dir,
        "COMSOL_WORKBENCH_WORKSPACE_DIR": arguments.workspace_dir,
        "COMSOL_WORKBENCH_KB_DIR": arguments.kb_dir,
        "COMSOL_WORKBENCH_JAVA_BRIDGE_DIR": arguments.java_bridge_dir,
    }
    for name, value in values.items():
        if value:
            os.environ[name] = value


def _configure_logging(arguments: argparse.Namespace) -> None:
    """Keep windowed frozen builds observable even when stderr is unavailable."""

    base = Path(arguments.workspace_dir or arguments.app_data_dir or Path.cwd())
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_dir / "scientific-runtime-python.log", encoding="utf-8")
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingLogFilter())


def _parent_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x00000102
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _terminate_owned_process_tree(pid: int) -> None:
    """Terminate only the explicitly supplied Workbench-owned root PID."""

    if pid <= 0 or os.name != "nt":
        return
    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logging.getLogger(__name__).exception("Failed to reclaim owned Ollama process tree pid=%s", pid)


def _watch_parent(pid: int, owned_ollama_pid: int, stop: callable) -> None:
    while _parent_alive(pid):
        time.sleep(1)
    _terminate_owned_process_tree(owned_ollama_pid)
    stop()


def _run_worker(name: str, request: Path) -> int:
    # PyInstaller's windowed child can keep the Windows ANSI code page even
    # when PYTHONIOENCODING was supplied by the parent. The line-oriented
    # WORKBENCH_EVENT/RESULT protocol is decoded as UTF-8 by ToolRegistry;
    # configure that stream explicitly before any worker emits JSON.
    if sys.stdout is None or not hasattr(sys.stdout, "reconfigure"):
        raise RuntimeError("Worker stdout protocol stream unavailable")
    sys.stdout.reconfigure(encoding="utf-8")
    if name == "heat_worker":
        from app.comsol.heat_worker import main
    elif name == "tds_worker":
        from app.comsol.tds_worker import main
    elif name == "solid_worker":
        from app.comsol.solid_worker import main
    elif name == "electrochem_worker":
        from app.comsol.electrochem_worker import main
    elif name == "battery_worker":
        from app.comsol.battery_worker import main
    elif name == "multiphysics_worker":
        from app.comsol.multiphysics_worker import main
    else:
        raise SystemExit(f"Unknown worker: {name}")
    return int(main(request))


def main() -> int:
    parser = argparse.ArgumentParser(description="COMSOL Scientific Workbench Runtime")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--session-token", default="")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--owned-ollama-pid", type=int, default=0)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--install-dir", default="")
    parser.add_argument("--resource-dir", default="")
    parser.add_argument("--app-data-dir", default="")
    parser.add_argument("--workspace-dir", default="")
    parser.add_argument("--kb-dir", default="")
    parser.add_argument("--java-bridge-dir", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--worker",
        choices=("heat_worker", "tds_worker", "solid_worker", "electrochem_worker", "battery_worker", "multiphysics_worker"),
    )
    mode.add_argument("--native-self-test-report", type=Path)
    parser.add_argument("--request", type=Path)
    arguments = parser.parse_args()
    if arguments.native_self_test_report is not None:
        # This CLI-only mode runs before settings/logging/API initialization. It cannot
        # accept worker/server context, arbitrary imports or model execution requests.
        if (arguments.desktop or arguments.request is not None or arguments.session_token
                or arguments.parent_pid or arguments.owned_ollama_pid
                or any((arguments.install_dir, arguments.resource_dir, arguments.app_data_dir,
                        arguments.workspace_dir, arguments.kb_dir, arguments.java_bridge_dir))):
            parser.error("Native diagnostics cannot be combined with Runtime or scientific execution")
        from app.diagnostics.native_selftest import run
        return run(arguments.native_self_test_report)
    _apply_environment(arguments)
    _configure_logging(arguments)
    if arguments.worker:
        if arguments.request is None:
            parser.error("--request is required with --worker")
        return _run_worker(arguments.worker, arguments.request)
    if arguments.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Scientific Runtime must stay on loopback")

    import uvicorn
    from app.main import app

    config = uvicorn.Config(
        app,
        host=arguments.host,
        port=arguments.port,
        access_log=False,
        log_level="info",
        log_config=None,
    )
    server = uvicorn.Server(config)
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    if arguments.parent_pid:
        threading.Thread(
            target=_watch_parent,
            args=(arguments.parent_pid, arguments.owned_ollama_pid, app.state.request_shutdown),
            name="workbench-parent-monitor",
            daemon=True,
        ).start()
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
