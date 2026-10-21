"""Public-safe contract tests; no COMSOL process or frozen artifact is used."""
from __future__ import annotations

import hashlib
import asyncio
from pathlib import Path

from app.adapter.rc42_contract import (
    _live_core_candidate, current_completion, object_id, project_artifact, project_event, project_run,
    project_scientific_snapshot, safe_error, verified_verdict,
)


def fixture(tmp_path: Path):
    path = tmp_path / 'temperature.png'
    path.write_bytes(b'known-result')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    run = {'run_id': 'run-1', 'attempt_id': 'attempt-1', 'session_id': 'study-1',
           'outcome': 'Completed', 'phase': 'COMPLETED', 'cancel_requested': False,
           'artifacts': {'temperature_image': str(path)}}
    completion = {
        'run_id': 'run-1', 'attempt_id': 'attempt-1',
        'artifacts': {'temperature_image': str(path)},
        'execution_verdict': {
            'run_id': 'run-1', 'command_success': True, 'model_state_verified': True,
            'scientific_validation_success': True, 'evidence_contract_passed': True,
            'completed': True, 'issues': [],
        },
        'evidence_contract': {
            'passed': True, 'run_id': 'run-1', 'attempt_id': 'attempt-1',
            'artifacts': {'temperature_image': {
                'path': str(path), 'size_bytes': path.stat().st_size, 'sha256': digest,
            }},
        },
        'validation': {'temperature_min_K': 293.0, 'temperature_max_K': 373.0},
    }
    events = [{'sequence': 1, 'type': 'run.completed', 'payload': completion,
               'created_at': '2026-09-30T00:00:00Z'}]
    return path, run, completion, events


def test_false_verification_never_green(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    completion['execution_verdict']['model_state_verified'] = False
    result = project_artifact(run, completion, events, 'temperature_image')
    assert result['evidence']['backendVerified'] is False
    assert result['evidenceState'] != 'verified'


def test_human_approval_is_not_certification(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    completion['execution_verdict']['scientific_validation_success'] = False
    events.append({'sequence': 2, 'type': 'review.resolved', 'payload': {'approved': True}})
    assert project_artifact(run, completion, events, 'temperature_image')['evidenceState'] != 'verified'


def test_stale_artifact(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    events.append({'sequence': 2, 'type': 'evidence.invalidated',
                   'payload': {'object_id': 'obj-one'}, 'created_at': '2026-09-30T00:01:00Z'})
    assert project_artifact(run, completion, events, 'temperature_image')['evidenceState'] == 'stale'


def test_hash_mismatch_is_invalid(tmp_path):
    path, run, completion, events = fixture(tmp_path)
    path.write_bytes(b'changed-result')
    assert project_artifact(run, completion, events, 'temperature_image')['evidenceState'] == 'invalid'


def test_missing_unit_and_range_are_omitted(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    completion['validation'] = {}
    artifact = project_artifact(run, completion, events, 'temperature_image')
    assert 'unit' not in artifact['metadata']
    assert 'range' not in artifact['metadata']
    assert artifact['numbers'] == {}


def test_workflow_specific_result_filename_and_range(tmp_path):
    path, run, completion, events = fixture(tmp_path)
    battery_path = path.with_name('battery_electrolyte_concentration.png')
    battery_path.write_bytes(path.read_bytes())
    record = {'path': str(battery_path), 'size_bytes': battery_path.stat().st_size,
              'sha256': hashlib.sha256(battery_path.read_bytes()).hexdigest()}
    run['artifacts'] = {'concentration_image': str(battery_path)}
    completion['artifacts'] = dict(run['artifacts'])
    completion['evidence_contract']['artifacts'] = {'concentration_image': record}
    completion['execution_verdict']['workflow_id'] = 'lithium_ion_battery_baseline'
    completion['validation'] = {'concentration_min_mol_per_m3': 1.0,
                                'concentration_max_mol_per_m3': 2.0}
    artifact = project_artifact(run, completion, events, 'concentration_image')
    assert artifact['metadata']['unit'] == 'mol/m³'
    assert artifact['numbers'] == {'min': 1.0, 'max': 2.0}
    assert artifact['uri'].endswith('battery_electrolyte_concentration.png')


def test_wrong_run_and_attempt_are_rejected(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    wrong = {**completion, 'run_id': 'wrong-run'}
    assert not verified_verdict(run, wrong)
    assert project_artifact(run, wrong, events, 'temperature_image') is None
    wrong = {**completion, 'attempt_id': 'wrong-attempt'}
    assert project_artifact(run, wrong, events, 'temperature_image') is None
    wrong_events = [{'sequence': 1, 'type': 'run.completed', 'payload': wrong}]
    assert current_completion(run, wrong_events)[0] is None
    assert project_artifact(run, *current_completion(run, wrong_events)[:1], wrong_events, 'temperature_image') is None


def test_boundary_id_survives_correction():
    before = object_id('study-1', 'boundary', 'left')
    after = object_id('study-1', 'boundary', 'left')
    assert before == after
    assert before != object_id('study-1', 'boundary', 'right')


def test_pause_requested_is_not_paused():
    session = {'created_at': '2026-09-30T00:00:00Z', 'events': [
        {'type': 'execution.pause_requested', 'payload': {'run_id': 'run-1'}}]}
    run = {'run_id': 'run-1', 'attempt_id': 'attempt-1', 'phase': 'SOLVING'}
    state = project_run(session, run)
    assert state['phase'] == 'solving'
    assert state['pauseRequested'] is True
    assert state['safePointState'] == 'requested'


def test_resume_clears_prior_pause_state():
    session = {'created_at': '2026-09-30T00:00:00Z', 'events': [
        {'type': 'execution.pause_requested', 'payload': {'run_id': 'run-1'}},
        {'type': 'execution.safe_boundary', 'payload': {'run_id': 'run-1', 'state': 'PAUSED'}},
        {'type': 'execution.resumed', 'payload': {'run_id': 'run-1'}}]}
    run = {'run_id': 'run-1', 'attempt_id': 'attempt-1', 'phase': 'SOLVING'}
    state = project_run(session, run)
    assert state['phase'] == 'solving'
    assert state['safePointState'] == 'none'
    assert state['pauseRequested'] is False


def test_invalidated_event_precedes_object_change():
    invalid = project_event('study-1', {'sequence': 2, 'type': 'evidence.invalidated',
        'payload': {'run_id': 'run-1', 'evidence_id': 'evidence-1'}, 'created_at': 'now'})
    changed = project_event('study-1', {'sequence': 3, 'type': 'execution.correction_verified',
        'payload': {'run_id': 'run-1', 'object_id': 'obj-one'}, 'created_at': 'now'})
    assert invalid['name'] == 'evidence.invalidated'
    assert changed['name'] == 'model.object_changed'
    assert invalid['sequence'] < changed['sequence']


def test_safe_error_has_no_raw_detail():
    error = safe_error('solver_failed', location='E:\\private\\trace.log')
    assert error['location'] == 'Runtime'
    assert 'private' not in str(error)


def test_capability_manifest_does_not_turn_history_into_current_verification():
    from app.main import capabilities
    matrix = asyncio.run(capabilities())['capabilities']
    assert matrix
    assert all(item['runtime_verified'] is False for item in matrix.values())
    assert matrix['heat_transfer_2d_stationary']['status'] == 'HISTORICAL_ACCEPTANCE'


def heat_core_readback():
    return {
        'source': 'direct_comsol_readback', 'model_tag': 'heat_test',
        'geometry_tag': 'comp1:geom1', 'geometry_feature_tag': 'r1',
        'geometry_kind': 'Rectangle', 'geometry_size': [10.0, 5.0],
        'geometry_unit': 'mm', 'geometry_domains': 1, 'geometry_edges': 4,
        'material_tag': 'mat1', 'material_name': 'User-defined thermal material',
        'material_origin': 'user', 'material_properties': [
            {'symbol': 'k', 'value': 205.0, 'unit': 'W/(m*K)'}],
        'physics_tag': 'comp1:ht', 'physics_type': 'HeatTransfer',
        'study_tag': 'std1', 'study_type': 'stationary', 'solver_tag': 'sol1',
        'mesh_tag': 'comp1:mesh1',
        'left_bc': {'tag': 'temp_left', 'type': 'TemperatureBoundary',
                    'selection': [1], 'expression': '350[K]'},
        'right_bc': {'tag': 'temp_right', 'type': 'TemperatureBoundary',
                     'selection': [4], 'expression': '293[K]'},
    }


def test_core_snapshot_exists_with_unavailable_diagnostics(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    completion['build_readback'] = heat_core_readback()
    snapshot = project_scientific_snapshot('study-1', run, completion, events)
    assert snapshot['geometry']['sizeMm'] == {'x': 10.0, 'y': 5.0}
    assert snapshot['boundaries'][0]['value'] == {'value': 350.0, 'unit': 'K'}
    assert snapshot['mesh']['readback'] == 'matched'
    assert snapshot['solver']['study'] == 'std1'
    assert snapshot['diagnostics']['mesh']['quality'] == {'evidenceState': 'unavailable'}
    assert snapshot['diagnostics']['solver']['relativeTolerance'] == {'evidenceState': 'unavailable'}
    assert all('value' not in item for section in snapshot['diagnostics'].values()
               for item in section.values())


def test_stale_diagnostic_drops_old_value(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    completion['build_readback'] = heat_core_readback()
    completion['optional_diagnostics'] = {'mesh': {'quality': {
        'evidenceState': 'verified', 'value': 0.8, 'source': 'COMSOL mesh readback'}}}
    events.append({'sequence': 2, 'type': 'evidence.invalidated',
                   'payload': {'run_id': 'run-1', 'attempt_id': 'attempt-1'},
                   'created_at': '2026-09-30T00:01:00Z'})
    snapshot = project_scientific_snapshot('study-1', run, completion, events)
    assert snapshot['diagnostics']['mesh']['quality']['evidenceState'] == 'stale'
    assert 'value' not in snapshot['diagnostics']['mesh']['quality']


def test_all_diagnostics_verified_cannot_certify(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    completion['build_readback'] = heat_core_readback()
    completion['execution_verdict']['scientific_validation_success'] = False
    completion['optional_diagnostics'] = {'mesh': {'quality': {
        'evidenceState': 'verified', 'value': 0.8, 'source': 'COMSOL mesh readback'}}}
    snapshot = project_scientific_snapshot('study-1', run, completion, events)
    assert snapshot is not None
    assert verified_verdict(run, completion) is False
    assert project_artifact(run, completion, events, 'temperature_image')['evidenceState'] != 'verified'


def test_core_snapshot_rejects_plan_like_or_incomplete_readback(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    completion['build_readback'] = {**heat_core_readback(), 'source': 'plan'}
    assert project_scientific_snapshot('study-1', run, completion, events) is None
    completion['build_readback'] = heat_core_readback()
    del completion['build_readback']['geometry_domains']
    assert project_scientific_snapshot('study-1', run, completion, events) is None


def test_corrected_artifact_rejects_prior_attempt_even_with_valid_hash(tmp_path):
    _, run, completion, events = fixture(tmp_path)
    run['attempt_id'] = 'attempt-2'
    assert project_artifact(run, completion, events, 'temperature_image') is None
    completion['attempt_id'] = 'attempt-2'
    completion['evidence_contract']['attempt_id'] = 'attempt-1'
    assert project_artifact(run, completion, events, 'temperature_image') is None


def test_pending_result_event_does_not_claim_scientific_verification():
    event = {'sequence': 9, 'type': 'result.available', 'created_at': 'now', 'payload': {
        'run_id': 'run-1', 'attempt_id': 'attempt-2',
        'artifact_id': 'artifact-run-1-attempt-2-temperature_image',
        'sha256': 'a' * 64, 'minimum': 293.0, 'maximum': 350.0,
        'evidence_state': 'pending'}}
    mapped = project_event('study-1', event, {'run_id': 'run-1', 'attempt_id': 'attempt-2'})
    assert mapped['name'] == 'result.available'
    assert mapped['payload']['artifact']['evidence']['backendVerified'] is False
    assert mapped['payload']['artifact']['evidenceState'] == 'pending'
    event['payload']['attempt_id'] = 'attempt-1'
    assert project_event('study-1', event, {'run_id': 'run-1', 'attempt_id': 'attempt-2'}) is None


def test_live_core_can_survive_boundary_mutation_without_old_value():
    readback = heat_core_readback()
    readback['left_bc']['expression'] = None
    readback['boundary_pending'] = ['left']
    run = {'run_id': 'run-1', 'attempt_id': 'attempt-1', 'session_id': 'study-1',
           'outcome': None, 'phase': 'PAUSED', 'cancel_requested': False, 'artifacts': {}}
    completion = {'run_id': 'run-1', 'attempt_id': 'attempt-1',
                  'build_readback': readback, '_snapshot_sequence': 12,
                  '_snapshot_updated_at': '2026-10-05T00:00:00Z'}
    snapshot = project_scientific_snapshot('study-1', run, completion, [])
    assert snapshot is not None
    assert snapshot['revision'] == 12
    assert snapshot['boundaries'][0]['underReview'] is True
    assert 'value' not in snapshot['boundaries'][0]
    assert snapshot['boundaries'][1]['value']['value'] == 293.0
    assert snapshot['results'] == []


def test_live_core_uses_post_mutation_readback_not_old_boundary():
    old_core = heat_core_readback()
    old_core['left_bc']['expression'] = '373[K]'
    run = {'run_id': 'run-1', 'attempt_id': 'attempt-1', 'session_id': 'study-1',
           'outcome': None, 'phase': 'PAUSED', 'cancel_requested': False, 'artifacts': {}}
    events = [
        {'sequence': 1, 'type': 'model.core_readback', 'created_at': 't1',
         'payload': {'run_id': 'run-1', 'attempt_id': 'attempt-1',
                     'verified_real_comsol': True, 'readback': old_core}},
        {'sequence': 2, 'type': 'evidence.invalidated', 'created_at': 't2',
         'payload': {'run_id': 'run-1', 'attempt_id': 'attempt-1',
                     'object_id': object_id('study-1', 'boundary', 'left')}},
    ]
    pending = project_scientific_snapshot(
        'study-1', run, _live_core_candidate(run, events, 'study-1'), events)
    assert pending['boundaries'][0]['underReview'] is True
    assert 'value' not in pending['boundaries'][0]
    events.append({'sequence': 3, 'type': 'model.readback_updated', 'created_at': 't3',
                   'payload': {'run_id': 'run-1', 'attempt_id': 'attempt-1',
                               'object_id': object_id('study-1', 'boundary', 'left'),
                               'verified_real_comsol': True,
                               'readback': {'feature_tag': 'temp_left',
                                            'feature_type': 'TemperatureBoundary',
                                            'selection': [1], 'expression': '350[K]',
                                            'model_state_verified': True}}})
    refreshed = project_scientific_snapshot(
        'study-1', run, _live_core_candidate(run, events, 'study-1'), events)
    assert refreshed['boundaries'][0]['value'] == {'value': 350.0, 'unit': 'K'}
    assert refreshed['revision'] == 3
