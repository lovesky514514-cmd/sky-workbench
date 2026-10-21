"""Controlled boundary contract tests. These are not real COMSOL evidence."""
from __future__ import annotations

import asyncio

import pytest

from app.adapter.rc42_contract import object_id
from app.execution.graph import ExecutionGraph
from app.execution.live_interrupt import LiveInterruptAdapter
from app.session.store import SessionStore


class BoundaryBridge:
    def __init__(self):
        self.expression = '293[K]'
        self.selection = [4]
        self.calls = []

    async def health(self):
        return {'real_comsol': True, 'model_connected': True, 'model_tag': 'heat_probe'}

    async def boundary_feature(self, **identity):
        self.calls.append(('read', identity))
        return {'command_success': True, 'model_state_verified': True,
                'model_tag': 'heat_probe', 'component_tag': 'comp1',
                'physics_tag': 'ht', 'feature_tag': 'temp_left',
                'feature_type': 'TemperatureBoundary', 'property_key': 'T0',
                'selection': list(self.selection), 'expression': self.expression,
                'unit': 'K'}

    async def set_boundary_feature(self, **mutation):
        self.calls.append(('mutate', mutation))
        assert mutation['expected_expression'] == self.expression
        assert mutation['expected_selection'] == self.selection[0]
        self.expression = mutation['proposed_expression']
        return {'command_success': True, 'model_state_verified': True,
                'expression': self.expression, 'selection': list(self.selection),
                'feature_type': 'TemperatureBoundary', 'property_key': 'T0'}


def setup(tmp_path):
    store = SessionStore(tmp_path / 'boundary.sqlite')
    store.initialize()
    sid = store.create('heat')['id']
    store.begin_run(sid, 'heat-run', {'workflow_id': 'heat_transfer_2d_stationary'})
    aid = store.begin_attempt('heat-run')
    bridge = BoundaryBridge()
    adapter = LiveInterruptAdapter(store, bridge)
    graph = ExecutionGraph.small_supported('heat-run', aid, 'heat_transfer_2d_stationary')
    adapter.register(sid, graph)
    adapter.register_heat_boundaries('heat-run', {'left': 4, 'right': 2})
    adapter.start('heat-run', 'build')
    return store, sid, aid, bridge, adapter


def test_boundary_review_requires_real_feature_readback_and_stable_id(tmp_path):
    store, sid, aid, bridge, adapter = setup(tmp_path)
    stable_id = object_id(sid, 'boundary', 'left')
    review = asyncio.run(adapter.request_boundary_review('heat-run', stable_id, 350, 'K', 'Correct left boundary'))
    assert review['affectedObjectId'] == stable_id
    assert review['currentValue'] == '293[K]'
    assert review['boundaryFeature']['featureId'] == 'temp_left'
    assert review['boundaryFeature']['selectionIdentity'] == 'comp1:geom1:boundary:4'
    assert bridge.calls[0][0] == 'read'
    assert store.open_review(sid)['reviewId'] == review['reviewId']


def test_boundary_mutation_order_and_no_science_recertification(tmp_path):
    store, sid, aid, bridge, adapter = setup(tmp_path)
    stable_id = object_id(sid, 'boundary', 'left')
    review = asyncio.run(adapter.request_boundary_review('heat-run', stable_id, 350, 'K', 'Correct left boundary'))
    adapter.request_pause('heat-run')
    adapter.safe_boundary('heat-run', 'build')
    result = asyncio.run(adapter.apply_boundary_correction('heat-run', review['reviewId']))
    assert result['boundaryObjectId'] == stable_id
    assert result['readback']['expression'] == '350[K]'
    assert result['scientific_validation_passed'] is False
    assert [call[0] for call in bridge.calls] == ['read', 'read', 'mutate', 'read']
    types = [event['type'] for event in store.get(sid)['events']]
    order = ['review.required', 'execution.pause_requested', 'execution.safe_boundary',
             'evidence.invalidated', 'model.object_changed', 'model.readback_updated', 'review.resolved']
    assert [types.index(item) for item in order] == sorted(types.index(item) for item in order)
    assert 'evidence.verified' not in types
    assert store.get_review(review['reviewId'])['state'] == 'RESOLVED'


@pytest.mark.parametrize('change', ['selection', 'expression', 'feature_type', 'unit'])
def test_boundary_drift_rejects_mutation(tmp_path, change):
    store, sid, aid, bridge, adapter = setup(tmp_path)
    review = asyncio.run(adapter.request_boundary_review(
        'heat-run', object_id(sid, 'boundary', 'left'), 350, 'K', 'Correct left boundary'))
    adapter.request_pause('heat-run')
    adapter.safe_boundary('heat-run', 'build')
    if change == 'selection': bridge.selection = [3]
    elif change == 'expression': bridge.expression = '299[K]'
    elif change == 'feature_type':
        original = bridge.boundary_feature
        async def altered(**kwargs):
            return {**await original(**kwargs), 'feature_type': 'FluxBoundary'}
        bridge.boundary_feature = altered
    else:
        original = bridge.boundary_feature
        async def altered(**kwargs):
            return {**await original(**kwargs), 'unit': 'degC'}
        bridge.boundary_feature = altered
    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(adapter.apply_boundary_correction('heat-run', review['reviewId']))
    assert not any(call[0] == 'mutate' for call in bridge.calls)


def test_stale_attempt_rejects_boundary_mutation(tmp_path):
    store, sid, aid, bridge, adapter = setup(tmp_path)
    review = asyncio.run(adapter.request_boundary_review(
        'heat-run', object_id(sid, 'boundary', 'left'), 350, 'K', 'Correct left boundary'))
    adapter.request_pause('heat-run')
    adapter.safe_boundary('heat-run', 'build')
    store.end_attempt('heat-run', aid, 'RetryableFailure')
    store.begin_attempt('heat-run')
    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(adapter.apply_boundary_correction('heat-run', review['reviewId']))
    assert not any(call[0] == 'mutate' for call in bridge.calls)


def test_review_cannot_be_created_after_pause_request(tmp_path):
    store, sid, aid, bridge, adapter = setup(tmp_path)
    adapter.request_pause('heat-run')
    with pytest.raises(RuntimeError, match='precede pause'):
        asyncio.run(adapter.request_boundary_review(
            'heat-run', object_id(sid, 'boundary', 'left'), 350, 'K', 'Correct left boundary'))
    assert store.open_review(sid) is None
    assert bridge.calls == []
