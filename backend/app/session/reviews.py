"""Durable typed human review facts, never scientific certification."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any


REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_requests (
 review_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL, state TEXT NOT NULL, payload_json TEXT NOT NULL,
 decision_json TEXT, FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_review_session_state
 ON review_requests(session_id, state);
"""

_ACTIONS = frozenset({'approve', 'modify', 'reject', 'defer'})
_DECISIONS = _ACTIONS


def _time() -> str:
    return datetime.now(UTC).isoformat()


def _validate_review(value: dict[str, Any]) -> None:
    required = ('reviewId', 'runId', 'attemptId', 'affectedObjectId', 'unit',
                'reason', 'downstreamImpact', 'allowedActions', 'currentValue', 'proposedValue')
    for key in required:
        if key not in value or value[key] in (None, ''):
            raise ValueError(f'review {key} is required')
    for key in ('reviewId', 'runId', 'attemptId', 'affectedObjectId', 'unit', 'reason', 'downstreamImpact'):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f'review {key} is required')
    if not isinstance(value['allowedActions'], list) or not value['allowedActions'] \
            or not set(value['allowedActions']) <= _ACTIONS:
        raise ValueError('review allowedActions are invalid')
    for key in ('currentValue', 'proposedValue'):
        candidate = value[key]
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float, str)):
            raise ValueError(f'review {key} is invalid')
        if isinstance(candidate, (int, float)) and not math.isfinite(candidate):
            raise ValueError(f'review {key} is nonfinite')
    feature = value.get('boundaryFeature')
    if feature is not None:
        if not isinstance(feature, dict) or not all(
            isinstance(feature.get(key), str) and feature[key]
            for key in ('physicsInterfaceId', 'featureId', 'selectionIdentity', 'propertyKey')
        ):
            raise ValueError('review boundaryFeature identity is invalid')


class ReviewStoreMixin:
    def create_review(self, session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        _validate_review(data)
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute('SELECT run_id,attempt_id,outcome,cancel_requested FROM run_journal '
                             'WHERE session_id=? ORDER BY rowid DESC LIMIT 1', (session_id,)).fetchone()
            if not run or run['outcome'] is not None or run['cancel_requested']:
                raise ValueError('review requires active run')
            if data['runId'] != run['run_id'] or data['attemptId'] != run['attempt_id']:
                raise ValueError('review run/attempt is stale')
            created = {key: data[key] for key in (
                'reviewId', 'runId', 'attemptId', 'affectedObjectId', 'currentValue',
                'proposedValue', 'unit', 'reason', 'downstreamImpact', 'allowedActions')}
            if 'boundaryFeature' in data:
                created['boundaryFeature'] = dict(data['boundaryFeature'])
            created.update(createdAt=_time(), state='OPEN')
            db.execute('INSERT INTO review_requests VALUES(?,?,?,?,?,?,NULL)',
                       (created['reviewId'], session_id, created['runId'], created['attemptId'],
                        'OPEN', json.dumps(created, ensure_ascii=False)))
            # Same transaction: a required event can never exist without its record.
            self._journal_event(db, session_id, 'review.required', created)
        return created

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute('SELECT payload_json,state FROM review_requests WHERE review_id=?',
                             (review_id,)).fetchone()
        return {**json.loads(row['payload_json']), 'state': row['state']} if row else None

    def open_review(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute('SELECT payload_json FROM review_requests WHERE session_id=? AND state=? '
                             'ORDER BY rowid DESC LIMIT 1', (session_id, 'OPEN')).fetchone()
        return json.loads(row['payload_json']) if row else None

    def resolve_review(self, session_id: str, review_id: str, decision: str,
                       edited_value: int | float | str | None = None) -> dict[str, Any]:
        if decision not in _DECISIONS:
            raise ValueError('review decision is invalid')
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM review_requests WHERE review_id=? AND session_id=?',
                             (review_id, session_id)).fetchone()
            if not row:
                raise KeyError(review_id)
            if row['state'] != 'OPEN':
                raise ValueError('review already resolved')
            run = db.execute('SELECT attempt_id,outcome,cancel_requested FROM run_journal WHERE run_id=?',
                             (row['run_id'],)).fetchone()
            if not run or run['attempt_id'] != row['attempt_id'] or run['outcome'] is not None \
                    or run['cancel_requested']:
                raise ValueError('review belongs to a stale or terminal attempt')
            request = json.loads(row['payload_json'])
            if decision not in request['allowedActions']:
                raise ValueError('review decision not allowed')
            if decision == 'modify' and edited_value is None:
                raise ValueError('review edited value required')
            if isinstance(edited_value, bool) or (isinstance(edited_value, (int, float))
                                                   and not math.isfinite(edited_value)):
                raise ValueError('review edited value invalid')
            resolved = {'reviewId': review_id, 'decision': decision, 'timestamp': _time(),
                        'runId': row['run_id'], 'attemptId': row['attempt_id'],
                        'affectedObjectId': request['affectedObjectId']}
            if edited_value is not None:
                resolved['editedValue'] = edited_value
            db.execute('UPDATE review_requests SET state=?,decision_json=? WHERE review_id=?',
                       ('RESOLVED', json.dumps(resolved, ensure_ascii=False), review_id))
            self._journal_event(db, session_id, 'review.resolved', resolved)
        return resolved
