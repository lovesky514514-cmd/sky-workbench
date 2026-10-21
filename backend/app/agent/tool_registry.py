"""Guarded tool execution with retries, alternatives and bounded logs."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import re
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import BACKEND_ROOT, PROJECT_ROOT, WORKSPACE_ROOT
from app.comsol.owned_process import OwnedProcess
from app.session.run_journal import RunCanceled, StaleAttempt

_executing_registry = ContextVar('executing_registry', default=None)


def worker_process_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Keep the line-oriented worker protocol UTF-8 on every Windows locale."""

    environment = dict(os.environ if base is None else base)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


ToolExecutor = Callable[[dict[str, Any], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class RegisteredTool:
    name: str
    description: str
    risk: str
    executor: ToolExecutor
    alternative: str | None = None


class ToolRegistry:
    """Own executable capabilities; the LLM never executes tools directly."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}
        self._owned: dict[str, OwnedProcess] = {}
        self.sessions = None

    def register(self, tool: RegisteredTool) -> None:
        self._tools[tool.name] = tool

    def catalog(self) -> list[dict[str, str | None]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "risk": tool.risk,
                "alternative": tool.alternative,
            }
            for tool in self._tools.values()
        ]

    def process_ids(self, session_id: str) -> dict[str, int]:
        process = self._active_processes.get(session_id)
        return {"comsol_worker": process.pid} if process and process.returncode is None else {}

    async def cancel(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        process = self._active_processes.get(session_id)
        if process is None or process.returncode is not None:
            return {"cancelled": False, "status": "not_running", "detail": "No active COMSOL worker"}
        if not force:
            return {
                "cancelled": False,
                "status": "safe_cancel_unavailable",
                "detail": "This isolated COMSOL API workflow exposes no documented in-process solver cancel operation; the process was not killed",
            }
        owned = self._owned.get(session_id)
        if owned is None or owned.process is not process:
            return {'cancelled': False, 'status': 'ownership_unverified'}
        cleanup = await owned.terminate()
        return {
            "cancelled": cleanup['reaped'],
            "status": "force_terminated" if cleanup['reaped'] else 'orphaned',
            "cleanup": cleanup,
            "detail": "Advanced force termination was explicitly requested; unsaved COMSOL state may be lost",
        }

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
        retries: int = 1,
    ) -> dict[str, Any]:
        tool = self._tools[name]
        if arguments.get('attempt_id') and retries:
            raise ValueError('Journaled attempts may only be retried by Runtime')
        started = time.perf_counter()
        errors: list[str] = []
        for attempt in range(retries + 1):
            try:
                if self.sessions and arguments.get('attempt_id'):
                    self.sessions.assert_current(arguments['run_id'], arguments['attempt_id'])
                token = _executing_registry.set(self)
                try:
                    result = await tool.executor(arguments, on_event)
                finally:
                    _executing_registry.reset(token)
                if arguments.get('attempt_id'):
                    if result.get('attempt_id', arguments['attempt_id']) != arguments['attempt_id']:
                        raise StaleAttempt('worker attempt identity mismatch')
                    result['attempt_id'] = arguments['attempt_id']
                result["tool"] = name
                result["attempt"] = attempt + 1
                result["duration_ms"] = round((time.perf_counter() - started) * 1000)
                result["fallback"] = attempt > 0
                return result
            except Exception as exc:
                if isinstance(exc, (RunCanceled, StaleAttempt)): raise
                if self.sessions and arguments.get('attempt_id'):
                    self.sessions.assert_current(arguments['run_id'], arguments['attempt_id'])
                errors.append(f"{type(exc).__name__}: {exc}")
                if attempt < retries:
                    await on_event(
                        {
                            "phase": "Repair",
                            "status": "running",
                            "label": "Analyzing tool failure and retrying in a clean process",
                            "fallback": True,
                            "output_summary": errors[-1],
                        }
                    )
                    await asyncio.sleep(0.2)
        raise RuntimeError("; ".join(errors))


async def run_heat_worker(
    arguments: dict[str, Any],
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
) -> dict[str, Any]:
    return await run_comsol_worker(arguments, on_event, "heat_worker.py")


async def run_tds_worker(
    arguments: dict[str, Any],
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
) -> dict[str, Any]:
    return await run_comsol_worker(arguments, on_event, "tds_worker.py")


async def run_solid_worker(
    arguments: dict[str, Any],
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
) -> dict[str, Any]:
    return await run_comsol_worker(arguments, on_event, "solid_worker.py")


async def run_electrochem_worker(
    arguments: dict[str, Any],
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
) -> dict[str, Any]:
    return await run_comsol_worker(arguments, on_event, "electrochem_worker.py")


async def run_battery_worker(
    arguments: dict[str, Any],
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
) -> dict[str, Any]:
    return await run_comsol_worker(arguments, on_event, "battery_worker.py")


async def run_multiphysics_worker(
    arguments: dict[str, Any],
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
) -> dict[str, Any]:
    return await run_comsol_worker(arguments, on_event, "multiphysics_worker.py")


async def run_comsol_worker(
    arguments: dict[str, Any],
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
    worker_name: str,
) -> dict[str, Any]:
    run_id = str(arguments["run_id"])
    attempt_id = str(arguments.get('attempt_id') or 'legacy-' + str(time.time_ns()))
    if not all(re.fullmatch(r'[A-Za-z0-9-]{1,100}', value) for value in (run_id, attempt_id)):
        raise ValueError('Invalid run/attempt identity')
    registry = _executing_registry.get() or tool_registry
    run_dir = WORKSPACE_ROOT / "past_runs" / run_id / attempt_id
    run_dir.mkdir(parents=True, exist_ok=False)
    # Existing scientific workers are untouched; their workspace is attempt-scoped.
    attempt_workspace = WORKSPACE_ROOT / 'models' / 'attempts' / run_id / attempt_id
    attempt_workspace.mkdir(parents=True, exist_ok=False)
    arguments = {**arguments, 'workspace': str(attempt_workspace), 'attempt_id': attempt_id}
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps(arguments, ensure_ascii=False, indent=2), encoding="utf-8")
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--worker", worker_name.removesuffix(".py"), "--request", str(request_path)]
    else:
        command = [sys.executable, str(BACKEND_ROOT / "app" / "comsol" / worker_name), "--request", str(request_path)]
    if registry.sessions: registry.sessions.assert_current(run_id, attempt_id)
    owned = await OwnedProcess.spawn(
        command, cwd=str(PROJECT_ROOT), env=worker_process_environment(),
    )
    process = owned.process
    session_id = str(arguments.get("session_id", ""))
    if session_id:
        registry._active_processes[session_id] = process
        registry._owned[session_id] = owned
    final: dict[str, Any] | None = None
    log_parts: list[str] = []
    assert process.stdout is not None
    try:
        if registry.sessions:
            registry.sessions.update_run(run_id, attempt_id, worker=owned.identity)
            registry.sessions.assert_current(run_id, attempt_id)
        while line_bytes := await process.stdout.readline():
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            # Preserve the complete worker protocol for post-run auditing. The UI
            # receives parsed summaries, while this file remains the raw record.
            log_parts.append(line)
            if line.startswith("WORKBENCH_EVENT:"):
                try:
                    event = json.loads(line.removeprefix("WORKBENCH_EVENT:"))
                    if event.get('attempt_id', attempt_id) != attempt_id or event.get('run_id', run_id) != run_id:
                        raise StaleAttempt('worker event identity mismatch')
                    await on_event({**event, 'run_id': run_id, 'attempt_id': attempt_id})
                except json.JSONDecodeError:
                    continue
            elif line.startswith("WORKBENCH_RESULT:"):
                final = json.loads(line.removeprefix("WORKBENCH_RESULT:"))
        exit_code = await process.wait()
    finally:
        cleanup = await owned.terminate()
        if registry.sessions:
            registry.sessions.update_run(run_id, attempt_id, cleanup=cleanup, artifacts=(final or {}).get('artifacts', {}))
        if session_id and registry._active_processes.get(session_id) is process:
            registry._active_processes.pop(session_id, None)
            registry._owned.pop(session_id, None)
    raw_log = "\n".join(log_parts)
    log_path = run_dir / 'worker.log'
    log_path.write_text(raw_log, encoding="utf-8")
    if exit_code != 0 or not final or not final.get("success"):
        error = (final or {}).get("error") or f"COMSOL worker exited with code {exit_code}"
        raise RuntimeError(f"{error}. Detailed log: {log_path}")
    final["raw_log"] = raw_log[-12_000:]
    final["log_path"] = str(log_path)
    final['attempt_id'] = attempt_id
    return final


tool_registry = ToolRegistry()
tool_registry.register(
    RegisteredTool(
        name="comsol.heat_transfer_2d",
        description="Build, calibrate, solve, validate and export a 2D stationary heat-transfer model.",
        risk="project-write",
        executor=run_heat_worker,
        alternative="fresh-process COMSOL Java API fallback",
    )
)
tool_registry.register(
    RegisteredTool(
        name="comsol.lithium_ion_battery_baseline",
        description="Build, read back, solve and scientifically validate a real Battery Design Module lithium-ion discharge baseline.",
        risk="project-write",
        executor=run_battery_worker,
        alternative="report MODULE_MISSING or FAILED; never substitute a generic electrochemistry or mock result",
    )
)
tool_registry.register(
    RegisteredTool(
        name="comsol.heat_tds_temperature_coupling",
        description="Solve and compare real coupling OFF/ON Heat to TDS models with D(T), readback and analytical validation.",
        risk="project-write",
        executor=run_multiphysics_worker,
        alternative="fail safely; no uncoupled or analytical-only substitute is permitted",
    )
)
tool_registry.register(
    RegisteredTool(
        name="comsol.electrochem_primary_current",
        description="Solve and validate a real Primary Current Distribution benchmark after checking the Electrochemistry Module license.",
        risk="project-write",
        executor=run_electrochem_worker,
        alternative="report MODULE_MISSING or FAILED; never substitute a mock field",
    )
)
tool_registry.register(
    RegisteredTool(
        name="comsol.solid_mechanics_uniaxial_bar",
        description="Build, read back, solve and independently validate a real 3D uniaxial Solid Mechanics bar.",
        risk="project-write",
        executor=run_solid_worker,
        alternative="fail safely; no analytical or mock substitute is permitted",
    )
)
tool_registry.register(
    RegisteredTool(
        name="comsol.tds_2d_stationary_diffusion",
        description="Build, read back, solve, scientifically validate and export a real 2D stationary TDS pure-diffusion model.",
        risk="project-write",
        executor=run_tds_worker,
        alternative="fail safely; no analytical or mock substitute is permitted",
    )
)
