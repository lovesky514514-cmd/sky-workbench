"""会话级模拟标记、操作版本和报告。与真实 RUN 账本分开。"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from uuid import uuid4

from app.execution.revision_store import CheckpointConflict
from app.scientific.execution_contract import write_binding

HEAT_SIMULATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS heat_simulations (
 session_id TEXT PRIMARY KEY REFERENCES sessions(id),
 simulation_id TEXT NOT NULL UNIQUE, scenario TEXT NOT NULL, state TEXT NOT NULL,
 version INTEGER NOT NULL, report_json TEXT, reason TEXT
);
"""


class HeatSimulationStoreMixin:
    def read_heat_simulation(self, session_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute('SELECT * FROM heat_simulations WHERE session_id=?', (session_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['report'] = json.loads(result.pop('report_json') or 'null')
        return result

    def has_heat_simulation(self, session_id: str) -> bool:
        with self._connect() as db:
            return db.execute('SELECT 1 FROM heat_simulations WHERE session_id=?', (session_id,)).fetchone() is not None

    def start_heat_simulation(self, session_id: str, project_id: str, scenario: str, plan: dict) -> None:
        with self._checkpoint_write() as db:
            session = db.execute('SELECT project_id,plan_json FROM sessions WHERE id=?', (session_id,)).fetchone()
            if session is None or session['project_id'] != project_id:
                raise KeyError(session_id)
            if session['plan_json'] is not None or db.execute(
                'SELECT 1 FROM run_journal WHERE session_id=? LIMIT 1', (session_id,)
            ).fetchone():
                raise CheckpointConflict('empty_session_required')
            simulation_id = 'simulation-' + uuid4().hex
            db.execute("INSERT INTO heat_simulations VALUES(?,?,?,'RUNNING',1,NULL,NULL)",
                       (session_id, simulation_id, scenario))
            db.execute("UPDATE sessions SET plan_json=?,state='PLAN_READY',updated_at=? WHERE id=?",
                       (json.dumps(plan, ensure_ascii=False, allow_nan=False), datetime.now(UTC).isoformat(), session_id))
            write_binding(db, session_id, plan)
            self._append_event(db, session_id, 'plan.updated', plan)
            self._append_event(db, session_id, 'heat_simulation.started', {
                'mode': 'simulation', 'simulation_id': simulation_id, 'scenario': scenario, 'version': 1,
            })

    def update_heat_simulation(self, session_id: str, version: int, state: str, *, reason: str | None = None,
                               report: dict | None = None) -> int:
        with self._checkpoint_write() as db:
            updated = db.execute(
                '''UPDATE heat_simulations SET state=?,version=version+1,reason=?,
                   report_json=COALESCE(?,report_json) WHERE session_id=? AND version=?''',
                (state, reason, json.dumps(report, ensure_ascii=False, allow_nan=False) if report is not None else None,
                 session_id, version),
            )
            if updated.rowcount != 1:
                raise CheckpointConflict('simulation_version_changed')
            self._append_event(db, session_id, 'heat_simulation.state', {
                'mode': 'simulation', 'state': state, 'version': version + 1, 'reason': reason,
                'audit_status': report['status'] if report else None,
            })
            if report is not None:
                self._append_event(db, session_id, 'heat_simulation.audit', {'mode': 'simulation', 'report': report})
            return version + 1
