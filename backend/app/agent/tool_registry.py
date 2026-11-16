"""Guarded tool execution with retries, alternatives and bounded logs."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import sys
import time
import re
import sqlite3
import subprocess
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import BACKEND_ROOT, PROJECT_ROOT, WORKSPACE_ROOT
from app.comsol.owned_process import OwnedProcess
from app.comsol.worker_protocol import receive_control, resolve_worker_result
from app.comsol.worker_lease import watch_worker
from app.comsol.view_checkpoint import CHECKPOINT_EVENT, checkpoint_data
from app.execution.files import reject_reparse
from app.session.run_journal import RunCanceled, StaleAttempt
from app.recovery.ingest import Signal, capture
from app.recovery.schemas import Cause, Origin

_executing_registry = ContextVar('executing_registry', default=None)
_logger = logging.getLogger(__name__)


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
            raise ValueError('Journaled attempts require an explicit controller decision; tool retries are forbidden')
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
                if self.sessions and arguments.get('run_id') and arguments.get('attempt_id'):
                    try:
                        run = self.sessions.get_run(arguments['run_id'])
                    except (KeyError, OSError, sqlite3.Error):
                        run = None
                    if run:
                        code = (Cause.EXPERT_STALE if isinstance(exc, StaleAttempt) else
                                Cause.RESOURCE if isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.ENOMEM}
                                else Cause.TOOL_PERMISSION if isinstance(exc, PermissionError) else 'worker_failed')
                        if not isinstance(exc, RunCanceled):
                            capture(self.sessions, Signal(Origin.TOOL, 'tool_execution', code),
                                    session_id=run['session_id'], run_id=run['run_id'],
                                    attempt_id=arguments['attempt_id'])
                if isinstance(exc, (RunCanceled, StaleAttempt)): raise
                if self.sessions and arguments.get('attempt_id'):
                    self.sessions.assert_current(arguments['run_id'], arguments['attempt_id'])
                if arguments.get('attempt_id'):
                    # 科研尝试不在工具层重试，也不丢失原异常上的提交状态信号。
                    raise RuntimeError(f'{type(exc).__name__}: {exc}') from exc
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


async def read_saved_heat(arguments, on_event):
    return await read_saved_model(arguments, on_event, 'heat_readback_worker.py')


async def read_saved_cooling(arguments, on_event):
    return await read_saved_model(arguments, on_event, 'cooling_readback_worker.py')


async def read_saved_model(arguments, on_event, worker_name):
    """只加载当前尝试账本登记的模型，不接受任意模型路径。"""
    import hashlib
    registry = _executing_registry.get() or tool_registry
    if registry.sessions is None:
        raise PermissionError('journal_required_for_independent_readback')
    registry.sessions.assert_current(arguments['run_id'], arguments['attempt_id'])
    run = registry.sessions.get_run(arguments['run_id'])
    if run['session_id'] != arguments['session_id']:
        raise PermissionError('readback_session_mismatch')
    original = Path(run['artifacts']['model'])
    attempt_root = WORKSPACE_ROOT / 'models' / 'attempts' / run['run_id'] / run['attempt_id']
    reject_reparse(original)
    reject_reparse(attempt_root)
    model = original.resolve(strict=True)
    model.relative_to(attempt_root.resolve())
    return await run_comsol_worker({**arguments, 'model_path': str(model),
        'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest()}, on_event, worker_name)


async def run_cooling_worker(arguments, on_event):
    return await run_comsol_worker(arguments, on_event, "cooling_worker.py")


async def run_transient_diffusion_worker(arguments, on_event):
    return await run_comsol_worker(arguments, on_event, 'transient_diffusion_worker.py')


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
    from app.scientific.schemas import ScientificPlan
    from app.scientific.solver_gate import require_solver_inputs

    require_solver_inputs(ScientificPlan.model_validate(arguments['plan']))
    attempt_id = str(arguments.get('attempt_id') or 'legacy-' + str(time.time_ns()))
    if not all(re.fullmatch(r'[A-Za-z0-9-]{1,100}', value) for value in (run_id, attempt_id)):
        raise ValueError('Invalid run/attempt identity')
    registry = _executing_registry.get() or tool_registry
    read_only = worker_name in {'heat_readback_worker.py', 'cooling_readback_worker.py'}
    run_dir = WORKSPACE_ROOT / "past_runs" / run_id / attempt_id
    if read_only:
        run_dir = run_dir / 'readbacks' / uuid.uuid4().hex
    reject_reparse(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / 'data'
    data_dir.mkdir()
    # Existing scientific workers are untouched; their workspace is attempt-scoped.
    attempt_workspace = WORKSPACE_ROOT / 'models' / 'attempts' / run_id / attempt_id
    reject_reparse(attempt_workspace)
    if not read_only:
        attempt_workspace.mkdir(parents=True, exist_ok=False)
    elif not attempt_workspace.is_dir():
        raise ValueError('attempt_workspace_missing')
    arguments = {**arguments, 'workspace': str(attempt_workspace), 'attempt_id': attempt_id}
    if worker_name in {'heat_worker.py', 'cooling_worker.py'} and os.name == 'nt':
        from app.comsol.bridge_process import compile_visualization_bridge
        from app.comsol.discovery import discover_comsol_java
        try:
            environment = discover_comsol_java()
            if environment.found:
                arguments['visualization_classes'] = str(await asyncio.to_thread(compile_visualization_bridge, environment))
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            _logger.warning('model_checkpoint_compile_unavailable: %s', type(exc).__name__)
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps(arguments, ensure_ascii=False, indent=2), encoding="utf-8")
    producer = worker_name.removesuffix('.py')
    command = [sys.executable]
    if not getattr(sys, 'frozen', False):
        command.append(str(BACKEND_ROOT / 'desktop_runtime.py'))
    command.extend(['--leased-worker', '--worker', producer, '--request', str(request_path)])
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
    log_path = run_dir / 'worker.log'
    assert process.stdout is not None

    def owns_attempt():
        try:
            return (registry.sessions is not None and registry._owned.get(session_id) is owned
                    and registry._active_processes.get(session_id) is process
                    and registry.sessions.owns_worker_attempt(session_id, run_id, attempt_id, owned.identity))
        except (sqlite3.Error, ValueError):
            return False

    def record(reason):
        _logger.warning('%s run=%s attempt=%s', reason, run_id, attempt_id)
        if registry.sessions and session_id:
            try:
                registry.sessions.append_event(session_id, 'worker.lease',
                                               {'run_id': run_id, 'attempt_id': attempt_id, 'status': reason})
            except (KeyError, sqlite3.Error):
                _logger.warning('worker_lease_audit_unavailable')

    async def receive():
        accepted_checkpoint = 0

        async def worker_event(event):
            nonlocal accepted_checkpoint
            if event.get('event_type') != CHECKPOINT_EVENT:
                await on_event(event)
                return
            if not owns_attempt():
                return
            index, size, digest = (event.get(key) for key in ('index', 'bytes', 'sha256'))
            if type(index) is not int or index <= accepted_checkpoint:
                return
            from app.comsol.visualization import inspect_topology
            try:
                raw = await asyncio.to_thread(checkpoint_data, attempt_workspace, index, size, digest)
                await asyncio.to_thread(inspect_topology, raw)
            except (OSError, ValueError):
                record('model_checkpoint_rejected')
                return
            if owns_attempt():
                try:
                    registry.sessions.append_event(session_id, CHECKPOINT_EVENT, {
                        'run_id': run_id, 'attempt_id': attempt_id, 'index': index, 'bytes': size, 'sha256': digest,
                    })
                    accepted_checkpoint = index
                except (KeyError, sqlite3.Error):
                    _logger.warning('model_checkpoint_event_unavailable')

        with log_path.open('xb') as log:
            receipt = await receive_control(process.stdout, log, worker_event, run_id=run_id, attempt_id=attempt_id)
        return receipt, await process.wait()

    execution = None
    try:
        if registry.sessions:
            registry.sessions.update_run(run_id, attempt_id, worker=owned.identity)
            registry.sessions.assert_current(run_id, attempt_id)
        execution = asyncio.create_task(receive())
        receipt, exit_code = await watch_worker(execution, run_dir, owned, owns_attempt, record)
        if registry.sessions:
            registry.sessions.assert_current(run_id, attempt_id)
        value = await asyncio.to_thread(resolve_worker_result, data_dir, receipt, exit_code=exit_code,
                                        run_id=run_id, attempt_id=attempt_id, producer=producer)
        if registry.sessions:
            registry.sessions.assert_current(run_id, attempt_id)
        if exit_code != 0 or not value['success']:
            raise RuntimeError(f"{value.get('error') or f'COMSOL worker exited with code {exit_code}'}. Detailed log: {log_path}")
        if read_only and value.get('model_sha256') != arguments.get('model_sha256'):
            raise ValueError('readback_result_model_digest_mismatch')
        final = value
    finally:
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        cleanup = await owned.terminate()
        if session_id and registry._active_processes.get(session_id) is process:
            registry._active_processes.pop(session_id, None)
            registry._owned.pop(session_id, None)
        if registry.sessions:
            registry.sessions.update_run(run_id, attempt_id, cleanup=cleanup)
    if registry.sessions:
        if read_only:
            registry.sessions.assert_current(run_id, attempt_id)
        else:
            registry.sessions.update_run(run_id, attempt_id, artifacts=final.get('artifacts', {}), require_current=True)
    final['raw_log'] = log_path.read_bytes()[-12_000:].decode('utf-8', errors='replace')
    final["log_path"] = str(log_path)
    final['attempt_id'] = attempt_id
    return final


tool_registry = ToolRegistry()
tool_registry.register(RegisteredTool(name='comsol.read_saved_cooling',
    description='Independently read the current saved cooling model and stored temperature/heat histories; never solve or save.',
    risk='project-read', executor=read_saved_cooling))
tool_registry.register(RegisteredTool(name='comsol.read_saved_heat',
    description='Independently read the current saved Heat model and nine-point field; never solve or save.',
    risk='project-read', executor=read_saved_heat))
tool_registry.register(RegisteredTool(name="comsol.heat_transfer_3d_cooling",
    description="Build and numerically check a 3D constant-property air-cooling model.",
    risk="project-write",executor=run_cooling_worker,alternative="Report failure; preserve the reviewed draft"))
tool_registry.register(RegisteredTool(name='comsol.tds_2d_transient_closed',
    description='Solve sealed transient diffusion and verify actual fields and mass conservation.',
    risk='project-write',executor=run_transient_diffusion_worker,alternative='Preserve the typed draft and report validation failure'))
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
