"""产品中的本地 Heat 模拟会话，复用专家调度、证据审核和检查点。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from app.execution.expert_contracts import Budget, Contract, ExpertResult, ExpertScope, ExpertTask
from app.execution.expert_scheduler import ExpertScheduler, ExpertTool
from app.execution.heat_review import HeatReview
from app.execution.heat_simulation_evidence import (
    REFERENCE, SOLVE_TOOL, STAGES, Scenario, audit_checkpoint, benchmark, fixture_plan, fixture_readbacks,
)
from app.execution.revision_checkpoint import TaskRule
from app.execution.revision_coordinator import RevisionCoordinator
from app.execution.revision_store import CheckpointConflict

logger = logging.getLogger(__name__)


class HeatSimulationService:
    def __init__(self, store):
        self.store = store
        self.jobs: dict[str, asyncio.Task] = {}

    def _active(self, session_id: str) -> bool:
        job = self.jobs.get(session_id)
        return job is not None and not job.done()

    def _checkpoint(self, session: dict, record: dict):
        try:
            return self.store.load_checkpoint(session['project_id'], session['id'], record['simulation_id'])
        except KeyError:
            return None

    def view(self, session: dict) -> dict:
        record = self.store.read_heat_simulation(session['id'])
        common = {'mode': 'simulation', 'scientific_validation_passed': False}
        if record is None:
            return {**common, 'state': 'IDLE', 'can_start': session['plan'] is None}
        checkpoint = self._checkpoint(session, record)
        state = record['state']
        if state == 'RUNNING' and not self._active(session['id']):
            state = 'INTERRUPTED'
        tasks = []
        if checkpoint:
            for name in STAGES:
                if name in checkpoint.verified:
                    phase = 'VERIFIED'
                elif name in checkpoint.in_flight:
                    phase = 'IN_FLIGHT'
                else:
                    phase = 'PENDING' if name in checkpoint.pending and state != 'CANCELLED' else 'NOT_DISPATCHED'
                tasks.append({'id': name, 'state': phase})
        return {**common, 'state': state, 'can_start': False, 'scenario': record['scenario'],
                'simulation_id': record['simulation_id'], 'version': record['version'],
                'attempt_id': checkpoint.scope.attempt_id if checkpoint else None,
                'tasks': tasks, 'report': record['report'], 'reason': record['reason']}

    def start(self, session: dict, scenario: Scenario) -> dict:
        self.store.start_heat_simulation(session['id'], session['project_id'], scenario, fixture_plan())
        self._schedule(session['id'], 1)
        return self.view(self.store.get(session['id']))

    def _require(self, session_id: str, version: int, states: set[str]) -> dict:
        record = self.store.read_heat_simulation(session_id)
        if record is None:
            raise KeyError(session_id)
        if record['version'] != version:
            raise CheckpointConflict('simulation_version_changed')
        if record['state'] not in states:
            raise CheckpointConflict('simulation_action_unavailable')
        return record

    def resume(self, session: dict, version: int) -> dict:
        sid = session['id']
        self._require(sid, version, {'RUNNING', 'PAUSED'})
        if self._active(sid):
            raise CheckpointConflict('simulation_already_running')
        next_version = self.store.update_heat_simulation(sid, version, 'RUNNING')
        self._schedule(sid, next_version, recovering=True)
        return self.view(session)

    def approve(self, session: dict, version: int, proposal_id: str) -> dict:
        sid = session['id']
        record = self._require(sid, version, {'REVISION_REQUIRED'})
        report = HeatReview.model_validate(record['report'])
        if report.revision is None or report.revision.revision_id != proposal_id:
            raise CheckpointConflict('simulation_proposal_changed')
        if self._active(sid):
            raise CheckpointConflict('simulation_already_running')
        next_version = self.store.update_heat_simulation(sid, version, 'RUNNING')
        self._schedule(sid, next_version)
        return self.view(session)

    def cancel(self, session: dict, version: int) -> dict:
        sid = session['id']
        record = self._require(sid, version, {'RUNNING', 'PAUSED', 'REVISION_REQUIRED'})
        self.store.update_heat_simulation(sid, version, 'CANCELLED', reason='user_cancelled')
        checkpoint = self._checkpoint(session, record)
        if checkpoint:
            self.store.stop_checkpoint(checkpoint.scope)
        if self._active(sid):
            self.jobs[sid].cancel()
        return self.view(session)

    def _schedule(self, sid: str, version: int, *, recovering: bool = False) -> None:
        task = asyncio.create_task(self._drive(sid, version, recovering), name=f'heat-simulation-{sid}')
        self.jobs[sid] = task
        task.add_done_callback(lambda completed: self.jobs.pop(sid, None) if self.jobs.get(sid) is completed else None)

    def _create_checkpoint(self, session: dict, record: dict):
        scope = ExpertScope(project_id=session['project_id'], session_id=session['id'],
                            case_id=record['simulation_id'], plan_id=session['plan']['plan_id'],
                            plan_revision=session['plan']['revision'], run_id='experts-' + record['simulation_id'],
                            attempt_id=uuid4().hex)
        rules = tuple(TaskRule(
            task=ExpertTask(schema_version=1, scope=scope, task_id=name, capability='heat_simulation', objective=name,
                            dependencies=(STAGES[index - 1],) if index else (),
                            input_evidence_refs=(REFERENCE.evidence_id,) if not index else (),
                            requested_tools=(SOLVE_TOOL,) if name == 'solve' else (),
                            budget_estimate=Budget(agent_calls=0, solver_runs=int(name == 'solve'))),
            review_targets=('scientific_correctness',) if name == 'solve' else (),
        ) for index, name in enumerate(STAGES))
        return self.store.create_checkpoint(scope, rules, Budget(agent_calls=0, solver_runs=2), (REFERENCE,))

    async def _drive(self, sid: str, version: int, recovering: bool) -> None:
        try:
            record = self._require(sid, version, {'RUNNING'})
            session = self.store.get(sid)
            checkpoint = self._checkpoint(session, record) or self._create_checkpoint(session, record)
            scenario = record['scenario']

            async def solve(task, _arguments):
                # 唯一的求解工具只模拟命令与回执。
                if scenario == 'unknown_submission':
                    raise RuntimeError('simulated_submission_unknown')
                return True

            def authorize(task, _name, _arguments):
                current = self.store.read_heat_simulation(sid)
                return (current is not None and current['state'] == 'RUNNING' and current['version'] == version
                        and task.scope.session_id == sid and task.scope.case_id == record['simulation_id'])

            scheduler = ExpertScheduler(
                capabilities={'heat_simulation': (SOLVE_TOOL,)},
                tools={SOLVE_TOOL: ExpertTool(Contract, solve, requires_run=True, exclusive_resource='fixture-heat')},
                max_concurrency=1, authorize_run=authorize,
            )
            coordinator = RevisionCoordinator(self.store, scheduler)
            previous = HeatReview.model_validate(record['report']) if record['report'] else None
            if previous and previous.revision and checkpoint.scope == previous.scope:
                checkpoint = coordinator.approve_revision(checkpoint.scope, previous.revision, previous)
            elif recovering:
                checkpoint = self.store.resume_checkpoint(checkpoint.scope)

            audit = None

            async def execute(context):
                nonlocal audit
                task = context.task
                await asyncio.sleep(0)
                summary = 'Simulation stage completed'
                if task.task_id == 'solve':
                    await context.call_tool(SOLVE_TOOL, {})
                    summary = 'Simulated solver returned success'
                elif task.task_id == 'validate':
                    audit = audit_checkpoint(self.store.read_checkpoint(task.scope), scenario)
                    summary = f'Simulated independent review: {audit.status}'
                return ExpertResult(
                    schema_version=1, scope=task.scope, task_id=task.task_id, summary=summary,
                    claims=[{'claim_id': 'fixture-stage', 'statement': summary,
                             'evidence_refs': [ref.evidence_id for ref in context.evidence]}],
                ).model_dump()

            def admit(result):
                if result.task_id != 'solve':
                    return ()
                return tuple(item.ref for item in fixture_readbacks(benchmark(checkpoint), result.scope, scenario))

            if checkpoint.status == 'ACTIVE':
                scratch = Path(self.store.path).parent / 'heat-simulation-scratch'
                scratch.mkdir(exist_ok=True)
                checkpoint = await coordinator.dispatch(
                    checkpoint.scope, workspace_root=scratch, execute=execute, admit_result=admit,
                    pause_before=lambda saved: scenario == 'interrupted' and not recovering and 'build' in saved.verified,
                )
            if checkpoint.status == 'ACTIVE':
                self.store.update_heat_simulation(sid, version, 'PAUSED', reason='safe_checkpoint')
            elif checkpoint.status == 'STOPPED':
                self.store.update_heat_simulation(sid, version, 'STOPPED', reason=checkpoint.stop_reason)
            else:
                audit = audit or audit_checkpoint(checkpoint, scenario)
                state = 'PASSED' if audit.status == 'PASS' else audit.status
                self.store.update_heat_simulation(sid, version, state, report=audit.model_dump(mode='json'))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('Heat simulation stopped: %s', type(exc).__name__)
            try:
                self.store.update_heat_simulation(sid, version, 'STOPPED', reason='simulation_unavailable')
            except CheckpointConflict:
                pass

    async def shutdown(self) -> None:
        tasks = tuple(task for task in self.jobs.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
