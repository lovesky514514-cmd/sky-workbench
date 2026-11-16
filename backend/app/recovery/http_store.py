"""同一 SessionStore 中的 HTTP 发送预算，不建立第二套科学任务状态机。"""
from datetime import UTC, datetime, timedelta
import json
import math

from app.recovery.ingest import canonical, digest


HTTP_SCHEMA = """
CREATE TABLE IF NOT EXISTS recovery_http_operations (
 operation_id TEXT PRIMARY KEY, case_id TEXT, scope_json TEXT NOT NULL,
 fingerprint TEXT NOT NULL, snapshot_sha TEXT NOT NULL, maximum INTEGER NOT NULL,
 used INTEGER NOT NULL DEFAULT 0, deadline TEXT NOT NULL, status TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 0, audit_json TEXT NOT NULL DEFAULT '[]', not_before TEXT, owner_sha TEXT
);
"""


class HttpRecoveryStoreMixin:
    @staticmethod
    def initialize_http_recovery(db):
        columns = {row[1] for row in db.execute('PRAGMA table_info(recovery_http_operations)')}
        if 'not_before' not in columns:
            db.execute('ALTER TABLE recovery_http_operations ADD COLUMN not_before TEXT')
        if 'owner_sha' not in columns:
            db.execute('ALTER TABLE recovery_http_operations ADD COLUMN owner_sha TEXT')

    @staticmethod
    def _http_owner_digest(db):
        row = db.execute('SELECT o.owner_id,i.launcher,i.worker FROM comsol_owner o LEFT JOIN comsol_owner_identity i ON o.owner_id=i.owner_id').fetchone()
        return digest(dict(row) if row else {})

    def _http_case_guard(self, db, row, scope):
        if row['case_id']:
            record = self._recovery_record(db, row['case_id'], scope.project_id, scope.session_id)
            if record['state'] in {'CANCELLED','EXPIRED','BLOCKED','ESCALATED','RESOLVED'}:
                raise ValueError('http_case_not_current')

    def _http_guard(self, db, scope):
        current = self._recovery_snapshot(db, scope)
        self._recovery_check_fresh(current, scope)
        try:
            self._recovery_check_owner(current, scope)
        except ValueError:
            # 手动连接的 Bridge 也占用原有 owner 槽位，但没有科学 RUN。
            # 仅在原账本与存活进程相符、模型在本工作项目内时允许只读健康核查。
            from app.comsol.bridge_process import java_bridge_process_manager
            from app.comsol.owned_process import OwnedProcess
            from app.config import WORKSPACE_ROOT
            from pathlib import Path
            row = db.execute("SELECT i.worker FROM comsol_owner o JOIN comsol_owner_identity i ON o.owner_id=i.owner_id WHERE o.owner_id='bridge'").fetchone()
            state = java_bridge_process_manager.status()
            worker = json.loads(row['worker']) if row else {}
            model = Path(state.get('model_path') or '.').resolve()
            if (scope.scientific_run_id or scope.project_id != 'workbench' or current.run_state != 'none'
                or not model.is_relative_to(WORKSPACE_ROOT.resolve()) or not model.is_file()
                or state.get('state') != 'ready' or state.get('owner_id') != 'bridge'
                or state.get('running') is not True or worker.get('pid') != state.get('pid')
                or not OwnedProcess.identity_alive(worker)):
                raise
        return current

    def open_http_operation(self, operation_id, scope, fingerprint, *, case_id=None, maximum=3, timeout=12):
        if not 1 <= maximum <= 3 or not 0 < timeout <= 30:
            raise ValueError('http_budget_invalid')
        binding = canonical(scope.model_dump(mode='json'))
        with self._recovery_write() as db:
            snapshot = self._http_guard(db, scope)
            if case_id:
                self._recovery_record(db, case_id, scope.project_id, scope.session_id)
            row = db.execute('SELECT * FROM recovery_http_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if row:
                if row['scope_json'] != binding or row['fingerprint'] != fingerprint or row['case_id'] != case_id:
                    raise ValueError('http_operation_identity_conflict')
                # 旧操作不能通过重新打开重置次数、期限或在途状态。
                return dict(row)
            db.execute('INSERT INTO recovery_http_operations(operation_id,case_id,scope_json,fingerprint,snapshot_sha,maximum,deadline,status,owner_sha) VALUES(?,?,?,?,?,?,?,?,?)',
                       (operation_id, case_id, binding, fingerprint, snapshot.sha256, maximum,
                        (datetime.now(UTC) + timedelta(seconds=timeout)).isoformat(), 'READY',self._http_owner_digest(db)))
            return dict(db.execute('SELECT * FROM recovery_http_operations WHERE operation_id=?', (operation_id,)).fetchone())

    def claim_http_send(self, operation_id, scope):
        with self._recovery_write() as db:
            snapshot = self._http_guard(db, scope)
            row = db.execute('SELECT * FROM recovery_http_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if not row or row['scope_json'] != canonical(scope.model_dump(mode='json')):
                raise ValueError('http_operation_identity_conflict')
            self._http_case_guard(db, row, scope)
            if row['snapshot_sha'] != snapshot.sha256 or row['owner_sha'] != self._http_owner_digest(db):
                raise ValueError('http_state_changed')
            if row['active'] or row['status'] not in {'READY','WAITING'}:
                raise ValueError('http_operation_not_replayable')
            if datetime.fromisoformat(row['deadline']) <= datetime.now(UTC):
                raise ValueError('http_deadline_exceeded')
            if row['not_before'] and datetime.fromisoformat(row['not_before']) > datetime.now(UTC):
                raise ValueError('http_cooldown_active')
            if row['used'] >= row['maximum']:
                raise ValueError('http_budget_exhausted')
            db.execute("UPDATE recovery_http_operations SET used=used+1,active=1,status='SENDING' WHERE operation_id=?", (operation_id,))
            return row['used'] + 1

    def record_http_attempt(self, operation_id, scope, observation, decision, *, retry, delay=0):
        if not math.isfinite(delay) or not 0 <= delay <= 31536000:
            raise ValueError('http_delay_invalid')
        with self._recovery_write() as db:
            snapshot = self._http_guard(db, scope)
            row = db.execute('SELECT * FROM recovery_http_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if not row or not row['active'] or row['scope_json'] != canonical(scope.model_dump(mode='json')):
                raise ValueError('http_operation_identity_conflict')
            self._http_case_guard(db, row, scope)
            if row['snapshot_sha'] != snapshot.sha256 or row['owner_sha'] != self._http_owner_digest(db):
                raise ValueError('http_state_changed')
            audit = json.loads(row['audit_json'])
            item = {'attempt': row['used'], 'time': datetime.now(UTC).isoformat(),
                    'observation': observation.model_dump(mode='json'), 'decision': decision.model_dump(mode='json')}
            audit.append(item)
            status = 'WAITING' if retry and row['used'] < row['maximum'] else 'COMPLETED'
            not_before = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat() if status == 'WAITING' else None
            db.execute('UPDATE recovery_http_operations SET active=0,status=?,audit_json=?,not_before=? WHERE operation_id=?',
                       (status, canonical(audit), not_before, operation_id))
            if row['case_id']:
                self._recovery_append(db, row['case_id'], 'http_observation', item)
            return status == 'WAITING'

    def stop_http_operation(self, operation_id, scope, *, cancelled=False):
        with self._recovery_write() as db:
            db.execute("UPDATE recovery_http_operations SET active=0,status=? WHERE operation_id=? AND scope_json=? AND status!='COMPLETED'",
                       ('CANCELLED' if cancelled else 'STOPPED', operation_id, canonical(scope.model_dump(mode='json'))))

    def http_operation(self, operation_id, scope):
        with self._connect() as db:
            row = db.execute('SELECT * FROM recovery_http_operations WHERE operation_id=? AND scope_json=?',
                             (operation_id, canonical(scope.model_dump(mode='json')))).fetchone()
            if not row:
                raise KeyError('http_operation_not_found')
            return dict(row)
