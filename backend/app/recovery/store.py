"""同一个 SessionStore 中追加诊断事件，不复制科学运行或审批状态。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import sqlite3
from uuid import uuid4

from app.recovery.ingest import canonical, digest
from app.recovery.schemas import (
    Cause, DiagnosticSnapshot, FailureEvent, FailureScope, KnowledgeEntry, State,
)
from app.recovery.http_store import HTTP_SCHEMA, HttpRecoveryStoreMixin

RECOVERY_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS recovery_cases (
 case_id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
 state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 budget INTEGER NOT NULL DEFAULT 2, updated_at TEXT NOT NULL,
 report_json TEXT, proposal_json TEXT, snapshot_json TEXT
);
CREATE INDEX IF NOT EXISTS recovery_scope ON recovery_cases(project_id, session_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS recovery_events (
 event_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES recovery_cases(case_id),
 kind TEXT NOT NULL, time TEXT NOT NULL, payload_json TEXT NOT NULL, sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recovery_case_history ON recovery_events(case_id, time, event_id);
CREATE TABLE IF NOT EXISTS recovery_library (
 project_id TEXT NOT NULL, entry_id TEXT NOT NULL, origin TEXT NOT NULL, cause TEXT NOT NULL,
 status TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(project_id, entry_id)
);
CREATE INDEX IF NOT EXISTS recovery_library_lookup ON recovery_library(project_id, origin, cause, status);
CREATE TABLE IF NOT EXISTS recovery_actions (
 proposal_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES recovery_cases(case_id),
 approval_ref TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL
);
""" + HTTP_SCHEMA + """
COMMIT;
"""

_ALLOWED = {
    State.DETECTED: {State.SNAPSHOTTING, State.ESCALATED},
    State.SNAPSHOTTING: {State.CLASSIFIED},
    State.CLASSIFIED: {State.DIAGNOSING},
    State.DIAGNOSING: {State.PROPOSED, State.BLOCKED, State.ESCALATED},
    State.PROPOSED: {State.WAITING, State.BLOCKED},
    State.WAITING: {State.SIMULATING, State.BLOCKED},
    State.SIMULATING: {State.VERIFYING, State.UNRESOLVED},
    State.VERIFYING: {State.RESOLVED, State.UNRESOLVED},
}
_IN_PROGRESS = {State.SNAPSHOTTING, State.CLASSIFIED, State.DIAGNOSING, State.SIMULATING, State.VERIFYING}
_TERMINAL = {State.RESOLVED, State.UNRESOLVED, State.CANCELLED, State.EXPIRED, State.BLOCKED, State.ESCALATED}


class RecoveryConflict(ValueError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def _case_signature(event: FailureEvent) -> str:
    return digest([event.request_fingerprint, event.facts.model_dump(mode='json', exclude_none=True)])


class RecoveryStoreMixin(HttpRecoveryStoreMixin):
    @contextmanager
    def _recovery_write(self):
        # 短事务；故障采集不等待常规写连接的 20 秒超时。
        if not self._lock.acquire(timeout=0.25):
            raise sqlite3.OperationalError('recovery_database_busy')
        try:
            with self._connect() as db:
                db.execute('PRAGMA busy_timeout=250')
                db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            self._lock.release()

    def note_recovery_drop(self):
        with self._recovery_drop_lock:
            self.recovery_drops += 1

    def recovery_scope(self, *, session_id=None, project_id=None, plan_id=None, plan_revision=None,
                       run_id=None, attempt_id=None, expert_task_id=None, expert_attempt_id=None):
        with self._connect() as db:
            db.execute('PRAGMA busy_timeout=250')
            if session_id:
                row = db.execute('SELECT project_id,plan_json FROM sessions WHERE id=?', (session_id,)).fetchone()
                if not row or (project_id and project_id != row['project_id']):
                    raise KeyError('session_scope')
                project_id = row['project_id']
                plan = json.loads(row['plan_json'] or '{}')
                if plan_id is None:
                    plan_id = plan.get('plan_id')
                if plan_revision is None:
                    plan_revision = plan.get('revision') or None
            if run_id:
                run = db.execute('SELECT session_id FROM run_journal WHERE run_id=?', (run_id,)).fetchone()
                if not run or run['session_id'] != session_id:
                    raise KeyError('run_scope')
                if attempt_id and not db.execute('SELECT 1 FROM run_attempts WHERE run_id=? AND attempt_id=?',
                                                (run_id, attempt_id)).fetchone():
                    raise KeyError('attempt_scope')
        return FailureScope(project_id=project_id, session_id=session_id, plan_id=plan_id,
                            plan_revision=plan_revision, scientific_run_id=run_id, scientific_attempt_id=attempt_id,
                            expert_task_id=expert_task_id, expert_attempt_id=expert_attempt_id)

    @staticmethod
    def _recovery_append(db, case_id, kind, value, *, event_id=None, time=None):
        raw = canonical(value)
        db.execute('INSERT INTO recovery_events VALUES(?,?,?,?,?,?)',
                   (event_id or uuid4().hex, case_id, kind, time or now(), raw, digest(value)))

    def ingest_recovery(self, event: FailureEvent) -> str | None:
        payload = event.model_dump(mode='json')
        with self._recovery_write() as db:
            prior = db.execute('SELECT case_id,payload_json,kind FROM recovery_events WHERE event_id=?',
                               (event.event_id,)).fetchone()
            if prior:
                expected = json.loads(prior['payload_json'])
                if prior['kind'] != 'failure' or any(expected.get(k) != v for k, v in payload.items()
                                                     if k != 'ingested_at_utc'):
                    raise RecoveryConflict('event_identity_conflict')
                return prior['case_id']
            if event.scope.session_id:
                owner = db.execute('SELECT project_id FROM sessions WHERE id=?',
                                   (event.scope.session_id,)).fetchone()
                if not owner or owner['project_id'] != event.scope.project_id:
                    raise KeyError('session_scope')
            if event.scope.scientific_run_id:
                run = db.execute('SELECT session_id FROM run_journal WHERE run_id=?',
                                 (event.scope.scientific_run_id,)).fetchone()
                if not run or run['session_id'] != event.scope.session_id:
                    raise KeyError('run_scope')
                if event.scope.scientific_attempt_id and not db.execute(
                    'SELECT 1 FROM run_attempts WHERE run_id=? AND attempt_id=?',
                    (event.scope.scientific_run_id, event.scope.scientific_attempt_id)
                ).fetchone():
                    raise KeyError('attempt_scope')
            count = db.execute('SELECT COUNT(*) FROM recovery_cases WHERE project_id IS ?',
                               (event.scope.project_id,)).fetchone()[0]
            recent = db.execute("""SELECT COUNT(*) FROM recovery_cases c JOIN recovery_events e ON e.event_id=c.case_id
                                   WHERE c.project_id IS ? AND e.time>=?""",
                                (event.scope.project_id, (datetime.now(UTC) - timedelta(minutes=1)).isoformat())
                                ).fetchone()[0]
            if count >= 5000 or recent >= 120:
                self.note_recovery_drop()
                return None
            case_id = event.event_id
            db.execute('INSERT INTO recovery_cases(case_id,project_id,session_id,state,updated_at) VALUES(?,?,?,?,?)',
                       (case_id, event.scope.project_id, event.scope.session_id, State.DETECTED, now()))
            self._recovery_append(db, case_id, 'failure', payload, event_id=event.event_id)
            if event.scope.session_id:
                self._append_event(db, event.scope.session_id, 'recovery.detected', {'case_id': case_id})
            return case_id

    def recovery_cases_for(self, project_id: str, session_id: str):
        with self._connect() as db:
            self._recovery_check_session(db, project_id, session_id)
            return [dict(row) for row in db.execute(
                """SELECT c.case_id,c.session_id,c.state,c.updated_at,e.payload_json
                   FROM recovery_cases c JOIN recovery_events e ON e.event_id=c.case_id
                   WHERE c.project_id=? AND (c.session_id=? OR c.session_id IS NULL)
                   ORDER BY c.updated_at DESC LIMIT 40""", (project_id, session_id))]

    def recovery_record(self, case_id: str, project_id: str, session_id: str):
        with self._connect() as db:
            return self._recovery_record(db, case_id, project_id, session_id)

    @staticmethod
    def _recovery_record(db, case_id, project_id, session_id):
        RecoveryStoreMixin._recovery_check_session(db, project_id, session_id)
        row = db.execute("""SELECT * FROM recovery_cases WHERE case_id=? AND project_id=?
                            AND (session_id=? OR session_id IS NULL)""",
                         (case_id, project_id, session_id)).fetchone()
        if not row:
            raise KeyError('case_scope')
        history = db.execute('SELECT * FROM recovery_events WHERE case_id=? ORDER BY rowid LIMIT 80',
                             (case_id,)).fetchall()
        result = {key: row[key] for key in ('case_id', 'state', 'revision', 'budget', 'updated_at')}
        for key in ('report', 'proposal', 'snapshot'):
            value = row[key + '_json']
            result[key] = json.loads(value) if value else None
        result['events'] = [{**dict(item), 'payload_json': json.loads(item['payload_json'])} for item in history]
        result['failure'] = result['events'][0]['payload_json']
        review = (RecoveryStoreMixin._read_recovery_review(db, session_id, result['proposal']['proposal_id'])
                  if result['proposal'] else None)
        result['review_decision'] = json.loads(review['decision_json'])['decision'] if review and review['decision_json'] else None
        result['proposal_status'] = None
        if result['proposal']:
            result['proposal_status'] = (
                'EXPIRED' if result['state'] in {State.EXPIRED, State.CANCELLED}
                else 'REJECTED' if result['review_decision'] == 'reject'
                else 'APPROVED_FOR_SIMULATION' if result['review_decision'] == 'approve'
                else 'WAITING_APPROVAL' if result['state'] == State.WAITING else 'DRAFT'
            )
        return result

    @staticmethod
    def _read_recovery_review(db, session_id, proposal_id):
        from app.session.reviews import ReviewStoreMixin
        return ReviewStoreMixin._recovery_review(db, session_id, proposal_id)

    @staticmethod
    def _recovery_check_session(db, project_id, session_id):
        row = db.execute('SELECT project_id FROM sessions WHERE id=?', (session_id,)).fetchone()
        if not row or row['project_id'] != project_id:
            raise KeyError('session_scope')

    def recovery_snapshot(self, scope: FailureScope):
        with self._connect() as db:
            return self._recovery_snapshot(db, scope)

    @staticmethod
    def _recovery_snapshot(db, scope):
        plan = {}
        if scope.session_id:
            row = db.execute('SELECT project_id,plan_json FROM sessions WHERE id=?', (scope.session_id,)).fetchone()
            if not row or row['project_id'] != scope.project_id:
                raise KeyError('session_scope')
            plan = json.loads(row['plan_json'] or '{}')
        run = None
        if scope.scientific_run_id:
            run = db.execute('SELECT * FROM run_journal WHERE run_id=? AND session_id=?',
                             (scope.scientific_run_id, scope.session_id)).fetchone()
        elif scope.session_id:
            run = db.execute('SELECT * FROM run_journal WHERE session_id=? ORDER BY rowid DESC LIMIT 1',
                             (scope.session_id,)).fetchone()
            # 计划诊断不绑定过去已结束的科学运行。
            if run and run['outcome'] is not None:
                run = None
        owner = db.execute('SELECT owner_id FROM comsol_owner WHERE slot=1').fetchone()
        state = 'none'
        if run:
            attempt = db.execute('SELECT outcome,cleanup FROM run_attempts WHERE run_id=? AND attempt_id=?',
                                 (run['run_id'], run['attempt_id'])).fetchone()
            orphan = attempt and json.loads(attempt['cleanup']).get('status') == 'orphaned'
            state = ('orphaned' if orphan else 'cancelled' if run['cancel_requested'] else
                     'terminal' if run['outcome'] is not None or (attempt and attempt['outcome'] is not None)
                     else 'active')
        body = dict(plan_revision=plan.get('revision') or None, plan_id=plan.get('plan_id'),
                    plan_hash=digest(plan) if plan else None,
                    run_id=run['run_id'] if run else None, attempt_id=run['attempt_id'] if run else None,
                    run_state=state, owner_matches=(bool(run and owner['owner_id'] == run['run_id']) if owner else None))
        return DiagnosticSnapshot(schema_version=1, snapshot_id=uuid4().hex, collected_at=now(),
                                  source='session_store_and_run_journal', sha256=digest(body), **body)

    @staticmethod
    def _recovery_check_fresh(snapshot, scope):
        if snapshot.plan_id != scope.plan_id or snapshot.plan_revision != scope.plan_revision:
            raise RecoveryConflict('stale_plan')
        if snapshot.run_state == 'cancelled':
            raise RecoveryConflict('cancelled')
        if scope.scientific_run_id and (snapshot.run_id != scope.scientific_run_id
                                        or snapshot.attempt_id != scope.scientific_attempt_id
                                        or snapshot.run_state != 'active'):
            raise RecoveryConflict('stale_or_terminal_attempt')

    @staticmethod
    def _recovery_check_owner(snapshot, scope):
        if scope.scientific_run_id:
            valid = snapshot.run_state == 'active' and snapshot.owner_matches is True
        else:
            valid = snapshot.run_state == 'none' and snapshot.owner_matches is None
        if not valid:
            raise RecoveryConflict('owner_not_proven')

    def recovery_transition(self, case_id, project_id, session_id, expected, revision, target, *,
                            snapshot=None, report=None, proposal=None, reason=None, check_scope=None,
                            consume=False, action=None, review_proposal=None, verification=None,
                            verification_snapshot=None):
        if (target not in _ALLOWED.get(expected, set())
            and not (expected not in _TERMINAL and target in {State.CANCELLED, State.EXPIRED, State.ESCALATED})):
            raise RecoveryConflict('invalid_state_transition')
        with self._recovery_write() as db:
            record = self._recovery_record(db, case_id, project_id, session_id)
            if record['state'] != expected or record['revision'] != revision:
                raise RecoveryConflict('recovery_state_changed')
            if consume and record['budget'] <= 0:
                raise RecoveryConflict('diagnostic_budget_exhausted')
            if check_scope is not None:
                current = self._recovery_snapshot(db, check_scope)
                self._recovery_check_fresh(current, check_scope)
                if action or review_proposal or expected in {State.SIMULATING, State.VERIFYING}:
                    self._recovery_check_owner(current, check_scope)
                if (action or review_proposal) and current.sha256 != record['snapshot']['sha256']:
                    raise RecoveryConflict('stale_plan')
                if verification_snapshot and current.sha256 != verification_snapshot.sha256:
                    raise RecoveryConflict('stale_or_terminal_attempt')
            if review_proposal:
                self._request_recovery_review(db, session_id, check_scope, review_proposal)
            if action:
                proof = self._recovery_review(db, session_id, action.approval_ref)
                self._check_recovery_review(proof, record['proposal'], check_scope)
                if not proof['decision_json'] or json.loads(proof['decision_json']).get('decision') != 'approve':
                    raise RecoveryConflict('simulation_approval_required')
                if action.digest != record['proposal']['digest'] or action.proposal_id != action.approval_ref:
                    raise RecoveryConflict('proposal_digest_changed')
                db.execute('INSERT INTO recovery_actions VALUES(?,?,?,?,?)',
                           (action.proposal_id, case_id, action.approval_ref, action.digest, 'CLAIMED'))
            if expected in {State.SIMULATING, State.VERIFYING} and target in _TERMINAL:
                db.execute('UPDATE recovery_actions SET status=? WHERE case_id=?', (target, case_id))
            values = {
                name: canonical(value.model_dump(mode='json')) if value else None
                for name, value in [('snapshot', snapshot), ('report', report), ('proposal', proposal)]
            }
            db.execute("""UPDATE recovery_cases SET state=?,revision=revision+1,budget=budget-?,updated_at=?,
                          snapshot_json=coalesce(?,snapshot_json),report_json=coalesce(?,report_json),
                          proposal_json=coalesce(?,proposal_json) WHERE case_id=?""",
                       (target, int(consume), now(), values['snapshot'], values['report'], values['proposal'], case_id))
            for name, value in values.items():
                if value:
                    self._recovery_append(db, case_id, name, json.loads(value))
            if verification and verification_snapshot:
                self._recovery_append(db, case_id, 'verification_snapshot', verification_snapshot.model_dump(mode='json'))
                self._recovery_append(db, case_id, 'verification', verification.model_dump(mode='json'))
            self._recovery_append(db, case_id, 'transition', {'from': expected, 'to': target, 'reason': reason})
            return self._recovery_record(db, case_id, project_id, session_id)

    @staticmethod
    def _check_recovery_review(proof, proposal, scope):
        if proof is None:
            raise RecoveryConflict('simulation_approval_required')
        binding = json.loads(proof['payload_json'])
        if (binding['digest'] != proposal['digest'] or binding['caseId'] != proposal['case_id']
            or binding['planRevision'] != scope.plan_revision or proof['run_id'] != scope.scientific_run_id
            or proof['attempt_id'] != scope.scientific_attempt_id):
            raise RecoveryConflict('proposal_digest_changed')

    def decide_recovery_review(self, project_id, session_id, proposal, scope, decision):
        if decision not in {'approve', 'reject'}:
            raise RecoveryConflict('invalid_review_decision')
        with self._recovery_write() as db:
            record = self._recovery_record(db, proposal.case_id, project_id, session_id)
            if record['state'] != State.WAITING:
                raise RecoveryConflict('proposal_not_current')
            current = self._recovery_snapshot(db, scope)
            self._recovery_check_fresh(current, scope)
            self._recovery_check_owner(current, scope)
            if current.sha256 != record['snapshot']['sha256']:
                raise RecoveryConflict('stale_plan')
            proof = self._recovery_review(db, session_id, proposal.proposal_id)
            self._check_recovery_review(proof, record['proposal'], scope)
            if proof['state'] != 'OPEN':
                if json.loads(proof['decision_json'])['decision'] == decision:
                    return
                raise RecoveryConflict('review_already_decided')
            self._resolve_recovery_review(db, session_id, proposal.proposal_id, decision)
            self._recovery_append(db, proposal.case_id, 'review', {'digest': proposal.digest, 'decision': decision})

    def recover_recovery_cases(self):
        with self._recovery_write() as db:
            for state in _IN_PROGRESS:
                for row in db.execute('SELECT case_id FROM recovery_cases WHERE state=?', (state,)).fetchall():
                    case_id = row['case_id']
                    target = State.UNRESOLVED if state in {State.SIMULATING, State.VERIFYING} else State.ESCALATED
                    db.execute('UPDATE recovery_cases SET state=?,revision=revision+1,updated_at=? WHERE case_id=?',
                               (target, now(), case_id))
                    db.execute("UPDATE recovery_actions SET status='UNRESOLVED' WHERE case_id=?", (case_id,))
                    self._recovery_append(db, case_id, 'transition',
                                          {'from': state, 'to': target, 'reason': 'restart_no_replay'})

    def recovery_verified_knowledge(self, event: FailureEvent, cause: Cause):
        if not event.scope.project_id:
            return ()
        with self._connect() as db:
            rows = db.execute("""SELECT entry_id,cause,payload_json FROM recovery_library WHERE project_id=? AND origin=?
                                  AND (? OR cause=?) AND status='VERIFIED_FIX'
                                  AND CASE WHEN json_valid(payload_json)
                                      THEN json_extract(payload_json, '$.case_signature') END=?
                                  ORDER BY entry_id LIMIT 12""",
                              (event.scope.project_id, event.origin, cause == Cause.UNKNOWN, cause,
                               _case_signature(event))).fetchall()
        entries=[]
        for row in rows:
            try:
                entry=KnowledgeEntry.model_validate_json(row['payload_json'])
                valid=(entry.entry_id==row['entry_id'] and entry.cause==row['cause'] and entry.origin==event.origin
                    and entry.status=='VERIFIED_FIX' and entry.verification_mode=='observed'
                    and entry.evidence_refs and entry.verification_refs and entry.reviewer and entry.reviewed_at
                    and not set(entry.evidence_refs)&set(entry.verification_refs))
                if valid and (not entry.versions or event.facts.engine_version in entry.versions):
                    entries.append(entry)
            except (ValueError,TypeError):
                # 损坏的缓存不能阻断确定性诊断，也不能把自标记升级成有效修复。
                continue
        return tuple(entries)

    def admit_recovery_knowledge(self, project_id, session_id, case_id, entry: KnowledgeEntry, *, verification=None):
        """仅宿主可调用。已验证修复须有独立观测，模拟结果不升级。"""
        with self._recovery_write() as db:
            record = self._recovery_record(db, case_id, project_id, session_id)
            failure = FailureEvent.model_validate(record['failure'])
            if entry.origin != failure.origin:
                raise RecoveryConflict('knowledge_origin_mismatch')
            known = {item['event_id'] for item in record['events']}
            if record['snapshot']:
                known.add(record['snapshot']['snapshot_id'])
            if not set(entry.evidence_refs + entry.verification_refs) <= known:
                raise RecoveryConflict('knowledge_evidence_outside_case')
            if entry.status == 'VERIFIED_FIX' and not (
                verification and verification.independent is True and verification.passed is True
                and verification.mode == 'observed' and verification.case_id == case_id
                and set(entry.verification_refs) == set(verification.evidence_refs)
                and entry.verification_refs and entry.evidence_refs and entry.reviewer and entry.reviewed_at
                and not set(entry.evidence_refs) & set(entry.verification_refs)
                and entry.verification_mode == 'observed'
            ):
                raise RecoveryConflict('verified_fix_requires_independent_observation')
            entry = entry.model_copy(update={'case_signature': _case_signature(failure)})
            db.execute('INSERT INTO recovery_library VALUES(?,?,?,?,?,?)',
                       (project_id, entry.entry_id, entry.origin, entry.cause, entry.status,
                        canonical(entry.model_dump(mode='json'))))
