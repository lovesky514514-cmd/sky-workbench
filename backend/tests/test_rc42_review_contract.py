"""Durable, fail-closed review records; no human decision is a science gate."""
from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from app.session.store import SessionStore
from app.adapter.rc42_contract import project_event, project_session
from app.adapter.rc42_contract import object_id
from app.execution.graph import ExecutionGraph, TypedCorrection
from app.execution.live_interrupt import LiveInterruptAdapter


def active_store(tmp_path: Path):
    store = SessionStore(tmp_path / 'reviews.sqlite')
    store.initialize()
    session_id = store.create('project-review')['id']
    store.begin_run(session_id, 'run-review', {'workflow_id': 'heat_transfer_2d_stationary'})
    attempt_id = store.begin_attempt('run-review')
    return store, session_id, attempt_id


def request(attempt_id: str):
    return {
        'reviewId': 'review-one', 'runId': 'run-review', 'attemptId': attempt_id,
        'affectedObjectId': 'boundary:stable-left',
        'currentValue': 293.0, 'proposedValue': 350.0, 'unit': 'K',
        'reason': 'User requests an explicit fixed-temperature boundary correction.',
        'downstreamImpact': 'Build, solve and scientific evidence require revalidation.',
        'allowedActions': ['approve', 'modify', 'reject', 'defer'],
    }


def test_review_persists_and_required_event_follows_record(tmp_path):
    store, sid, attempt = active_store(tmp_path)
    created = store.create_review(sid, request(attempt))
    reopened = SessionStore(store.path)
    assert reopened.get_review('review-one') == created
    assert created['state'] == 'OPEN'
    events = reopened.get(sid)['events']
    required = next(e for e in events if e['type'] == 'review.required')
    assert required['payload']['reviewId'] == created['reviewId']
    assert reopened.get_review(required['payload']['reviewId']) is not None
    assert project_event(sid, required, reopened.latest_run(sid))['name'] == 'review.required'
    assert project_session(reopened, sid)['reviewRequest']['objectId'] == 'boundary:stable-left'


def test_stale_attempt_cannot_create_review(tmp_path):
    store, sid, attempt = active_store(tmp_path)
    with pytest.raises(ValueError, match='attempt'):
        store.create_review(sid, request('stale-attempt'))
    assert not any(e['type'] == 'review.required' for e in store.get(sid)['events'])


def test_human_decision_is_durable_but_not_verification(tmp_path):
    store, sid, attempt = active_store(tmp_path)
    store.create_review(sid, request(attempt))
    decision = store.resolve_review(sid, 'review-one', 'approve')
    assert decision['reviewId'] == 'review-one'
    assert decision['decision'] == 'approve'
    assert 'scientific_validation_passed' not in decision
    assert 'model_state_verified' not in decision
    assert store.get_review('review-one')['state'] == 'RESOLVED'
    events = store.get(sid)['events']
    assert [e['type'] for e in events if e['type'].startswith('review.')] == [
        'review.required', 'review.resolved']
    assert events[-1]['payload']['reviewId'] == 'review-one'
    assert project_event(sid, events[-1], store.latest_run(sid))['name'] == 'review.resolved'
    assert project_session(store, sid)['reviewRequest'] is None


def test_review_rejects_missing_unit_and_duplicate_resolution(tmp_path):
    store, sid, attempt = active_store(tmp_path)
    invalid = request(attempt)
    invalid['unit'] = ''
    with pytest.raises(ValueError, match='unit'):
        store.create_review(sid, invalid)
    store.create_review(sid, request(attempt))
    store.resolve_review(sid, 'review-one', 'reject')
    with pytest.raises(ValueError, match='resolved'):
        store.resolve_review(sid, 'review-one', 'approve')


def test_model_readback_event_requires_real_verified_source():
    base = {'sequence': 7, 'type': 'model.readback_updated',
            'payload': {'run_id': 'run-review', 'attempt_id': 'attempt-review',
                        'object_id': 'boundary:stable-left', 'verified_real_comsol': False}}
    assert project_event('study', base) is None
    base['payload']['verified_real_comsol'] = True
    event = project_event('study', base)
    assert event['name'] == 'model.readback_updated'
    assert event['payload']['evidence']['backendVerified'] is False


def test_controlled_bridge_review_correction_order_and_identity(tmp_path):
    """Controlled fake Bridge tests ordering only; it is NOT real COMSOL proof."""
    store, sid, attempt = active_store(tmp_path)
    graph = ExecutionGraph.small_supported('run-review', attempt, 'tds_2d_stationary_diffusion')
    class ControlledBridge:
        value = '293[mol/m^3]'
        async def health(self):
            return {'real_comsol': True, 'model_connected': True, 'model_tag': 'controlled'}
        async def parameters(self):
            return {'model_state_verified': True, 'parameters': {'c_left': self.value}}
        async def set_parameter(self, name, value):
            assert name == 'c_left'
            self.value = value
            return {'command_success': True, 'model_state_verified': True, 'readback': value}
    adapter = LiveInterruptAdapter(store, ControlledBridge())
    adapter.register(sid, graph)
    adapter.start('run-review', 'build')
    adapter.request_pause('run-review')
    adapter.safe_boundary('run-review', 'build')
    review = request(attempt)
    review.update(affectedObjectId=object_id(sid, 'parameter', 'left_concentration'),
                  currentValue='293[mol/m^3]', proposedValue=350.0, unit='mol/m^3')
    store.create_review(sid, review)
    asyncio.run(adapter.apply_parameter_correction('run-review', TypedCorrection(
        'parameters.left_concentration', 350.0, 'mol/m^3', 'reviewed change'), review_id='review-one'))
    types = [e['type'] for e in store.get(sid)['events']]
    ordered = ['review.required', 'evidence.invalidated', 'execution.correction_verified',
               'model.readback_updated', 'review.resolved']
    assert [types.index(name) for name in ordered] == sorted(types.index(name) for name in ordered)
    events = store.get(sid)['events']
    changed = next(e for e in events if e['type'] == 'execution.correction_verified')
    readback = next(e for e in events if e['type'] == 'model.readback_updated')
    assert changed['payload']['object_id'] == review['affectedObjectId']
    assert readback['payload']['object_id'] == review['affectedObjectId']
    assert store.get_review('review-one')['state'] == 'RESOLVED'
