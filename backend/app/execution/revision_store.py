"""N04 与会话事件共享 SQLite 事务，所有派发先写账再调用执行器。"""

from __future__ import annotations

from contextlib import contextmanager
import json
from uuid import uuid4

from app.execution.expert_contracts import Budget, EvidenceRef, ExpertIntake, ExpertResult, ExpertScope
from app.execution.revision_checkpoint import RevisionCheckpoint, StopReason, TaskRule, VerifiedTask, add_budget, within
from app.scientific.execution_contract import execution_digest

CHECKPOINT_SCHEMA = """
CREATE TABLE IF NOT EXISTS expert_checkpoints (
 session_id TEXT NOT NULL REFERENCES sessions(id), case_id TEXT NOT NULL, payload_json TEXT NOT NULL,
 PRIMARY KEY(session_id, case_id)
);
"""


class CheckpointConflict(ValueError):
    pass


class RevisionStoreMixin:
    @staticmethod
    def _require_scientific_scope(db, scope):
        """真实科研图是 RunJournal 的子记录；模拟检查点不产生科学运行身份。"""
        row = db.execute('SELECT * FROM run_journal WHERE run_id=?', (scope.run_id,)).fetchone()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_studies'").fetchone():
            study = db.execute('SELECT status,scope_json,goal_json FROM research_studies WHERE case_id=? AND session_id=?',
                               (scope.case_id, scope.session_id)).fetchone()
            if study and (study['status'] != 'ACTIVE' or json.loads(study['scope_json']) != scope.model_dump()):
                raise CheckpointConflict('research_attempt_not_active')
            if study:
                session = db.execute('SELECT project_id,plan_json FROM sessions WHERE id=?', (scope.session_id,)).fetchone()
                plan = json.loads(session['plan_json'] or '{}') if session else {}
                if session is None or session['project_id'] != scope.project_id or json.loads(study['goal_json']).get('session_plan_binding') != [plan.get('plan_id'), plan.get('revision')]:
                    raise CheckpointConflict('research_session_plan_changed')
        if row is not None:
            attempt = db.execute('SELECT outcome FROM run_attempts WHERE run_id=? AND attempt_id=?',
                                 (scope.run_id, scope.attempt_id)).fetchone()
            owner = db.execute('SELECT owner_id FROM comsol_owner WHERE slot=1').fetchone()
            if (row['session_id'] != scope.session_id or row['attempt_id'] != scope.attempt_id
                    or row['outcome'] is not None or row['cancel_requested']
                    or attempt is None or attempt['outcome'] is not None
                    or owner is None or owner['owner_id'] != scope.run_id):
                raise CheckpointConflict('scientific_attempt_not_active_or_owned')

    def extend_checkpoint(self, scope, additions=(), cancellations=(), *, reason, evidence_refs=()):
        """总控按当前证据修订任务图；不修改科学计划或产生 RUN 授权。"""
        with self._checkpoint_write() as db:
            self._require_scientific_scope(db, scope)
            checkpoint = self._checkpoint_row(db, scope)
            self._require_checkpoint_plan(checkpoint, self._checkpoint_plan(db, scope))
            if checkpoint.status == 'STOPPED' or checkpoint.dispatch_token or checkpoint.in_flight:
                raise CheckpointConflict('graph_revision_requires_safe_boundary')
            known = {ref.evidence_id for ref in checkpoint.catalog()}
            if not reason or not set(evidence_refs) <= known:
                raise CheckpointConflict('graph_revision_evidence_missing')
            cancelled = set(cancellations)
            if not cancelled <= set(checkpoint.pending):
                raise CheckpointConflict('only_unclaimed_tasks_can_be_cancelled')
            existing = {rule.task.task_id for rule in checkpoint.rules}
            if any(rule.task.task_id in existing or rule.task.scope != scope for rule in additions):
                raise CheckpointConflict('graph_revision_identity_conflict')
            rules = tuple(rule for rule in checkpoint.rules if rule.task.task_id not in cancelled) + tuple(additions)
            ExpertIntake(scope=scope, tasks=(rule.task.model_dump() for rule in rules), evidence=checkpoint.catalog())
            pending = tuple(name for name in checkpoint.pending if name not in cancelled) + tuple(rule.task.task_id for rule in additions)
            updated = checkpoint.model_copy(update={'rules': rules, 'pending': pending,
                                                    'status': 'ACTIVE' if pending else 'SETTLED'})
            if not within(updated.limit, add_budget((updated.spent, updated.reserved()))):
                return self._stop_checkpoint(db, checkpoint, 'budget_exhausted')
            return self._save_checkpoint(db, updated, 'graph_revised', reason=reason,
                added_tasks=[rule.task.task_id for rule in additions], cancelled_tasks=list(cancellations),
                evidence_refs=list(evidence_refs))

    @contextmanager
    def _checkpoint_write(self):
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @staticmethod
    def _checkpoint_plan(db, scope: ExpertScope) -> dict:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_studies'").fetchone():
            study = db.execute('SELECT project_id,scope_json,snapshot_json FROM research_studies WHERE case_id=? AND session_id=?',
                               (scope.case_id, scope.session_id)).fetchone()
            if study:
                if study['project_id'] != scope.project_id or json.loads(study['scope_json']) != scope.model_dump():
                    raise CheckpointConflict('research_scope_changed')
                return json.loads(study['snapshot_json'])
        row = db.execute('SELECT project_id, plan_json FROM sessions WHERE id=?', (scope.session_id,)).fetchone()
        if row is None or row['project_id'] != scope.project_id or not row['plan_json']:
            raise CheckpointConflict('session_or_project_mismatch')
        plan = json.loads(row['plan_json'])
        if plan.get('plan_id') != scope.plan_id:
            raise CheckpointConflict('plan_identity_changed')
        return plan

    @staticmethod
    def _load_checkpoint(db, session_id: str, case_id: str) -> RevisionCheckpoint:
        row = db.execute('SELECT payload_json FROM expert_checkpoints WHERE session_id=? AND case_id=?',
                         (session_id, case_id)).fetchone()
        if row is None:
            raise KeyError(case_id)
        return RevisionCheckpoint.model_validate_json(row['payload_json'])

    def _checkpoint_row(self, db, scope: ExpertScope) -> RevisionCheckpoint:
        checkpoint = self._load_checkpoint(db, scope.session_id, scope.case_id)
        if checkpoint.scope != scope:
            raise CheckpointConflict('stale_coordinator_attempt')
        return checkpoint

    @staticmethod
    def _require_checkpoint_plan(checkpoint: RevisionCheckpoint, plan: dict) -> None:
        if execution_digest(plan) != execution_digest(checkpoint.plan):
            raise CheckpointConflict('plan_changed')

    def _save_checkpoint(self, db, checkpoint: RevisionCheckpoint, kind: str, **facts) -> RevisionCheckpoint:
        scope = checkpoint.scope
        db.execute('''INSERT INTO expert_checkpoints VALUES(?,?,?)
                      ON CONFLICT(session_id,case_id) DO UPDATE SET payload_json=excluded.payload_json''',
                   (scope.session_id, scope.case_id, checkpoint.model_dump_json()))
        self._append_event(db, scope.session_id, f'experts.{kind}', {
            'scope': scope.model_dump(mode='json'), 'status': checkpoint.status,
            'spent': checkpoint.spent.model_dump(), 'reserved': checkpoint.reserved().model_dump(), **facts,
        })
        return checkpoint

    def create_checkpoint(self, scope: ExpertScope, rules: tuple[TaskRule, ...], limit: Budget,
                          references: tuple[EvidenceRef, ...] = ()) -> RevisionCheckpoint:
        if any(item.kind != 'reference' for item in references):
            raise CheckpointConflict('initial_references_only')
        ExpertIntake(scope=scope, tasks=(rule.task.model_dump() for rule in rules), evidence=references)
        with self._checkpoint_write() as db:
            self._require_scientific_scope(db, scope)
            if db.execute('SELECT 1 FROM expert_checkpoints WHERE session_id=? AND case_id=?',
                          (scope.session_id, scope.case_id)).fetchone():
                raise CheckpointConflict('checkpoint_exists')
            plan = self._checkpoint_plan(db, scope)
            if type(plan.get('revision')) is not int or plan['revision'] != scope.plan_revision:
                raise CheckpointConflict('plan_changed')
            affordable = within(limit, add_budget(rule.task.budget_estimate for rule in rules))
            checkpoint = RevisionCheckpoint(
                schema_version=1, scope=scope, plan=plan, rules=rules, references=references, verified={},
                issued_evidence_ids=tuple(item.evidence_id for item in references),
                pending=tuple(rule.task.task_id for rule in rules) if affordable else (), in_flight=(),
                limit=limit, spent=Budget(agent_calls=0, solver_runs=0), status='ACTIVE' if affordable else 'STOPPED',
                stop_reason=None if affordable else 'budget_exhausted',
            )
            return self._save_checkpoint(db, checkpoint, 'checkpoint_created', reason=checkpoint.stop_reason)

    def read_checkpoint(self, scope: ExpertScope) -> RevisionCheckpoint:
        with self._connect() as db:
            return self._checkpoint_row(db, scope)

    def load_checkpoint(self, project_id: str, session_id: str, case_id: str) -> RevisionCheckpoint:
        """进程重启后用稳定身份查找当前尝试；所有写入仍须校验完整身份。"""
        with self._connect() as db:
            checkpoint = self._load_checkpoint(db, session_id, case_id)
            if checkpoint.scope.project_id != project_id:
                raise KeyError(case_id)
            return checkpoint

    def begin_checkpoint_dispatch(self, scope: ExpertScope) -> str:
        with self._checkpoint_write() as db:
            self._require_scientific_scope(db, scope)
            checkpoint = self._checkpoint_row(db, scope)
            self._require_checkpoint_plan(checkpoint, self._checkpoint_plan(db, scope))
            if checkpoint.status != 'ACTIVE' or checkpoint.dispatch_token or checkpoint.in_flight:
                raise CheckpointConflict('dispatch_unavailable')
            token = uuid4().hex
            self._save_checkpoint(db, checkpoint.model_copy(update={'dispatch_token': token}), 'dispatch_started')
            return token

    def finish_checkpoint_dispatch(self, scope: ExpertScope, token: str, *, failed: bool = False) -> RevisionCheckpoint:
        with self._checkpoint_write() as db:
            checkpoint = self._checkpoint_row(db, scope)
            if checkpoint.status == 'STOPPED':
                return checkpoint
            if checkpoint.dispatch_token != token:
                raise CheckpointConflict('dispatch_revoked')
            try:
                self._require_checkpoint_plan(checkpoint, self._checkpoint_plan(db, scope))
            except CheckpointConflict:
                return self._stop_checkpoint(db, checkpoint, 'plan_changed')
            if failed:
                unknown = any(checkpoint.task(name).budget_estimate.solver_runs for name in checkpoint.in_flight)
                return self._stop_checkpoint(db, checkpoint, 'submission_unknown' if unknown else 'task_failed')
            return self._save_checkpoint(db, checkpoint.model_copy(update={'dispatch_token': None}), 'dispatch_finished')

    def claim_checkpoint_task(self, scope: ExpertScope, task_id: str, *, token: str | None = None) -> None:
        with self._checkpoint_write() as db:
            self._require_scientific_scope(db, scope)
            checkpoint = self._checkpoint_row(db, scope)
            self._require_checkpoint_plan(checkpoint, self._checkpoint_plan(db, scope))
            if checkpoint.dispatch_token != token or task_id not in checkpoint.ready():
                raise CheckpointConflict('task_not_ready_or_already_claimed')
            spent = add_budget((checkpoint.spent, checkpoint.task(task_id).budget_estimate))
            updated = checkpoint.model_copy(update={
                'pending': tuple(name for name in checkpoint.pending if name != task_id),
                'in_flight': (*checkpoint.in_flight, task_id), 'spent': spent,
            })
            self._save_checkpoint(db, updated, 'task_claimed', task_id=task_id)

    def assert_checkpoint_claim(self, scope: ExpertScope, task_id: str, token: str) -> None:
        with self._connect() as db:
            db.execute('BEGIN')
            self._require_scientific_scope(db, scope)
            checkpoint = self._checkpoint_row(db, scope)
            self._require_checkpoint_plan(checkpoint, self._checkpoint_plan(db, scope))
            if (checkpoint.status != 'ACTIVE' or checkpoint.dispatch_token != token
                    or task_id not in checkpoint.in_flight):
                raise CheckpointConflict('task_claim_revoked')

    def publish_checkpoint_task(self, result: ExpertResult, evidence: tuple[EvidenceRef, ...] = (),
                                *, token: str | None = None) -> None:
        """总控提交经校验的结果及宿主已准入的产物引用。"""
        scope, task_id = result.scope, result.task_id
        with self._checkpoint_write() as db:
            self._require_scientific_scope(db, scope)
            checkpoint = self._checkpoint_row(db, scope)
            self._require_checkpoint_plan(checkpoint, self._checkpoint_plan(db, scope))
            if (checkpoint.status != 'ACTIVE' or checkpoint.dispatch_token != token
                    or task_id not in checkpoint.in_flight):
                raise CheckpointConflict('task_not_in_flight')
            task = checkpoint.task(task_id)
            visible = set(task.input_evidence_refs)
            for dep in task.dependencies:
                previous = checkpoint.verified[dep]
                visible.update(ref for claim in previous.result.claims for ref in claim.evidence_refs)
                visible.update(ref.evidence_id for ref in previous.evidence)
            known = {ref.evidence_id for ref in checkpoint.catalog()} & visible
            if len({claim.claim_id for claim in result.claims}) != len(result.claims) or any(
                    set(claim.evidence_refs) - known for claim in result.claims):
                raise CheckpointConflict('result_evidence_outside_task')
            ids = tuple(item.evidence_id for item in evidence)
            if len(set(ids)) != len(ids) or set(ids).intersection(checkpoint.issued_evidence_ids):
                raise CheckpointConflict('evidence_id_already_issued')
            record = VerifiedTask(result=result, evidence=evidence)
            in_flight = tuple(name for name in checkpoint.in_flight if name != task_id)
            updated = checkpoint.model_copy(update={
                'verified': {**checkpoint.verified, task_id: record}, 'in_flight': in_flight,
                'issued_evidence_ids': (*checkpoint.issued_evidence_ids, *ids),
                'status': 'ACTIVE' if in_flight or checkpoint.pending else 'SETTLED',
            })
            self._save_checkpoint(db, updated, 'task_verified', task_id=task_id, record=record.model_dump(mode='json'))

    def _stop_checkpoint(self, db, checkpoint: RevisionCheckpoint, reason: StopReason, **facts) -> RevisionCheckpoint:
        updated = checkpoint.model_copy(update={
            'status': 'STOPPED', 'stop_reason': reason, 'pending': (), 'dispatch_token': None,
        })
        return self._save_checkpoint(db, updated, 'checkpoint_stopped', reason=reason, **facts)

    def stop_checkpoint(self, scope: ExpertScope, *, reason: StopReason = 'cancelled') -> RevisionCheckpoint:
        with self._checkpoint_write() as db:
            checkpoint = self._checkpoint_row(db, scope)
            return self._stop_checkpoint(db, checkpoint, reason) if checkpoint.status != 'STOPPED' else checkpoint

    def resume_checkpoint(self, scope: ExpertScope) -> RevisionCheckpoint:
        """恢复只重新授权未派发任务；已经写入派发账的任务不会重放。"""
        with self._checkpoint_write() as db:
            checkpoint = self._checkpoint_row(db, scope)
            if db.execute('SELECT 1 FROM run_journal WHERE run_id=?', (scope.run_id,)).fetchone():
                # 真实科学尝试的恢复由原运行账本裁决，协调器不能轮换出第二个身份。
                return self._stop_checkpoint(db, checkpoint,
                    'submission_unknown' if checkpoint.in_flight else 'task_failed')
            if checkpoint.status == 'STOPPED' or (checkpoint.status == 'SETTLED' and not checkpoint.dispatch_token):
                return checkpoint
            try:
                self._require_checkpoint_plan(checkpoint, self._checkpoint_plan(db, scope))
            except CheckpointConflict:
                return self._stop_checkpoint(db, checkpoint, 'plan_changed')
            rotated = checkpoint.model_copy(update={
                'scope': scope.model_copy(update={'attempt_id': uuid4().hex}), 'dispatch_token': None,
            })
            if rotated.in_flight:
                return self._stop_checkpoint(db, rotated, 'submission_unknown')
            return self._save_checkpoint(db, rotated, 'checkpoint_resumed', previous_attempt_id=scope.attempt_id)
