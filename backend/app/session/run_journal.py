"""Durable lifecycle authority, separate from the frozen scientific verdict."""
import hashlib
import json
import uuid
import os
from datetime import UTC, datetime


class RunCanceled(RuntimeError):
    pass


class StaleAttempt(RuntimeError):
    pass


RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_journal (
 run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, attempt_id TEXT,
 phase TEXT NOT NULL, plan_hash TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
 force_cancel INTEGER NOT NULL DEFAULT 0, outcome TEXT, artifacts TEXT NOT NULL DEFAULT '{}',
 updated_at TEXT NOT NULL, FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS run_attempts (
 run_id TEXT NOT NULL, attempt_id TEXT NOT NULL, phase TEXT NOT NULL,
 outcome TEXT, worker TEXT NOT NULL DEFAULT '{}', artifacts TEXT NOT NULL DEFAULT '{}',
 cleanup TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id, attempt_id)
);
CREATE TABLE IF NOT EXISTS comsol_owner (
 slot INTEGER PRIMARY KEY CHECK(slot=1), owner_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comsol_owner_identity (
 owner_id TEXT PRIMARY KEY, launcher TEXT NOT NULL, worker TEXT NOT NULL DEFAULT '{}'
);
"""


class RunJournalMixin:
    def _journal_event(self, db, sid, kind, payload):
        now = datetime.now(UTC).isoformat()
        db.execute('INSERT INTO events(session_id,event_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)',
                   (sid, str(uuid.uuid4()), kind, json.dumps(payload, ensure_ascii=False), now))

    def begin_run(self, session_id, run_id, plan):
        from app.comsol.owned_process import OwnedProcess
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM comsol_owner').fetchone():
                raise RuntimeError('COMSOL_OWNER_BUSY: active or orphaned owner; no concurrent model mutation')
            db.execute('INSERT INTO comsol_owner VALUES(1,?)', (run_id,))
            db.execute('INSERT INTO comsol_owner_identity(owner_id,launcher) VALUES(?,?)', (run_id, json.dumps(OwnedProcess.identify(os.getpid()))))
            db.execute('INSERT INTO run_journal(run_id,session_id,phase,plan_hash,updated_at) VALUES(?,?,?,?,?)',
                       (run_id, session_id, 'PRECHECK', digest, datetime.now(UTC).isoformat()))
            self._journal_event(db, session_id, 'run.started', {'run_id': run_id, 'plan_hash': digest})

    def get_run(self, run_id):
        with self._connect() as db:
            row = db.execute('SELECT * FROM run_journal WHERE run_id=?', (run_id,)).fetchone()
            if row is None: raise KeyError(run_id)
            result = dict(row)
            result['cancel_requested'] = bool(result['cancel_requested'])
            result['artifacts'] = json.loads(result['artifacts'])
            result['attempts'] = []
            for attempt in db.execute('SELECT * FROM run_attempts WHERE run_id=? ORDER BY rowid', (run_id,)):
                item = dict(attempt)
                for field in ('worker', 'cleanup', 'artifacts'): item[field] = json.loads(item[field])
                result['attempts'].append(item)
            return result

    def latest_run(self, session_id):
        with self._connect() as db:
            row = db.execute('SELECT run_id FROM run_journal WHERE session_id=? ORDER BY rowid DESC LIMIT 1', (session_id,)).fetchone()
        return self.get_run(row['run_id']) if row else None

    @staticmethod
    def _check_run(row, attempt_id=None):
        if row is None: raise StaleAttempt('run_missing')
        if row['cancel_requested']: raise RunCanceled('run-level cancellation requested')
        if row['outcome'] is not None or (attempt_id is not None and row['attempt_id'] != attempt_id):
            raise StaleAttempt('stale_or_terminal_attempt')

    def assert_current(self, run_id, attempt_id=None):
        with self._connect() as db:
            row = db.execute('SELECT * FROM run_journal WHERE run_id=?', (run_id,)).fetchone()
            self._check_run(row, attempt_id)
            if attempt_id:
                attempt = db.execute('SELECT outcome FROM run_attempts WHERE run_id=? AND attempt_id=?', (run_id, attempt_id)).fetchone()
                if not attempt or attempt['outcome'] is not None: raise StaleAttempt('attempt_not_active')

    def attempt_is_current(self, run_id, attempt_id):
        try: self.assert_current(run_id, attempt_id); return True
        except (RunCanceled, StaleAttempt): return False

    def begin_attempt(self, run_id):
        attempt_id = str(uuid.uuid4())
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM run_journal WHERE run_id=?', (run_id,)).fetchone()
            self._check_run(row)
            previous = db.execute('SELECT * FROM run_attempts WHERE run_id=? ORDER BY rowid DESC LIMIT 1', (run_id,)).fetchone()
            if previous:
                worker, cleanup = json.loads(previous['worker']), json.loads(previous['cleanup'])
                if previous['outcome'] != 'RetryableFailure' or (worker and not cleanup.get('reaped')):
                    raise StaleAttempt('previous attempt is active or orphaned')
            db.execute('INSERT INTO run_attempts(run_id,attempt_id,phase) VALUES(?,?,?)', (run_id, attempt_id, 'BUILDING'))
            db.execute('UPDATE run_journal SET attempt_id=?,phase=? WHERE run_id=?', (attempt_id, 'BUILDING', run_id))
            self._journal_event(db, row['session_id'], 'run.attempt', {'run_id': run_id, 'attempt_id': attempt_id})
        return attempt_id

    def update_run(self, run_id, attempt_id, *, phase=None, worker=None, artifacts=None, cleanup=None):
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM run_journal WHERE run_id=?', (run_id,)).fetchone()
            if not row or row['attempt_id'] != attempt_id: raise StaleAttempt('stale journal update')
            # Cleanup remains recordable after cancellation; no stale phase can revive a run.
            if phase:
                self._check_run(row, attempt_id)
                db.execute('UPDATE run_journal SET phase=? WHERE run_id=?', (phase, run_id))
                db.execute('UPDATE run_attempts SET phase=? WHERE run_id=? AND attempt_id=?', (phase, run_id, attempt_id))
            for column, value in [('worker', worker), ('artifacts', artifacts), ('cleanup', cleanup)]:
                if value is not None:
                    db.execute(f'UPDATE run_attempts SET {column}=? WHERE run_id=? AND attempt_id=?', (json.dumps(value), run_id, attempt_id))
            if artifacts is not None:
                db.execute('UPDATE run_journal SET artifacts=? WHERE run_id=?', (json.dumps(artifacts), run_id))

    def request_cancel(self, session_id, *, force=False):
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM run_journal WHERE session_id=? ORDER BY rowid DESC LIMIT 1', (session_id,)).fetchone()
            if not row or row['outcome'] is not None:
                return {'cancel_requested': False, 'status': 'not_running', 'cancelled': False}
            db.execute('UPDATE run_journal SET cancel_requested=1,force_cancel=MAX(force_cancel,?),phase=? WHERE run_id=?', (int(force), 'CANCEL_REQUESTED', row['run_id']))
            db.execute('UPDATE sessions SET state=? WHERE id=?', ('CANCEL_REQUESTED', session_id))
            payload = {'run_id': row['run_id'], 'attempt_id': row['attempt_id'], 'cancel_requested': True, 'force': force, 'state': 'CANCEL_REQUESTED'}
            self._journal_event(db, session_id, 'run.cancel_requested', payload)
            self._journal_event(db, session_id, 'state.changed', {'state': 'CANCEL_REQUESTED', 'error': None})
            return payload

    def end_attempt(self, run_id, attempt_id, outcome):
        if outcome not in ('RetryableFailure', 'FatalFailure', 'Canceled'): raise ValueError(outcome)
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM run_journal WHERE run_id=?', (run_id,)).fetchone()
            self._check_run(row, attempt_id)
            db.execute('UPDATE run_attempts SET outcome=? WHERE run_id=? AND attempt_id=?', (outcome, run_id, attempt_id))
            self._journal_event(db, row['session_id'], 'run.attempt_ended', {'run_id': run_id, 'attempt_id': attempt_id, 'outcome': outcome})

    def finish_run(self, run_id, attempt_id, outcome):
        if outcome not in ('Completed', 'Canceled', 'FatalFailure', 'Interrupted', 'PrecheckRejected'): raise ValueError(outcome)
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM run_journal WHERE run_id=?', (run_id,)).fetchone()
            if not row or row['attempt_id'] != attempt_id: raise StaleAttempt('stale finish')
            if row['outcome'] is not None: raise StaleAttempt('already terminal')
            if outcome == 'Completed':
                self._check_run(row, attempt_id)
                active = db.execute('SELECT outcome FROM run_attempts WHERE run_id=? AND attempt_id=?', (run_id, attempt_id)).fetchone()
                if not active or active['outcome'] is not None: raise StaleAttempt('attempt already ended')
            if row['cancel_requested']: outcome = 'Canceled'
            phase = {'Completed': 'COMPLETED', 'Canceled': 'CANCELED', 'FatalFailure': 'FAILED', 'Interrupted': 'INTERRUPTED', 'PrecheckRejected': 'VALIDATION_FAILED'}[outcome]
            db.execute('UPDATE run_journal SET outcome=?,phase=? WHERE run_id=?', (outcome, phase, run_id))
            db.execute('UPDATE run_attempts SET outcome=?,phase=? WHERE run_id=? AND attempt_id=?', (outcome, phase, run_id, attempt_id))
            attempts = db.execute('SELECT worker,cleanup FROM run_attempts WHERE run_id=?', (run_id,)).fetchall()
            orphaned = any(json.loads(a['worker']) and not json.loads(a['cleanup']).get('reaped') for a in attempts)
            if outcome == 'Completed' and orphaned:
                raise StaleAttempt('owned worker cleanup not proven; completion refused')
            if not orphaned: db.execute('DELETE FROM comsol_owner WHERE owner_id=?', (run_id,))
            lifecycle = {'run_id': run_id, 'attempt_id': attempt_id, 'outcome': outcome, 'state': phase, 'orphaned': orphaned, 'cancel_requested': bool(row['cancel_requested'])}
            self._journal_event(db, row['session_id'], 'run.lifecycle', lifecycle)
            db.execute('UPDATE sessions SET state=? WHERE id=?', (phase, row['session_id']))
            self._journal_event(db, row['session_id'], 'state.changed', {'state': phase, 'error': None})
        return {'run_id': run_id, 'attempt_id': attempt_id, 'outcome': outcome, 'state': phase, 'cancel_requested': bool(row['cancel_requested']), 'orphaned': orphaned}

    def acquire_bridge_owner(self, owner_id):
        from app.comsol.owned_process import OwnedProcess
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT owner_id FROM comsol_owner').fetchone()
            if row and row['owner_id'] != owner_id: raise RuntimeError('COMSOL_OWNER_BUSY')
            if not row: db.execute('INSERT INTO comsol_owner VALUES(1,?)', (owner_id,))
            db.execute('INSERT OR IGNORE INTO comsol_owner_identity(owner_id,launcher) VALUES(?,?)', (owner_id, json.dumps(OwnedProcess.identify(os.getpid()))))

    def record_bridge_process(self, owner_id, identity):
        with self._lock, self._connect() as db:
            db.execute('UPDATE comsol_owner_identity SET worker=? WHERE owner_id=?', (json.dumps(identity), owner_id))

    def release_bridge_owner(self, owner_id):
        with self._lock, self._connect() as db:
            db.execute('DELETE FROM comsol_owner WHERE owner_id=?', (owner_id,))
            db.execute('DELETE FROM comsol_owner_identity WHERE owner_id=?', (owner_id,))

    def reconcile_active_runs(self):
        """Revoke uncertain attempts; never infer success from artifact existence.

        Startup only. Surviving/unverifiable identities remain orphaned and retain
        ownership. We never kill a recovered PID merely because it was journaled.
        """
        from app.comsol.owned_process import OwnedProcess
        results = []
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT * FROM run_journal WHERE outcome IS NULL').fetchall()
            for row in rows:
                sid, rid = row['session_id'], row['run_id']
                origin = db.execute('SELECT launcher FROM comsol_owner_identity WHERE owner_id=?', (rid,)).fetchone()
                launcher = json.loads(origin['launcher']) if origin else {}
                if launcher.get('pid') != os.getpid() and OwnedProcess.identity_alive(launcher) is True:
                    # A second runtime must not revoke a still-live owner's run.
                    continue
                orphaned = False
                for attempt in db.execute('SELECT * FROM run_attempts WHERE run_id=?', (rid,)).fetchall():
                    worker = json.loads(attempt['worker'])
                    alive = bool(worker) and OwnedProcess.identity_alive(worker) is not False
                    orphaned |= alive
                    cleanup = {'reaped': not alive, 'status': 'orphaned' if alive else 'not_alive_at_reconciliation'}
                    db.execute('UPDATE run_attempts SET outcome=?,phase=?,cleanup=? WHERE run_id=? AND attempt_id=?',
                               ('Interrupted', 'ORPHANED' if alive else 'INTERRUPTED', json.dumps(cleanup), rid, attempt['attempt_id']))
                state = 'CANCELED' if row['cancel_requested'] else 'INTERRUPTED'
                outcome = 'Canceled' if row['cancel_requested'] else 'Interrupted'
                db.execute('UPDATE run_journal SET phase=?,outcome=? WHERE run_id=?', (state, outcome, rid))
                lifecycle = {'run_id': rid, 'attempt_id': row['attempt_id'], 'state': state, 'outcome': outcome, 'orphaned': orphaned, 'cancel_requested': bool(row['cancel_requested'])}
                model = {'run_lifecycle': lifecycle, 'results': ['Interrupted execution; scientific completion not established']}
                db.execute('UPDATE sessions SET state=?,error=?,model_json=? WHERE id=?', (state, 'Runtime interrupted; previous attempt cannot certify success', json.dumps(model), sid))
                for kind, payload in [('run.reconciled', lifecycle), ('state.changed', {'state': state, 'error': 'Execution interrupted'}), ('model.updated', model),
                                      ('timeline.completed', {'id': 'recovery-' + rid, 'phase': 'Recovery', 'category': 'SYSTEM', 'status': 'error', 'label': state, 'run_lifecycle': lifecycle})]:
                    self._journal_event(db, sid, kind, payload)
                if not orphaned: db.execute('DELETE FROM comsol_owner WHERE owner_id=?', (rid,))
                results.append(lifecycle)
            # Pre-journal versions could leave an active session behind.
            active = ('PRECHECK', 'BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION', 'VALIDATING', 'EXPORTING', 'REPORTING', 'REPAIRING', 'REVALIDATING', 'CANCEL_REQUESTED')
            for row in db.execute('SELECT id,state FROM sessions WHERE id NOT IN (SELECT session_id FROM run_journal WHERE outcome IS NULL)').fetchall():
                if row['state'] in active:
                    db.execute('UPDATE sessions SET state=?,error=?,model_json=NULL WHERE id=?', ('INTERRUPTED', 'Unproven execution at startup', row['id']))
                    self._journal_event(db, row['id'], 'state.changed', {'state': 'INTERRUPTED', 'error': 'Unproven execution at startup'})
            owner = db.execute('SELECT owner_id FROM comsol_owner').fetchone()
            if owner:
                oid = owner['owner_id']
                identity = db.execute('SELECT * FROM comsol_owner_identity WHERE owner_id=?', (oid,)).fetchone()
                if oid == 'bridge' and identity:
                    launcher, worker = json.loads(identity['launcher']), json.loads(identity['worker'])
                    if OwnedProcess.identity_alive(launcher) is False and OwnedProcess.identity_alive(worker) is False:
                        db.execute('DELETE FROM comsol_owner WHERE owner_id=?', (oid,))
                        db.execute('DELETE FROM comsol_owner_identity WHERE owner_id=?', (oid,))
                elif oid != 'bridge':
                    terminal = db.execute('SELECT outcome FROM run_journal WHERE run_id=?', (oid,)).fetchone()
                    if terminal and terminal['outcome'] is not None:
                        attempts = db.execute('SELECT worker FROM run_attempts WHERE run_id=?', (oid,)).fetchall()
                        if all(OwnedProcess.identity_alive(json.loads(a['worker'])) is False for a in attempts):
                            db.execute('DELETE FROM comsol_owner WHERE owner_id=?', (oid,))
                            db.execute('DELETE FROM comsol_owner_identity WHERE owner_id=?', (oid,))
        return results
