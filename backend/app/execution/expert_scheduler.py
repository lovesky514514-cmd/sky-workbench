"""受限专家调度与访问控制 / Bounded expert dispatch and scoped access."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path, PureWindowsPath
import re
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel

from app.execution.expert_contracts import EvidenceRef, ExpertIntake, ExpertResult, ExpertTask
from app.execution.files import bounded_read, reject_reparse


_SCRATCH_LIMIT = 1024 * 1024
_SCRATCH_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')


@dataclass(frozen=True, slots=True)
class ExpertTool:
    arguments: type[BaseModel]
    execute: Callable[[ExpertTask, BaseModel], Awaitable[Any]]
    requires_run: bool = False
    exclusive_resource: str | None = None


@dataclass(frozen=True, slots=True)
class ExpertContext:
    task: ExpertTask
    dependencies: tuple[ExpertResult, ...]
    evidence: tuple[EvidenceRef, ...]
    call_tool: Callable[[str, dict[str, Any]], Awaitable[Any]]
    read_scratch: Callable[[str], bytes]
    write_scratch: Callable[[str, bytes], None]


ExpertExecutor = Callable[[ExpertContext], Awaitable[dict[str, Any]]]
RunAuthorizer = Callable[[ExpertTask, str, BaseModel], bool]
FailureObserver = Callable[[ExpertTask, str], None]
ResultObserver = Callable[[ExpertResult], None]
TaskGuard = Callable[[ExpertTask], None]


@dataclass(frozen=True, slots=True)
class ExpertBatchResult:
    results: dict[str, ExpertResult]
    failures: dict[str, str]
    blocked: dict[str, tuple[str, ...]]
    # 提交和所有权错误仅供宿主 / Commit and ownership errors stay on the host.
    errors: dict[str, Exception] = field(default_factory=dict, repr=False)


class ExpertScheduler:
    """通过授权工具调度可信适配器 / Dispatch trusted adapters through scoped tools."""

    def __init__(self, *, capabilities: Mapping[str, Iterable[str]], tools: Mapping[str, ExpertTool],
                 max_concurrency: int, authorize_run: RunAuthorizer | None = None,
                 observe_failure: FailureObserver | None = None) -> None:
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError('Expert concurrency must be a positive integer')
        self._max_concurrency = max_concurrency
        self._authorize_run = authorize_run
        self._observe_failure = observe_failure
        self._tools = dict(tools)
        self._capabilities = {name: frozenset(allowed) for name, allowed in capabilities.items()}
        for name, allowed in self._capabilities.items():
            if not allowed <= self._tools.keys():
                raise ValueError(f'Capability {name} references an unregistered tool')
        for name, tool in self._tools.items():
            if tool.arguments.model_config.get('extra') != 'forbid':
                raise ValueError(f'Expert tool {name} must reject undeclared arguments')
            if tool.requires_run and not tool.exclusive_resource:
                raise ValueError(f'Solver tool {name} must declare its exclusive resource')
        self._batch_lock = asyncio.Lock()

    def _note_failure(self, task: ExpertTask, code: str) -> None:
        if self._observe_failure is not None:
            try:
                self._observe_failure(task, code)
            except Exception:
                # 诊断失败保留原任务状态 / Diagnostic failures preserve task state.
                pass

    async def run(self, intake: ExpertIntake, *, workspace_root: Path, execute: ExpertExecutor,
                  retained: Iterable[ExpertResult] = (), publish: ResultObserver | None = None,
                  enforce_solver_budget: bool = False, assert_active: TaskGuard | None = None) -> ExpertBatchResult:
        async with self._batch_lock:
            tasks = {task.task_id: task for task in intake.tasks}
            seeds: dict[str, ExpertResult] = {}
            for result in retained:
                task = tasks.get(result.task_id)
                if (task is None or result.task_id in seeds
                        or result.scope.plan_revision > task.scope.plan_revision
                        or result.scope.model_copy(update={'attempt_id': task.scope.attempt_id,
                                                           'plan_revision': task.scope.plan_revision}) != task.scope):
                    raise ValueError('Retained result is outside the current coordinator case')
                intake.evidence_for(ref for claim in result.claims for ref in claim.evidence_refs)
                seeds[result.task_id] = result
            if any(set(tasks[task_id].dependencies) - seeds.keys() for task_id in seeds):
                raise ValueError('Retained tasks require retained prerequisites')
            resources: dict[str, set[str]] = {}
            for task in tasks.values():
                allowed = self._capabilities.get(task.capability)
                if allowed is None:
                    self._note_failure(task, 'TOOL_PERMISSION_DENIED')
                    raise ValueError(f'Unknown expert capability: {task.capability}')
                if not set(task.requested_tools) <= allowed:
                    self._note_failure(task, 'TOOL_PERMISSION_DENIED')
                    raise PermissionError(f'Task {task.task_id} requests a tool outside its capability')
                resources[task.task_id] = {
                    resource for name in task.requested_tools
                    if (resource := self._tools[name].exclusive_resource) is not None
                }

            reject_reparse(workspace_root)
            with TemporaryDirectory(prefix='experts-', dir=workspace_root) as directory:
                return await self._dispatch(intake, tasks, resources, Path(directory), execute, seeds, publish,
                                            enforce_solver_budget, assert_active)

    async def _dispatch(self, intake: ExpertIntake, tasks: dict[str, ExpertTask], resources: dict[str, set[str]],
                        directory: Path, execute: ExpertExecutor, seeds: dict[str, ExpertResult],
                        publish: ResultObserver | None, enforce_solver_budget: bool,
                        assert_active: TaskGuard | None) -> ExpertBatchResult:
        graph = TopologicalSorter({name: task.dependencies for name, task in tasks.items()})
        graph.prepare()
        ready: list[str] = []
        active: dict[asyncio.Task[ExpertResult], str] = {}
        held: set[str] = set()
        batch = ExpertBatchResult(dict(seeds), {}, {})
        # Directory names are generated by the host, independently of model IDs.
        workspaces = {name: directory / str(index) for index, name in enumerate(tasks)}
        try:
            while graph.is_active():
                ready.extend(graph.get_ready())
                for name in tuple(ready):
                    task = tasks[name]
                    unavailable = tuple(dep for dep in task.dependencies if dep not in batch.results)
                    if name in seeds:
                        ready.remove(name)
                        graph.done(name)
                    elif unavailable:
                        self._note_failure(task, 'DEPENDENCY_UNSATISFIED')
                        batch.blocked[name] = unavailable
                        ready.remove(name)
                        graph.done(name)
                    elif len(active) < self._max_concurrency and not held & resources[name]:
                        dependencies = tuple(batch.results[dep] for dep in task.dependencies)
                        future = asyncio.create_task(
                            self._execute(intake, task, dependencies, workspaces[name], execute, publish,
                                          enforce_solver_budget, assert_active)
                        )
                        active[future] = name
                        ready.remove(name)
                        held.update(resources[name])
                if not active:
                    continue
                completed, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for future in completed:
                    name = active.pop(future)
                    held.difference_update(resources[name])
                    try:
                        batch.results[name] = future.result()
                    except Exception as error:
                        error._sky_expert_task_id = name
                        error._sky_expert_scope = tasks[name].scope
                        batch.failures[name] = f'{type(error).__name__}: {error}'
                        batch.errors[name] = error
                        if not isinstance(error, PermissionError):
                            self._note_failure(tasks[name], 'unclassified')
                    graph.done(name)
        finally:
            for future in active:
                future.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
        return batch

    async def _execute(self, intake: ExpertIntake, task: ExpertTask, dependencies: tuple[ExpertResult, ...],
                       workspace: Path, execute: ExpertExecutor, publish: ResultObserver | None,
                       enforce_solver_budget: bool, assert_active: TaskGuard | None) -> ExpertResult:
        workspace.mkdir()
        refs = set(task.input_evidence_refs)
        for dependency in dependencies:
            for claim in dependency.claims:
                refs.update(claim.evidence_refs)
        evidence = intake.evidence_for(sorted(refs))
        owner = asyncio.current_task()
        solver_calls = 0

        def require_owner() -> None:
            if owner is None or asyncio.current_task() is not owner or owner.cancelling():
                raise PermissionError('Expert access requires an active dispatched task')
            if assert_active is not None:
                assert_active(task)

        async def call_tool(name: str, payload: dict[str, Any]) -> Any:
            nonlocal solver_calls
            require_owner()
            if name not in task.requested_tools:
                self._note_failure(task, 'TOOL_PERMISSION_DENIED')
                raise PermissionError('Tool was not granted to this expert task')
            tool = self._tools[name]
            arguments = tool.arguments.model_validate(payload)
            if tool.requires_run:
                if enforce_solver_budget and solver_calls >= task.budget_estimate.solver_runs:
                    raise PermissionError('The task has exhausted its reserved solver calls')
                if self._authorize_run is None or self._authorize_run(task, name, arguments) is not True:
                    self._note_failure(task, 'RUN_APPROVAL_MISSING')
                    raise PermissionError('The solver tool requires host RUN authorization')
                solver_calls += 1
            return await tool.execute(task, arguments)

        def scratch_path(name: str) -> Path:
            require_owner()
            if not _SCRATCH_NAME.fullmatch(name) or name.endswith('.') or PureWindowsPath(name).is_reserved():
                raise PermissionError('Scratch file must have a safe basename')
            path = workspace / name
            reject_reparse(path)
            return path

        def read_scratch(name: str) -> bytes:
            path = scratch_path(name)
            return bounded_read(workspace, path.name, _SCRATCH_LIMIT)

        def write_scratch(name: str, data: bytes) -> None:
            path = scratch_path(name)
            if not isinstance(data, bytes) or len(data) > _SCRATCH_LIMIT:
                raise ValueError('Scratch write must be bytes within the size limit')
            with path.open('xb') as stream:
                stream.write(data)

        context = ExpertContext(task, dependencies, evidence, call_tool, read_scratch, write_scratch)
        payload = await execute(context)
        require_owner()
        result = intake.result(payload, dispatched_task_id=task.task_id)
        if any(set(claim.evidence_refs) - refs for claim in result.claims):
            self._note_failure(task, 'EVIDENCE_SCOPE_VIOLATION')
            raise PermissionError('Expert result cites evidence outside its task context')
        if publish is not None:
            publish(result)
        return result
