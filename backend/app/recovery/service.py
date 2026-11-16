"""总控编排故障诊断，专家只能提出候选，审批与复验由宿主负责。"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import ValidationError

from app.execution.expert_contracts import Contract, EvidenceRef, ExpertIntake, ExpertScope
from app.execution.expert_scheduler import ExpertScheduler, ExpertTool
from app.recovery.classifier import RULES, classify
from app.recovery.ingest import Signal, capture, digest
from app.recovery.schemas import (
    Action, Candidate, Cause, DiagnosisReport, FailureEvent,
    Identifier, ModelDiagnosis, Origin, Proposal, State,
)
from app.recovery.store import RecoveryConflict
from app.recovery.verifier import IndependentVerifier, RecoveryActionExecutor, SimulatedActionExecutor, SimulationClaim

CAPABILITY = 'recovery_diagnostics'
SNAPSHOT = 'recovery.get_authorized_snapshot'
LOOKUP = 'recovery.lookup_verified_case'
REPORT_TIMEOUT = 12
MODEL_TIMEOUT = 4


class CaseArgs(Contract):
    case_id: Identifier


class LookupArgs(CaseArgs):
    cause: Cause


class RecoveryService:
    def __init__(self, store, workspace: Path, *, model=None, executor: RecoveryActionExecutor | None = None,
                 verifier: IndependentVerifier | None = None):
        self.store = store
        self.workspace = workspace
        self.model = model
        self.executor = executor or SimulatedActionExecutor()
        self.verifier = verifier or IndependentVerifier()
        self._admission = asyncio.Semaphore(3)
        self._active: dict[str, asyncio.Task] = {}
        self._user_cancelled: set[str] = set()

    def _case(self, case_id, project_id, session_id):
        record = self.store.recovery_record(case_id, project_id, session_id)
        return record, FailureEvent.model_validate(record['failure'])

    def _transition(self, record, event, session_id, target, **values):
        return self.store.recovery_transition(event.event_id, event.scope.project_id, session_id,
                                              State(record['state']), record['revision'], target, **values)

    async def diagnose(self, case_id, project_id, session_id, *, allow_model=False):
        record, event = self._case(case_id, project_id, session_id)
        if record['state'] != State.DETECTED:
            return record
        if not record['budget']:
            return self._transition(record, event, session_id, State.ESCALATED, reason='diagnostic_budget_exhausted')
        try:
            record = self._transition(record, event, session_id, State.SNAPSHOTTING, consume=True)
        except RecoveryConflict:
            return self.store.recovery_record(case_id, project_id, session_id)

        self._active[case_id] = asyncio.current_task()
        try:
            async with asyncio.timeout(REPORT_TIMEOUT):
                async with self._admission:
                    snapshot, report = await self._diagnose_expert(event, session_id, allow_model)
                record = self._transition(record, event, session_id, State.CLASSIFIED, snapshot=snapshot)
                record = self._transition(record, event, session_id, State.DIAGNOSING)
                # 历史故障仍可诊断，旧计划或旧尝试不可进入提案审批。
                try:
                    self.store._recovery_check_fresh(snapshot, event.scope)
                except RecoveryConflict as exc:
                    target = State.CANCELLED if str(exc) == 'cancelled' else State.EXPIRED
                    return self._transition(record, event, session_id, target, report=report, reason=str(exc))
                if report.rule.action == Action.FORBIDDEN:
                    return self._transition(record, event, session_id, State.BLOCKED, report=report,
                                            reason='policy_denied')
                proposal = self._proposal(event, snapshot, report)
                return self._transition(record, event, session_id, State.PROPOSED, report=report,
                                        proposal=proposal, check_scope=event.scope)
        except asyncio.CancelledError:
            stopped = self._finish_early(record, event, session_id, State.CANCELLED, 'diagnosis_cancelled')
            if case_id in self._user_cancelled:
                return stopped
            raise
        except RecoveryConflict as exc:
            state = State.CANCELLED if str(exc) == 'cancelled' else State.EXPIRED
            return self._finish_early(record, event, session_id, state, str(exc))
        except TimeoutError:
            return self._finish_early(record, event, session_id, State.ESCALATED, 'diagnosis_timeout')
        except (OSError, KeyError, ValueError, RuntimeError, sqlite3.Error):
            return self._finish_early(record, event, session_id, State.ESCALATED, 'diagnosis_unavailable')
        finally:
            self._active.pop(case_id, None)
            self._user_cancelled.discard(case_id)

    def cancel(self, case_id, project_id, session_id):
        record, event = self._case(case_id, project_id, session_id)
        if record['state'] in {State.CANCELLED, State.EXPIRED, State.RESOLVED, State.UNRESOLVED,
                               State.ESCALATED, State.BLOCKED}:
            return record
        stopped = self._transition(record, event, session_id, State.CANCELLED, reason='user_cancelled')
        owner = self._active.get(case_id)
        if owner:
            self._user_cancelled.add(case_id)
            owner.cancel()
        return stopped

    async def check_connection(self, case_id, project_id, session_id, bridge):
        from app.recovery.connection_probe import probe_connection
        if case_id in self._active:
            raise RecoveryConflict('diagnostic_action_in_flight')
        self._active[case_id] = asyncio.current_task()
        try:
            return await probe_connection(self.store, self.workspace, case_id, project_id, session_id, bridge)
        except asyncio.CancelledError:
            if case_id in self._user_cancelled:
                return self.store.recovery_record(case_id, project_id, session_id)
            raise
        finally:
            self._active.pop(case_id, None)
            self._user_cancelled.discard(case_id)

    def _finish_early(self, record, event, session_id, state, reason):
        try:
            return self._transition(record, event, session_id, state, reason=reason)
        except RecoveryConflict:
            return self.store.recovery_record(event.event_id, event.scope.project_id, session_id)

    async def _diagnose_expert(self, event: FailureEvent, session_id: str, allow_model: bool):
        snapshot = self.store.recovery_snapshot(event.scope)
        attempt = uuid4().hex
        scope = ExpertScope(project_id=event.scope.project_id, session_id=session_id, case_id=event.event_id,
                            plan_id=event.scope.plan_id or 'diagnostic-only',
                            plan_revision=event.scope.plan_revision or 1, run_id=event.event_id, attempt_id=attempt)
        task_id = uuid4().hex
        try:
            self.store._recovery_check_fresh(snapshot, event.scope)
        except RecoveryConflict:
            # 取消和过期后不启动工具或模型，仍可对历史事件进行规则分类。
            return snapshot, await self._report(event, snapshot, (), False)

        def guard(task, arguments):
            if task.scope != scope or task.task_id != task_id or arguments.case_id != event.event_id:
                raise PermissionError('diagnostic_scope_mismatch')

        async def get_snapshot(task, args):
            guard(task, args)
            self.store._recovery_check_fresh(self.store.recovery_snapshot(event.scope), event.scope)
            return snapshot

        async def lookup(task, args):
            guard(task, args)
            self.store._recovery_check_fresh(self.store.recovery_snapshot(event.scope), event.scope)
            return self.store.recovery_verified_knowledge(event, args.cause)

        def observe(task, code):
            capture(self.store, Signal(Origin.EXPERT, 'expert_dispatch', code),
                    session_id=event.scope.session_id, project_id=event.scope.project_id,
                    plan_id=event.scope.plan_id, plan_revision=event.scope.plan_revision,
                    run_id=event.scope.scientific_run_id, attempt_id=event.scope.scientific_attempt_id,
                    expert_task_id=task.task_id, expert_attempt_id=task.scope.attempt_id, parent=event.event_id,
                    correlation=event.correlation_id)

        tools = {SNAPSHOT: ExpertTool(CaseArgs, get_snapshot), LOOKUP: ExpertTool(LookupArgs, lookup)}
        scheduler = ExpertScheduler(capabilities={CAPABILITY: tools}, tools=tools, max_concurrency=1,
                                    observe_failure=observe)
        task = dict(schema_version=1, scope=scope.model_dump(), task_id=task_id, capability=CAPABILITY,
                    objective='Classify a scoped failure and propose safe observations',
                    requested_tools=tuple(tools), input_evidence_refs=(event.event_id, snapshot.snapshot_id),
                    budget_estimate={'agent_calls': int(allow_model), 'solver_runs': 0})
        evidence = [EvidenceRef(schema_version=1, evidence_id=ref, kind='reference', source_ref='session_recovery',
                                locator=f'recovery:{ref}', applicability='this diagnostic attempt only')
                    for ref in task['input_evidence_refs']]
        intake = ExpertIntake(scope=scope, tasks=[task], evidence=evidence)
        report = None

        async def execute(context):
            nonlocal report
            current = await context.call_tool(SNAPSHOT, {'case_id': event.event_id})
            rule = classify(event)
            knowledge = await context.call_tool(LOOKUP, {'case_id': event.event_id, 'cause': rule.classification})
            report = await self._report(event, current, knowledge, allow_model)
            report = report.model_copy(update={'expert_task_id': context.task.task_id,
                                               'expert_attempt_id': context.task.scope.attempt_id})
            return dict(schema_version=1, scope=context.task.scope.model_dump(), task_id=task_id,
                        summary=report.rule.classification.value, claims=[
                            {'claim_id': 'diagnosis', 'statement': report.rule.safe_next_probe,
                             'evidence_refs': report.evidence_refs}])

        self.workspace.mkdir(parents=True, exist_ok=True)
        batch = await scheduler.run(intake, workspace_root=self.workspace, execute=execute)
        if task_id not in batch.results or report is None:
            self.store._recovery_check_fresh(self.store.recovery_snapshot(event.scope), event.scope)
            raise RuntimeError('diagnostic_expert_failed')
        return snapshot, report

    async def _report(self, event, snapshot, knowledge, allow_model):
        rule = classify(event)
        evidence = (event.event_id, snapshot.snapshot_id)
        status = 'NOT_NEEDED'
        candidates = ()
        basis = 'rule'
        if rule.classification == Cause.UNKNOWN:
            basis = 'insufficient_evidence'
            status = 'DISABLED'
            if knowledge:
                basis = 'verified_case_match'
                status = 'NOT_NEEDED'
                causes = dict.fromkeys(item.cause for item in knowledge if item.cause != Cause.UNKNOWN)
                candidates = tuple(Candidate(cause_code=cause, supporting_evidence_refs=evidence,
                                             explanation=RULES[cause][2]) for cause in tuple(causes)[:3])
            elif allow_model and self.model:
                status, candidates = await self._model_candidates(event, evidence)
                if candidates:
                    basis = 'model_hypotheses_only'
        return DiagnosisReport(schema_version=1, diagnosis_id=uuid4().hex, case_id=event.event_id,
                               rule=rule, evidence_refs=evidence, missing_evidence=rule.required_evidence,
                               knowledge_refs=tuple(item.entry_id for item in knowledge),
                               candidates=candidates, confidence_basis=basis, model_status=status)

    async def _model_candidates(self, event, evidence):
        safe = {'origin': event.origin, 'code': event.error_code, 'status': event.http_status,
                'route': event.route_template, 'phase': event.facts.phase, 'evidence_ids': evidence}
        try:
            async with asyncio.timeout(MODEL_TIMEOUT):
                response = await self.model(safe, ModelDiagnosis.model_json_schema())
        except (TimeoutError, OSError, ValueError, RuntimeError, KeyError, httpx.HTTPError):
            return 'UNAVAILABLE', ()
        if response is None:
            return 'UNAVAILABLE', ()
        try:
            if not isinstance(response, str) or len(response.encode('utf-8')) > 4096:
                return 'INVALID', ()
            parsed = ModelDiagnosis.model_validate_json(response)
            if any(not set(item.supporting_evidence_refs + item.contradicting_evidence_refs) <= set(evidence)
                   or set(item.supporting_evidence_refs) & set(item.contradicting_evidence_refs)
                   for item in parsed.candidates):
                return 'INVALID', ()
        except (ValidationError, ValueError):
            return 'INVALID', ()
        return 'CANDIDATES', tuple(Candidate(**item.model_dump(), explanation=RULES[item.cause_code][2])
                                   for item in parsed.candidates)

    @staticmethod
    def _proposal(event, snapshot, report):
        owned = snapshot.run_state == 'active' and snapshot.owner_matches is True and event.scope.scientific_attempt_id
        idle = snapshot.run_state == 'none' and snapshot.owner_matches is None and event.scope.session_id
        simulation = report.rule.action is Action.REFRESH and bool(owned or idle)
        body = dict(schema_version=1, proposal_id=uuid4().hex, case_id=event.event_id,
                    snapshot_id=snapshot.snapshot_id, plan_revision=event.scope.plan_revision,
                    scientific_attempt_id=event.scope.scientific_attempt_id,
                    action=Action.SIMULATE if simulation else report.rule.action,
                    expected_preconditions=('计划版本、尝试和权限仍有效',),
                    affected_tasks=(event.scope.expert_task_id,) if event.scope.expert_task_id else (),
                    affected_resources=('session_snapshot',) if report.rule.action is Action.REFRESH else (),
                    risk_level='SIMULATED' if simulation else 'READ_ONLY', approval_required=simulation,
                    expected_result='模拟刷新后状态一致' if simulation else '得到可核对的只读状态',
                    rollback_available=False,
                    verification_plan=('由独立验证器重新核对运行身份、快照和模拟回执',) if simulation else
                    (report.rule.safe_next_probe,), evidence_refs=report.evidence_refs)
        return Proposal(**body, digest=digest(body))

    def submit(self, case_id, project_id, session_id):
        record, event = self._case(case_id, project_id, session_id)
        proposal = self._current_proposal(record, State.PROPOSED)
        if proposal.action is not Action.SIMULATE or not proposal.approval_required:
            raise RecoveryConflict('read_only_proposal')
        try:
            return self._transition(record, event, session_id, State.WAITING,
                                    check_scope=event.scope, review_proposal=proposal)
        except RecoveryConflict as exc:
            return self._expire_if_stale(record, event, session_id, exc)

    def decide(self, case_id, project_id, session_id, proposal_digest, decision):
        record, event = self._case(case_id, project_id, session_id)
        proposal = self._current_proposal(record, State.WAITING)
        if proposal_digest != proposal.digest:
            raise RecoveryConflict('proposal_digest_changed')
        try:
            self.store.decide_recovery_review(project_id, session_id, proposal, event.scope, decision)
        except RecoveryConflict as exc:
            return self._expire_if_stale(record, event, session_id, exc)
        if decision == 'reject':
            return self._transition(record, event, session_id, State.BLOCKED, reason='review_rejected')
        return self.store.recovery_record(case_id, project_id, session_id)

    async def simulate(self, case_id, project_id, session_id):
        record, event = self._case(case_id, project_id, session_id)
        proposal = self._current_proposal(record, State.WAITING)
        if proposal.action is not Action.SIMULATE:
            raise RecoveryConflict('action_not_simulatable')
        before = self.store.recovery_snapshot(event.scope)
        try:
            claim = SimulationClaim(proposal_id=proposal.proposal_id, approval_ref=proposal.proposal_id,
                                    digest=proposal.digest)
            record = self._transition(record, event, session_id, State.SIMULATING,
                                      check_scope=event.scope, action=claim, snapshot=before)
        except RecoveryConflict as exc:
            return self._expire_if_stale(record, event, session_id, exc)
        self._active[case_id] = asyncio.current_task()
        try:
            async with asyncio.timeout(REPORT_TIMEOUT):
                receipt = await self.executor.execute(proposal, before)
                record = self._transition(record, event, session_id, State.VERIFYING, check_scope=event.scope)
                after = self.store.recovery_snapshot(event.scope)
                verification = await self.verifier.verify(proposal, before, after, receipt)
                accepted = (verification.independent is True and verification.passed is True
                            and verification.mode == 'simulation' and verification.case_id == case_id
                            and set(verification.evidence_refs) == {before.snapshot_id, after.snapshot_id})
                return self._transition(record, event, session_id, State.RESOLVED if accepted else State.UNRESOLVED,
                                        check_scope=event.scope, reason='simulation_only' if accepted else
                                        'independent_check_failed', verification=verification, verification_snapshot=after)
        except asyncio.CancelledError:
            stopped = self._finish_early(record, event, session_id, State.CANCELLED, 'simulation_cancelled')
            if case_id in self._user_cancelled:
                return stopped
            raise
        except RecoveryConflict as exc:
            return self._expire_if_stale(record, event, session_id, exc)
        except (TimeoutError, OSError, ValueError, RuntimeError, AttributeError, sqlite3.Error):
            return self._finish_early(record, event, session_id, State.UNRESOLVED, 'simulation_unavailable')
        finally:
            self._active.pop(case_id, None)
            self._user_cancelled.discard(case_id)

    @staticmethod
    def _current_proposal(record, state):
        if record['state'] != state or record['proposal'] is None:
            raise RecoveryConflict('proposal_not_current')
        proposal = Proposal.model_validate(record['proposal'])
        if digest(proposal.model_dump(mode='json', exclude={'digest'})) != proposal.digest:
            raise RecoveryConflict('proposal_digest_changed')
        return proposal

    def _expire_if_stale(self, record, event, session_id, exc):
        if str(exc) in {'stale_plan', 'cancelled', 'stale_or_terminal_attempt', 'owner_not_proven'}:
            return self._finish_early(record, event, session_id,
                                      State.CANCELLED if str(exc) == 'cancelled' else State.EXPIRED, str(exc))
        raise exc
