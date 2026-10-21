"""Durable, provenance-bearing state used to reconstruct model context.

Conversation text remains an audit trail. It is deliberately excluded from the
authoritative snapshot so replacing an LLM session cannot change model facts.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


STATE_GROUNDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_state_facts (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 fact_id TEXT NOT NULL UNIQUE,
 session_id TEXT NOT NULL,
 fact_key TEXT NOT NULL,
 value_json TEXT NOT NULL,
 provenance TEXT NOT NULL,
 run_id TEXT,
 attempt_id TEXT,
 created_at TEXT NOT NULL,
 FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_model_state_facts_session_key
 ON model_state_facts(session_id, fact_key, sequence);
CREATE TABLE IF NOT EXISTS context_snapshots (
 snapshot_id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL,
 canonical_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_context_snapshots_session
 ON context_snapshots(session_id, created_at);
"""


class Provenance(StrEnum):
    USER_REQUEST = 'USER_REQUEST'
    PLAN = 'PLAN'
    COMSOL_READBACK = 'COMSOL_READBACK'
    DETERMINISTIC_CHECK = 'DETERMINISTIC_CHECK'
    SOLVER_RESULT = 'SOLVER_RESULT'
    HUMAN_CORRECTION = 'HUMAN_CORRECTION'


_PRECEDENCE = {
    Provenance.USER_REQUEST: 10,
    Provenance.PLAN: 20,
    Provenance.SOLVER_RESULT: 30,
    Provenance.DETERMINISTIC_CHECK: 40,
    Provenance.HUMAN_CORRECTION: 50,
    Provenance.COMSOL_READBACK: 60,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ModelStateLedger:
    def __init__(self, store: Any) -> None:
        self.store = store

    def append(self, session_id: str, fact_key: str, value: Any, provenance: Provenance,
               *, run_id: str | None = None, attempt_id: str | None = None) -> str:
        if not fact_key.strip():
            raise ValueError('fact_key is required')
        provenance = Provenance(provenance)
        fact_id = str(uuid.uuid4())
        with self.store._lock, self.store._connect() as db:
            db.execute(
                'INSERT INTO model_state_facts(fact_id,session_id,fact_key,value_json,provenance,run_id,attempt_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (fact_id, session_id, fact_key, json.dumps(value, ensure_ascii=False, sort_keys=True), provenance.value, run_id, attempt_id, _now()),
            )
        self.store.append_event(session_id, 'state.fact_appended', {
            'fact_id': fact_id, 'fact_key': fact_key, 'provenance': provenance.value,
            'run_id': run_id, 'attempt_id': attempt_id,
        })
        return fact_id

    def resolved(self, session_id: str) -> dict[str, dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute(
                'SELECT * FROM model_state_facts WHERE session_id=? ORDER BY sequence', (session_id,)
            ).fetchall()
        selected: dict[str, Any] = {}
        for row in rows:
            current = selected.get(row['fact_key'])
            candidate = (_PRECEDENCE[Provenance(row['provenance'])], row['sequence'])
            if current is None or candidate >= current[0]:
                selected[row['fact_key']] = (candidate, row)
        return {
            key: {
                'value': json.loads(row['value_json']), 'provenance': row['provenance'],
                'fact_id': row['fact_id'], 'run_id': row['run_id'],
                'attempt_id': row['attempt_id'], 'created_at': row['created_at'],
            }
            for key, (_, row) in selected.items()
        }


class EventLedger:
    """Typed façade over the existing append-only event stream."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> str:
        return self.store.append_event(session_id, event_type, payload)

    def tail(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute(
                'SELECT * FROM events WHERE session_id=? ORDER BY sequence DESC LIMIT ?',
                (session_id, max(1, min(limit, 100))),
            ).fetchall()
        return [self.store._event_from_row(row) for row in reversed(rows)]


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    schema_version: int
    session_id: str
    state: str
    plan: dict[str, Any] | None
    model: dict[str, Any] | None
    facts: dict[str, dict[str, Any]]
    active_run: dict[str, Any] | None
    assumptions: list[dict[str, Any]]
    validated_memories: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    canonical_hash: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def rebuild_context_snapshot(store: Any, session_id: str, *, max_events: int = 20,
                             persist: bool = True) -> ContextSnapshot:
    from app.memory.store import MemoryStore
    from app.session.assumptions import AssumptionLedger

    session = store.get(session_id)
    facts = ModelStateLedger(store).resolved(session_id)
    grounded_model = dict(session.get('model') or {})
    for key, fact in facts.items():
        if key.startswith('model.'):
            grounded_model[key.removeprefix('model.')] = fact['value']
    event_tail = EventLedger(store).tail(session_id, min(100, max_events * 3))
    events = [event for event in event_tail if event['type'] != 'message'][-max_events:]
    active = store.latest_run(session_id)
    if active and active.get('outcome') is not None:
        active = None
    plan = session.get('plan') or {}
    workflow_id = str(plan.get('workflow_id') or '')
    version_scope = str(plan.get('version') or '1.1')
    payload = {
        'schema_version': 2, 'session_id': session_id, 'state': str(session['state']),
        'plan': session.get('plan'), 'model': grounded_model or None, 'facts': facts,
        'active_run': active,
        'assumptions': AssumptionLedger(store).active(session_id),
        'validated_memories': MemoryStore(store).relevant(workflow_id, version_scope=version_scope) if workflow_id else [],
        'recent_events': events,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(encoded).hexdigest()
    snapshot = ContextSnapshot(**payload, canonical_hash=digest)
    if persist:
        with store._lock, store._connect() as db:
            db.execute(
                'INSERT INTO context_snapshots(snapshot_id,session_id,canonical_hash,payload_json,created_at) VALUES(?,?,?,?,?)',
                (str(uuid.uuid4()), session_id, digest, json.dumps(snapshot.as_dict(), ensure_ascii=False, sort_keys=True), _now()),
            )
    return snapshot
