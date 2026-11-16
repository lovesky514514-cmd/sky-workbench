"""研究讨论与真实科学 RUN 分账，同库保存提案和模型开销，不授予 RUN 权限。"""
import json
import os
from uuid import uuid4

from app.session.store import utc_now

SCHEMA = '''
CREATE TABLE IF NOT EXISTS research_studies (
 case_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), project_id TEXT NOT NULL,
 request_key TEXT NOT NULL, scope_json TEXT NOT NULL, goal_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 status TEXT NOT NULL, outcome_json TEXT, created_at TEXT NOT NULL,
 UNIQUE(session_id,request_key)
);
CREATE TABLE IF NOT EXISTS research_model_calls (
 call_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES research_studies(case_id), task_id TEXT NOT NULL,
 reserved_tokens INTEGER NOT NULL, status TEXT NOT NULL, receipt_json TEXT, created_at TEXT NOT NULL,
 UNIQUE(case_id,task_id)
);
'''


class ResearchStore:
    def __init__(self, sessions):
        self.sessions = sessions
        with sessions._connect() as db:
            db.executescript(SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(research_studies)')}
            if 'launcher_json' not in columns:
                db.execute("ALTER TABLE research_studies ADD COLUMN launcher_json TEXT NOT NULL DEFAULT '{}'")
            if 'proposal_case_id' not in columns:
                db.execute('ALTER TABLE research_studies ADD COLUMN proposal_case_id TEXT')

    def find(self, session_id, request_key):
        with self.sessions._connect() as db:
            row = db.execute('SELECT case_id FROM research_studies WHERE session_id=? AND request_key=?',
                             (session_id, request_key)).fetchone()
        return self.read(row['case_id']) if row else None

    def claim_plan(self, parent_id, proposed_id):
        """先原子领取，重复/重启不能再次消耗同一研究讨论的提案预算。"""
        with self.sessions._checkpoint_write() as db:
            parent = db.execute('SELECT * FROM research_studies WHERE case_id=?', (parent_id,)).fetchone()
            if parent is None or parent['status'] not in {'READY_FOR_PLAN', 'NEEDS_EVIDENCE', 'DEGRADED'}:
                raise ValueError('parent_research_not_ready')
            if parent['proposal_case_id']:
                return parent['proposal_case_id']
            session = db.execute('SELECT project_id,plan_json FROM sessions WHERE id=?', (parent['session_id'],)).fetchone()
            if session is None or session['project_id'] != parent['project_id']:
                raise PermissionError('research_project_changed')
            plan = json.loads(session['plan_json'] or '{}')
            if json.loads(parent['goal_json'])['session_plan_binding'] != [plan.get('plan_id'), plan.get('revision')]:
                raise ValueError('parent_research_plan_changed')
            db.execute('UPDATE research_studies SET proposal_case_id=? WHERE case_id=?', (proposed_id, parent_id))
            return proposed_id

    def create(self, scope, request_key, goal):
        snapshot = {'plan_id': scope.plan_id, 'revision': scope.plan_revision, 'runnable': False, 'goal': goal}
        with self.sessions._checkpoint_write() as db:
            session = db.execute('SELECT project_id,plan_json FROM sessions WHERE id=?', (scope.session_id,)).fetchone()
            if session is None or session['project_id'] != scope.project_id:
                raise PermissionError('research_project_or_session_mismatch')
            plan = json.loads(session['plan_json'] or '{}')
            goal['session_plan_binding'] = [plan.get('plan_id'), plan.get('revision')]
            db.execute('''INSERT INTO research_studies
                (case_id,session_id,project_id,request_key,scope_json,goal_json,snapshot_json,status,outcome_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (scope.case_id, scope.session_id, scope.project_id, request_key, scope.model_dump_json(),
                 json.dumps(goal, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False), 'ACTIVE', None, utc_now()))
            from app.comsol.owned_process import OwnedProcess
            db.execute('UPDATE research_studies SET launcher_json=? WHERE case_id=?',
                       (json.dumps(OwnedProcess.identify(os.getpid())), scope.case_id))
        return self.read(scope.case_id)

    def read(self, case_id):
        with self.sessions._connect() as db:
            row = db.execute('SELECT * FROM research_studies WHERE case_id=?', (case_id,)).fetchone()
            if row is None:
                raise KeyError(case_id)
            result = dict(row)
            result.pop('launcher_json', None)
            for field in ('scope', 'goal', 'snapshot', 'outcome'):
                raw = result.pop(field + '_json')
                result[field] = json.loads(raw) if raw else None
            result['calls'] = [dict(row) for row in db.execute('SELECT * FROM research_model_calls WHERE case_id=? ORDER BY rowid', (case_id,))]
            for call in result['calls']:
                call['receipt'] = json.loads(call.pop('receipt_json') or '{}')
            return result

    def guard(self, scope):
        with self.sessions._connect() as db:
            self._guard(db, scope)

    @staticmethod
    def _guard(db, scope):
        row = db.execute('''SELECT r.scope_json,r.status,r.goal_json,s.project_id,s.plan_json
                            FROM research_studies r JOIN sessions s ON r.session_id=s.id WHERE r.case_id=?''',
                         (scope.case_id,)).fetchone()
        if row is None or row['status'] != 'ACTIVE' or json.loads(row['scope_json']) != scope.model_dump():
            raise PermissionError('research_attempt_inactive')
        plan = json.loads(row['plan_json'] or '{}')
        goal = json.loads(row['goal_json'])
        if row['project_id'] != scope.project_id or goal.get('session_plan_binding') != [plan.get('plan_id'), plan.get('revision')]:
            raise PermissionError('research_project_or_plan_binding_changed')

    def reserve(self, scope, task_id, tokens, limit):
        with self.sessions._checkpoint_write() as db:
            self._guard(db, scope)
            rows = db.execute('SELECT reserved_tokens FROM research_model_calls WHERE case_id=?', (scope.case_id,)).fetchall()
            if len(rows) >= limit.model_calls or sum(row['reserved_tokens'] for row in rows) + tokens > limit.token_limit:
                raise ValueError('research_model_budget_exhausted')
            call_id = uuid4().hex
            db.execute('INSERT INTO research_model_calls VALUES(?,?,?,?,?,?,?)',
                       (call_id, scope.case_id, task_id, tokens, 'RESERVED', None, utc_now()))
            return call_id

    def receipt(self, call_id, status, data):
        with self.sessions._checkpoint_write() as db:
            row = db.execute('SELECT status FROM research_model_calls WHERE call_id=?', (call_id,)).fetchone()
            if row is None or row['status'] != 'RESERVED':
                raise ValueError('duplicate_or_unknown_model_receipt')
            db.execute('UPDATE research_model_calls SET status=?,receipt_json=? WHERE call_id=?',
                       (status, json.dumps(data, ensure_ascii=False), call_id))

    def finish(self, scope, status, outcome):
        with self.sessions._checkpoint_write() as db:
            # 失败/取消也必须能封账；旧计划的结果只记拒收，不恢复 ACTIVE。
            row = db.execute('SELECT status,scope_json FROM research_studies WHERE case_id=?', (scope.case_id,)).fetchone()
            if row is None or row['status'] != 'ACTIVE' or json.loads(row['scope_json']) != scope.model_dump():
                raise PermissionError('research_attempt_inactive')
            if status in {'READY_FOR_PLAN', 'NEEDS_EVIDENCE'}:
                self._guard(db, scope)
            db.execute('UPDATE research_studies SET status=?,outcome_json=? WHERE case_id=?',
                       (status, json.dumps(outcome, ensure_ascii=False), scope.case_id))
        return self.read(scope.case_id)

    def interrupt_active(self):
        """启动时不补发没有回执的请求，不创建/重放科学 RUN。"""
        with self.sessions._checkpoint_write() as db:
            rows = db.execute("SELECT scope_json,launcher_json FROM research_studies WHERE status='ACTIVE'").fetchall()
            interrupted = 0
            for row in rows:
                from app.comsol.owned_process import OwnedProcess
                launcher = json.loads(row['launcher_json'])
                if launcher.get('pid') != os.getpid() and OwnedProcess.identity_alive(launcher) is not False:
                    continue
                scope = json.loads(row['scope_json'])
                db.execute("UPDATE research_studies SET status='INTERRUPTED' WHERE case_id=?", (scope['case_id'],))
                saved = db.execute('SELECT payload_json FROM expert_checkpoints WHERE session_id=? AND case_id=?',
                                   (scope['session_id'], scope['case_id'])).fetchone()
                if saved:
                    from app.execution.revision_checkpoint import RevisionCheckpoint
                    self.sessions._stop_checkpoint(db, RevisionCheckpoint.model_validate_json(saved['payload_json']), 'submission_unknown')
                interrupted += 1
            return interrupted
