"""Explicit local script runs, isolated from scientific execution verdicts."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import json
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.comsol.owned_process import OwnedProcess


MAX_SOURCE = 200_000
MAX_OUTPUT = 65_536
MAX_FIGURE = 20_000_000
RUN_SECONDS = 120
EXTENSIONS = {"Python": "py", "MATLAB": "m", "Wolfram": "wl", "Mathics3": "wl"}
SECRET_NAME = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|LICENSE|AUTH)", re.I)


def mathics_worker_path() -> Path:
    """Locate only our small connector; Mathics itself stays outside the sidecar."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "mathics_worker.py"


def _mathics_interpreter() -> str | None:
    """An explicit interpreter wins; never silently switch to Wolfram or Python."""
    configured = os.environ.get("SKY_MATHICS_PYTHON")
    if configured:
        candidate = Path(configured)
    else:
        launcher = shutil.which("mathics3")
        if not launcher:
            return None
        candidate = Path(launcher).parent / ("python.exe" if os.name == "nt" else "python3")
    if not candidate.is_file() or not mathics_worker_path().is_file():
        return None
    try:
        check = subprocess.run(
            [str(candidate), "-I", "-c", "from mathics.session import MathicsSession"],
            env=child_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return str(candidate) if check.returncode == 0 else None


def image_kind(name: str, data: bytes) -> str | None:
    """Reject active formats and files whose header disagrees with the suffix."""
    suffix = Path(name).suffix.lower()
    if suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if suffix == ".webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def command_for(engine: str, executable: str, filename: str) -> list[str]:
    """Use argument arrays and fixed, workspace-relative filenames; no shell."""
    if engine == "Python" and filename == "main.py":
        return [executable, "-I", filename]
    if engine == "Wolfram" and filename == "main.wl" and "wolframscript" in Path(executable).name.lower():
        return [executable, "-file", filename, "-local"]
    if engine == "MATLAB" and filename == "main.m":
        return [executable, "-batch", "run('main.m')"]
    if engine == "Mathics3" and filename == "main.wl":
        return [executable, "-I", str(mathics_worker_path()), filename]
    raise ValueError("No supported local script runner for this engine")


def executable_for(engine: str) -> str | None:
    if engine == "Mathics3":
        return _mathics_interpreter()
    if engine == "Python":
        return shutil.which("python.exe") or shutil.which("python")
    if engine not in ("MATLAB", "Wolfram"):
        return None
    from app.simulation_adapters import capabilities

    record = next((item for item in capabilities() if item["engine"] == engine), None)
    if record and record["probe"] == "AVAILABLE_ON_REQUEST":
        return str(record["executable"])
    return None


def child_environment() -> dict[str, str]:
    """Do not pass desktop/API credentials to vendor or user-authored scripts."""
    return {name: value for name, value in os.environ.items() if not SECRET_NAME.search(name)}


@dataclass
class ScriptRun:
    run_id: str
    engine: str
    workspace: tempfile.TemporaryDirectory[str]
    state: str = "PENDING"
    output: str = ""
    elapsed_ms: int = 0
    figures: list[dict[str, str | int]] = field(default_factory=list)
    mathics_result: dict[str, object] | None = None
    task: asyncio.Task[None] | None = None
    owned: OwnedProcess | None = None

    def public(self) -> dict[str, object]:
        return {
            "run_id": self.run_id, "engine": self.engine, "state": self.state,
            "output": self.output, "elapsed_ms": self.elapsed_ms, "figures": self.figures,
            "mathics_result": self.mathics_result,
            "script_exit_success": self.state == "COMPLETED",
            "model_state_verified": False, "scientific_validation_passed": False,
        }


class ScriptRunManager:
    """Own one explicit script process and its temporary, non-scientific outputs."""

    def __init__(self, root: Path, resolver: Callable[[str], str | None] = executable_for):
        self.root = root
        self.resolver = resolver
        self.runs: dict[str, ScriptRun] = {}
        self._start_lock = asyncio.Lock()

    def availability(self) -> dict[str, bool]:
        available: dict[str, bool] = {}
        for name in (*EXTENSIONS, "Materials Studio"):
            try:
                available[name] = bool(self.resolver(name))
            except OSError:
                # An inaccessible vendor installation must not hide a different,
                # independently installed local interpreter.
                available[name] = False
        return available

    async def start(self, engine: str, source: str) -> dict[str, object]:
        if engine not in EXTENSIONS or not source.strip() or len(source) > MAX_SOURCE or "\x00" in source:
            raise ValueError("Unsupported engine or invalid script source")
        async with self._start_lock:
            if any(run.state in ("PENDING", "RUNNING") for run in self.runs.values()):
                raise RuntimeError("A local script is already running")
            executable = await asyncio.to_thread(self.resolver, engine)
            if not executable:
                raise FileNotFoundError("Local script engine unavailable")
            self.root.mkdir(parents=True, exist_ok=True)
            self._prune()
            workspace = tempfile.TemporaryDirectory(prefix="script-", dir=self.root)
            run = ScriptRun(str(uuid.uuid4()), engine, workspace)
            self.runs[run.run_id] = run
            run.task = asyncio.create_task(self._execute(run, source, executable))
            return run.public()

    async def _execute(self, run: ScriptRun, source: str, executable: str) -> None:
        started = time.perf_counter()
        path = Path(run.workspace.name)
        filename = f"main.{EXTENSIONS[run.engine]}"
        try:
            (path / filename).write_text(source, encoding="utf-8")
            if run.state == "CANCELED":
                return
            run.state = "RUNNING"
            run.owned = await OwnedProcess.spawn(
                command_for(run.engine, executable, filename), cwd=str(path), env=child_environment()
            )
            output = bytearray()

            async def collect() -> None:
                assert run.owned is not None
                while chunk := await run.owned.process.stdout.read(4096):
                    output.extend(chunk)
                    if len(output) > MAX_OUTPUT:
                        raise OverflowError("Output exceeded the local display limit")
                await run.owned.process.wait()

            try:
                await asyncio.wait_for(collect(), RUN_SECONDS)
                run.state = "COMPLETED" if run.owned.process.returncode == 0 else "FAILED"
            except asyncio.TimeoutError:
                run.state = "TIMED_OUT"
            except OverflowError:
                run.state = "OUTPUT_LIMIT"
            message = output[:MAX_OUTPUT].decode("utf-8", errors="replace")
            if run.state == "FAILED" and re.search(r"\b(?:licen[sc]e|activation)\b", message, re.I):
                message = "The local engine requires a valid license or activation."
            run.output = message.replace(str(path), "<script workspace>")
            if run.engine == "Mathics3" and run.state in ("COMPLETED", "FAILED"):
                try:
                    result_file = path / "mathics_result.json"
                    if result_file.is_symlink() or result_file.stat().st_size > MAX_OUTPUT:
                        raise ValueError("Invalid Mathics result")
                    result = json.loads(result_file.read_text(encoding="utf-8"))
                    if (result.get("status") not in ("success", "failed") or not isinstance(result.get("text"), str)
                            or not isinstance(result.get("latex"), str)
                            or not isinstance(result.get("mathml"), str)
                            or not isinstance(result.get("messages"), list)):
                        raise ValueError("Invalid Mathics result")
                    if (run.state == "COMPLETED") != (result["status"] == "success"):
                        raise ValueError("Mathics result disagrees with worker exit")
                    run.mathics_result = result
                except (OSError, ValueError, TypeError, AttributeError):
                    run.state = "FAILED"
                    run.output = "The Mathics worker did not return a valid result."
        except asyncio.CancelledError:
            run.state = "CANCELED"
            raise
        except (OSError, RuntimeError, ValueError):
            run.state = "FAILED"
            run.output = "The local script engine could not start or write its workspace."
        finally:
            if run.owned is not None:
                cleanup = await run.owned.terminate()
                if not cleanup.get("reaped"):
                    run.state = "CLEANUP_UNVERIFIED"
            run.elapsed_ms = round((time.perf_counter() - started) * 1000)
            if run.state == "COMPLETED":
                run.figures = self._figures(path)

    def _figures(self, root: Path) -> list[dict[str, str | int]]:
        found: list[dict[str, str | int]] = []
        for path in sorted(root.iterdir()):
            if len(found) == 20:
                break
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FIGURE:
                continue
            data = path.read_bytes()
            kind = image_kind(path.name, data)
            if kind:
                found.append({"name": path.name, "mime": kind, "size": len(data),
                              "sha256": hashlib.sha256(data).hexdigest()})
        return found

    def status(self, run_id: str) -> dict[str, object]:
        return self._get(run_id).public()

    async def wait(self, run_id: str) -> None:
        task = self._get(run_id).task
        if task:
            with suppress(asyncio.CancelledError):
                await task

    async def cancel(self, run_id: str) -> dict[str, object]:
        run = self._get(run_id)
        if run.state in ("PENDING", "RUNNING") and run.task:
            run.state = "CANCELED"
            run.task.cancel()
            await self.wait(run_id)
        return run.public()

    def figure(self, run_id: str, name: str) -> tuple[str, bytes]:
        run = self._get(run_id)
        record = next((item for item in run.figures if item["name"] == name), None)
        if not record or run.state != "COMPLETED":
            raise FileNotFoundError("Figure unavailable")
        path = Path(run.workspace.name) / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FIGURE:
            raise FileNotFoundError("Figure unavailable")
        data = path.read_bytes()
        if image_kind(name, data) != record["mime"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise FileNotFoundError("Figure integrity lost")
        return str(record["mime"]), data

    async def shutdown(self) -> None:
        for run_id in list(self.runs):
            await self.cancel(run_id)
        for run in self.runs.values():
            run.workspace.cleanup()
        self.runs.clear()

    def _get(self, run_id: str) -> ScriptRun:
        try:
            return self.runs[str(uuid.UUID(run_id))]
        except (KeyError, ValueError) as exc:
            raise KeyError("Unknown local script run") from exc

    def _prune(self) -> None:
        for run_id in list(self.runs)[:-9]:
            run = self.runs[run_id]
            if run.state not in ("PENDING", "RUNNING"):
                run.workspace.cleanup()
                del self.runs[run_id]
