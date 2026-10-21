"""Fail-closed RC4.2 provider for the frozen Greenfield WorkbenchAdapter.

No projection may turn a file, a human approval or a worker claim into a
scientific certification. The current run journal and verdict remain authority.
"""
from __future__ import annotations

import hashlib
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.session.execution_eta import estimate_remaining_time
from app.validators.execution_verdict import verify_artifact_integrity


ARTIFACTS = {
    'temperature_image': ('temperature.png', 'Temperature', 'K', 'temperature_min_K', 'temperature_max_K'),
    'concentration_image': ('concentration.png', 'Concentration', 'mol/m³', 'concentration_min_mol_per_m3', 'concentration_max_mol_per_m3'),
    'stress_image': ('solid_mises.png', 'von Mises stress', 'Pa', 'von_mises_min_Pa', 'von_mises_max_Pa'),
    'potential_image': ('electrolyte_potential.png', 'Electric potential', 'V', 'potential_min_V', 'potential_max_V'),
}
WORKFLOW_ARTIFACTS = {
    ('lithium_ion_battery_baseline', 'concentration_image'):
        ('battery_electrolyte_concentration.png', 'Electrolyte concentration', 'mol/m³',
         'concentration_min_mol_per_m3', 'concentration_max_mol_per_m3'),
    ('heat_tds_temperature_coupling', 'concentration_image'):
        ('coupled_concentration.png', 'Concentration', 'mol/m³',
         'concentration_min_mol_per_m3', 'concentration_max_mol_per_m3'),
    ('heat_tds_temperature_coupling', 'temperature_image'):
        ('coupled_temperature.png', 'Temperature', 'K',
         'temperature_min_K', 'temperature_max_K'),
}
PHASES = {'PRECHECK': 'building', 'BUILDING': 'building', 'BUILD_VALIDATION': 'building',
          'SOLVING': 'solving', 'SOLUTION_VALIDATION': 'validating', 'VALIDATING': 'validating',
          'EXPORTING': 'validating', 'REPORTING': 'validating', 'PAUSED': 'paused',
          'COMPLETED': 'settled', 'FAILED': 'failed', 'CANCELED': 'failed',
          'INTERRUPTED': 'failed', 'VALIDATION_FAILED': 'failed'}
_KELVIN_LITERAL = re.compile(r'^\s*(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\[K\]\s*$')


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _safe_diagnostic(raw: Any, stale: bool) -> dict[str, Any]:
    """Never expose a superseded or unsupported diagnostic value."""
    if stale:
        return {'evidenceState': 'stale'}
    if not isinstance(raw, dict):
        return {'evidenceState': 'unavailable'}
    state = raw.get('evidenceState')
    if state not in {'unavailable', 'pending', 'stale', 'invalid', 'verified'}:
        return {'evidenceState': 'invalid'}
    result = {'evidenceState': state}
    if isinstance(raw.get('detail'), str) and raw['detail']:
        result['detail'] = raw['detail']
    if state == 'verified':
        value = raw.get('value')
        source = raw.get('source')
        if (isinstance(value, (int, float, str)) and not isinstance(value, bool)
                and (not isinstance(value, float) or math.isfinite(value))
                and value != '' and isinstance(source, str) and source):
            result.update(value=value, source=source)
        else:
            result = {'evidenceState': 'invalid'}
    return result


def project_scientific_snapshot(session_id: str, run: dict[str, Any] | None,
                                completion: dict[str, Any] | None,
                                events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Project proven Heat core independently of optional diagnostic coverage.

    The snapshot describes model existence, not certification. Trust remains with
    the current execution verdict, evidence contract and artifact integrity.
    """
    if not run or not completion or completion.get('run_id') != run.get('run_id') \
            or completion.get('attempt_id') != run.get('attempt_id'):
        return None
    readback = completion.get('build_readback')
    if not isinstance(readback, dict) or readback.get('source') != 'direct_comsol_readback':
        return None
    required = ('model_tag', 'geometry_tag', 'geometry_feature_tag', 'material_tag',
                'material_name', 'material_origin', 'physics_tag', 'physics_type',
                'study_tag', 'study_type', 'solver_tag', 'mesh_tag')
    if any(not isinstance(readback.get(key), str) or not readback[key] for key in required):
        return None
    if readback['study_type'] not in {'stationary', 'transient', 'eigenfrequency'} \
            or readback['material_origin'] not in {'library', 'user'} \
            or readback.get('geometry_kind') != 'Rectangle' \
            or readback.get('geometry_unit') not in {'m', 'cm', 'mm'} \
            or not _positive_int(readback.get('geometry_domains')) \
            or not _positive_int(readback.get('geometry_edges')):
        return None
    size = readback.get('geometry_size')
    if not isinstance(size, list) or len(size) != 2 or not all(_finite_positive(item) for item in size):
        return None
    properties = readback.get('material_properties')
    if not isinstance(properties, list):
        return None
    projected_properties = []
    for item in properties:
        if not isinstance(item, dict) or not isinstance(item.get('symbol'), str) \
                or not _finite_positive(item.get('value')) or not isinstance(item.get('unit'), str) \
                or not item['unit']:
            return None
        projected_properties.append({'symbol': item['symbol'],
                                     'quantity': {'value': item['value'], 'unit': item['unit']}})
    if not projected_properties:
        return None
    boundaries = []
    for side in ('left', 'right'):
        feature = readback.get(f'{side}_bc')
        if not isinstance(feature, dict) or feature.get('type') != 'TemperatureBoundary' \
                or not isinstance(feature.get('tag'), str) or not feature['tag'] \
                or not isinstance(feature.get('selection'), list) \
                or len(feature['selection']) != 1 or not _positive_int(feature['selection'][0]):
            return None
        boundary = {'id': object_id(session_id, 'boundary', side),
                    'label': f'Boundary {feature["selection"][0]}',
                    'location': f'{side} edge', 'kind': 'fixed_temperature'}
        if feature.get('expression') is None and side in readback.get('boundary_pending', []):
            boundary['underReview'] = True
        else:
            match = _KELVIN_LITERAL.fullmatch(str(feature.get('expression', '')))
            if match is None:
                return None
            value = float(match.group(1))
            if not _finite_positive(value):
                return None
            boundary['value'] = {'value': value, 'unit': 'K'}
        boundaries.append(boundary)
    _, completed_sequence = current_completion(run, events)
    stale = any(int(item.get('sequence', 0)) > completed_sequence
                and item.get('type') in {'evidence.invalidated', 'validation.invalidated_by_human_correction'}
                for item in events) if completed_sequence >= 0 else False
    raw_diagnostics = completion.get('optional_diagnostics') or {}
    diagnostics = {
        'mesh': {key: _safe_diagnostic((raw_diagnostics.get('mesh') or {}).get(key), stale)
                 for key in ('quality', 'failedElements', 'sizing')},
        'solver': {key: _safe_diagnostic((raw_diagnostics.get('solver') or {}).get(key), stale)
                   for key in ('relativeTolerance', 'iterations', 'residual')},
    }
    factor = {'m': 1000.0, 'cm': 10.0, 'mm': 1.0}[readback['geometry_unit']]
    geometry = {'kind': 'rectangle', 'sizeMm': {'x': size[0] * factor, 'y': size[1] * factor},
                'label': 'Rectangle', 'domainCount': readback['geometry_domains'],
                'boundaryCount': readback['geometry_edges'],
                'features': [{'id': object_id(session_id, 'geometry', readback['geometry_feature_tag']),
                              'label': 'Rectangle', 'settled': True}]}
    mesh = {'id': object_id(session_id, 'mesh', 'primary'),
            'tag': readback['mesh_tag'], 'readback': 'matched'}
    validation = completion.get('validation') or {}
    if verified_verdict(run, completion) and _positive_int(validation.get('node_count')):
        mesh['nodes'] = validation['node_count']
    if verified_verdict(run, completion) and _positive_int(validation.get('element_count')):
        mesh['elements'] = validation['element_count']
    solver = {'id': object_id(session_id, 'solver', 'primary'),
              'study': readback['study_tag'], 'solver': readback['solver_tag'],
              'tag': readback['solver_tag']}
    if verified_verdict(run, completion) and _positive_int(validation.get('dof')):
        solver['degreesOfFreedom'] = validation['dof']
        solver['converged'] = True
    finished = next((item for item in events if int(item.get('sequence', 0)) == completed_sequence), None)
    if solver.get('converged') and finished and finished.get('created_at'):
        solver['solvedAt'] = finished['created_at']
    artifacts = [item for key in ARTIFACTS
                 if (item := project_artifact(run, completion, events, key))]
    return {
        'id': f'model-{session_id}', 'name': readback['model_tag'],
        'studyType': readback['study_type'],
        'revision': max(completed_sequence, int(completion.get('_snapshot_sequence') or 0)),
        'updatedAt': finished.get('created_at', '') if finished else completion.get('_snapshot_updated_at', ''),
        'geometry': geometry,
        'material': {'id': object_id(session_id, 'material', 'primary'),
                     'name': readback['material_name'], 'origin': readback['material_origin'],
                     'properties': projected_properties},
        'physics': {'id': object_id(session_id, 'physics',
                                  (completion.get('execution_verdict') or {}).get('workflow_id') or 'primary'),
                    'interface': 'Heat Transfer in Solids',
                    'detail': readback['physics_type'], 'studyType': readback['study_type'],
                    'couplings': []},
        'boundaries': boundaries, 'mesh': mesh, 'solver': solver,
        'diagnostics': diagnostics, 'results': artifacts,
    }


def _live_core_candidate(run: dict[str, Any] | None,
                         events: list[dict[str, Any]], session_id: str) -> dict[str, Any] | None:
    """Current-attempt direct COMSOL core, without inventing scientific success."""
    if not run or run.get('outcome') is not None or not run.get('attempt_id'):
        return None
    matching = lambda item: (item.get('payload') or {}).get('run_id') == run['run_id'] \
        and (item.get('payload') or {}).get('attempt_id') == run['attempt_id']
    core_event = next((item for item in reversed(events)
                       if item.get('type') == 'model.core_readback' and matching(item)
                       and (item.get('payload') or {}).get('verified_real_comsol') is True), None)
    if core_event is None:
        return None
    readback = deepcopy((core_event.get('payload') or {}).get('readback'))
    if not isinstance(readback, dict):
        return None
    updated = core_event
    for item in events:
        if int(item.get('sequence', 0)) <= int(core_event['sequence']) or not matching(item):
            continue
        payload = item.get('payload') or {}
        if item.get('type') == 'evidence.invalidated':
            for side in ('left', 'right'):
                if payload.get('object_id') == object_id(session_id, 'boundary', side):
                    readback[f'{side}_bc']['expression'] = None
                    readback.setdefault('boundary_pending', []).append(side)
                    updated = item
        elif item.get('type') == 'model.readback_updated' and payload.get('verified_real_comsol') is True:
            feature = payload.get('readback')
            for side in ('left', 'right'):
                prior = readback.get(f'{side}_bc') or {}
                if (payload.get('object_id') == object_id(session_id, 'boundary', side)
                        and isinstance(feature, dict) and feature.get('feature_tag') == prior.get('tag')
                        and feature.get('feature_type') == 'TemperatureBoundary'
                        and feature.get('selection') == prior.get('selection')
                        and feature.get('model_state_verified') is True):
                    prior['expression'] = feature.get('expression')
                    if side in readback.get('boundary_pending', []):
                        readback['boundary_pending'].remove(side)
                    updated = item
    return {'run_id': run['run_id'], 'attempt_id': run['attempt_id'],
            'build_readback': readback, '_snapshot_sequence': int(updated['sequence']),
            '_snapshot_updated_at': updated.get('created_at', '')}


def object_id(session_id: str, kind: str, key: str) -> str:
    """Stable across plan, build, readback and correction, independent of labels."""
    return 'obj-' + hashlib.sha256(f'{session_id}\0{kind}\0{key}'.encode()).hexdigest()[:20]


def current_completion(run: dict[str, Any] | None, events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, int]:
    if not run or run.get('outcome') != 'Completed' or run.get('cancel_requested'):
        return None, -1
    for event in reversed(events):
        if event.get('type') != 'run.completed':
            continue
        data = event.get('payload') or {}
        if data.get('run_id') != run.get('run_id'):
            continue
        if data.get('attempt_id') != run.get('attempt_id'):
            return None, -1
        return data, int(event['sequence'])
    return None, -1


def verified_verdict(run: dict[str, Any], completion: dict[str, Any] | None) -> bool:
    if not completion:
        return False
    verdict = completion.get('execution_verdict') or {}
    evidence = completion.get('evidence_contract') or {}
    return bool(completion.get('run_id') == run.get('run_id')
                and completion.get('attempt_id') == run.get('attempt_id')
                and verdict.get('run_id') == run.get('run_id')
                and all(verdict.get(key) is True for key in (
                    'command_success', 'model_state_verified', 'scientific_validation_success',
                    'evidence_contract_passed', 'completed'))
                and not verdict.get('issues') and evidence.get('passed') is True
                and evidence.get('run_id') == run.get('run_id')
                and evidence.get('attempt_id') == run.get('attempt_id'))


def project_artifact(run: dict[str, Any], completion: dict[str, Any] | None,
                     events: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Reject any artifact not bound to the final run/attempt and frozen hash."""
    if key not in ARTIFACTS or not completion:
        return None
    if completion.get('run_id') != run.get('run_id') or completion.get('attempt_id') != run.get('attempt_id'):
        return None
    contract = completion.get('evidence_contract') or {}
    if contract.get('run_id') != run.get('run_id') or contract.get('attempt_id') != run.get('attempt_id'):
        return None
    workflow = (completion.get('execution_verdict') or {}).get('workflow_id')
    filename, variable, unit, minimum_key, maximum_key = WORKFLOW_ARTIFACTS.get(
        (workflow, key), ARTIFACTS[key])
    path_text = (run.get('artifacts') or {}).get(key)
    record = ((completion.get('evidence_contract') or {}).get('artifacts') or {}).get(key)
    if not isinstance(path_text, str) or not isinstance(record, dict):
        return None
    if Path(path_text).name != filename or record.get('path') != path_text:
        return None
    if (completion.get('artifacts') or {}).get(key) != path_text:
        return None
    digest = record.get('sha256')
    if not isinstance(digest, str) or len(digest) != 64:
        return None
    integrity = verify_artifact_integrity(Path(path_text), record)
    _, completed_at = current_completion(run, events)
    stale_event = next((event for event in events if int(event['sequence']) > completed_at
                        and event.get('type') in {'evidence.invalidated', 'validation.invalidated_by_human_correction'}), None)
    verified = verified_verdict(run, completion) and integrity['passed'] and stale_event is None
    state = 'stale' if stale_event else 'invalid' if not integrity['passed'] else 'verified' if verified else 'pending'
    artifact_id = f"artifact-{run['run_id']}-{run['attempt_id']}-{key}"
    evidence: dict[str, Any] = {
        'id': f'evidence-{artifact_id}', 'artifact': artifact_id,
        'runId': run['run_id'], 'attemptId': run['attempt_id'], 'semanticState': state,
        'state': {'stale': 'invalidated', 'invalid': 'partial', 'verified': 'verified', 'pending': 'claimed'}[state],
        'backendVerified': verified, 'readbackHash': digest, 'claims': [],
        'reason': {'stale': 'A model input changed after certification.',
                   'invalid': 'Artifact bytes do not match the frozen manifest.',
                   'verified': 'Current run, attempt, verdict and artifact integrity match.',
                   'pending': 'Complete authoritative verification is unavailable.'}[state],
    }
    if stale_event:
        payload = stale_event.get('payload') or {}
        evidence['invalidatedBy'] = {
            'objectId': payload.get('object_id', ''), 'objectLabel': payload.get('object_label', 'Model input'),
            'changedAt': stale_event.get('created_at', ''), 'change': 'Human correction'}
    metadata: dict[str, Any] = {'plotKind': 'surface'}
    validation = completion.get('validation') or {}
    if isinstance(validation, dict) and unit and minimum_key and maximum_key:
        low, high = validation.get(minimum_key), validation.get(maximum_key)
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (low, high)) and low <= high:
            metadata.update(unit=unit, range={'min': low, 'max': high})
    return {
        'id': artifact_id, 'artifactId': artifact_id, 'modelId': f"model-{run['session_id']}",
        'runId': run['run_id'], 'attemptId': run['attempt_id'], 'quantity': variable,
        'variable': variable, 'title': variable, 'metadata': metadata,
        'numbers': ({'min': metadata['range']['min'], 'max': metadata['range']['max']}
                    if 'range' in metadata else {}),
        'sha256': digest, 'uri': f"/api/artifacts/{run['run_id']}/{filename}",
        'provenance': 'COMSOL execution verdict and frozen artifact manifest',
        'createdAt': next((event['created_at'] for event in events if int(event['sequence']) == completed_at), ''),
        'evidenceState': state, 'evidence': evidence,
    }


def project_run(session: dict[str, Any], run: dict[str, Any] | None,
                eta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not run:
        return None
    raw_phase = str(run.get('phase') or session.get('state') or '')
    phase = PHASES.get(raw_phase, 'failed')
    events = session.get('events') or []
    pause_state = 'none'
    for e in events:
        if (e.get('payload') or {}).get('run_id') != run['run_id']:
            continue
        if e['type'] == 'execution.pause_requested':
            pause_state = 'requested'
        elif e['type'] == 'execution.safe_boundary' and (e.get('payload') or {}).get('state') == 'PAUSED':
            pause_state = 'paused'
        elif e['type'] == 'execution.resumed':
            pause_state = 'none'
    if phase in ('settled', 'failed'):
        pause_state = 'none'
    pause_requested, paused = pause_state == 'requested', pause_state == 'paused'
    if paused:
        phase = 'paused'
    headline = ('Pause requested; awaiting safe point' if pause_requested and not paused
                and phase not in ('settled', 'failed') else
                {'building': 'Building model', 'solving': 'Solving', 'validating': 'Validating evidence',
                 'paused': 'Paused at safe point', 'settled': 'Run finished', 'failed': 'Run stopped'}.get(phase, 'Unknown'))
    active = 'solve' if phase == 'solving' else 'verify' if phase == 'validating' else 'geometry'
    progress = {
        'runId': run['run_id'], 'attemptId': run.get('attempt_id'), 'phase': phase,
        'headline': headline, 'current': headline,
        'steps': [{'id': step, 'label': step.title(), 'status': 'now' if step == active and phase not in ('settled', 'failed') else 'wait'}
                  for step in ('geometry', 'material', 'physics', 'mesh', 'solve', 'verify')],
        'startedAt': run.get('updated_at') or session.get('created_at'),
        'pauseRequested': pause_requested,
        'safePointState': pause_state,
        # The persistent journal does not prove that an in-process interrupt
        # graph/COMSOL safe point is currently attached. Do not offer Pause.
        'pauseCapability': 'unavailable',
    }
    if eta and eta.get('status') == 'estimated' and isinstance(eta.get('estimated_remaining_seconds'), int):
        progress['estimatedSecondsRemaining'] = eta['estimated_remaining_seconds']
    return progress


def safe_error(code: str, *, location: str | None = None,
               diagnostic_id: str | None = None) -> dict[str, Any]:
    """Do not pass exception strings, paths, stacks or raw JSON to the normal UI."""
    known = {
        'artifact_corrupt': ('Artifact integrity lost', 'The result no longer matches its frozen record.', 'Open diagnostics'),
        'solver_failed': ('Solve did not complete', 'No scientific result was certified.', 'Review model'),
        'validation_failed': ('Validation did not pass', 'The scientific checks did not pass.', 'Review evidence'),
        'evidence_contract_failed': ('Evidence is incomplete', 'The run cannot be marked verified.', 'Review evidence'),
        'adapter_unavailable': ('Runtime unavailable', 'The scientific runtime could not be reached.', 'Retry'),
        'not_implemented': ('Action unavailable', 'This operation is not supported in this build.', 'Close'),
    }
    code = code if code in known else 'unknown'
    title, summary, action = known.get(code, ('Operation did not complete', 'No result was certified.', 'Retry'))
    result = {'code': code, 'title': title, 'summary': summary,
              'actions': [{'id': action.lower().replace(' ', '_'), 'label': action, 'emphasis': 'primary'}]}
    if location:
        result['location'] = location if location in {'Build', 'Solve', 'Validation', 'Artifact', 'Runtime'} else 'Runtime'
    if diagnostic_id:
        result['diagnosticId'] = diagnostic_id
    return result


def project_review(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Typed durable review -> frozen Greenfield ReviewRequest, never evidence."""
    if not record or record.get('state') != 'OPEN':
        return None
    option_copy = {
        'approve': ('Apply proposed value', 'The model changes and downstream evidence is invalidated.'),
        'modify': ('Edit proposed value', 'The edited value requires new COMSOL readback and validation.'),
        'reject': ('Reject change', 'The model remains unchanged.'),
        'defer': ('Decide later', 'The review stays unresolved.'),
    }
    options = [{'id': action, 'label': option_copy[action][0], 'consequence': option_copy[action][1]}
               for action in record['allowedActions'] if action in option_copy]
    if not options:
        return None
    return {
        'id': record['reviewId'], 'reviewId': record['reviewId'],
        'runId': record['runId'], 'attemptId': record['attemptId'],
        'objectId': record['affectedObjectId'], 'objectLabel': record['affectedObjectId'],
        'question': 'Review the proposed scientific model change.',
        'whyNotAutomatic': 'A human decision is required before changing this model object.',
        'current': str(record['currentValue']), 'proposed': str(record['proposedValue']),
        'unit': record['unit'], 'reason': record['reason'], 'impact': record['downstreamImpact'],
        'options': options,
        'finePrint': 'Approval is a decision, not scientific verification. Revalidation is required.',
        'raisedAt': record['createdAt'], 'state': record['state'],
    }


def project_session(store: Any, session_id: str) -> dict[str, Any]:
    """Wire provider; mandatory unproven snapshot fields remain absent, not guessed."""
    session = store.get(session_id)
    run = store.latest_run(session_id)
    completion, _ = current_completion(run, session['events'])
    artifacts = [item for key in ARTIFACTS
                 if run and (item := project_artifact(run, completion, session['events'], key))]
    plan = session.get('plan') or {}
    model = session.get('model') or {}
    ids = {kind: object_id(session_id, kind, 'primary')
           for kind in ('geometry', 'material', 'mesh', 'solver')}
    ids['physics'] = object_id(session_id, 'physics', str(plan.get('workflow_id') or 'primary'))
    ids['boundaries'] = {side: object_id(session_id, 'boundary', side)
                         for side in ('left', 'right', 'top', 'bottom')}
    parameters = plan.get('parameters') or {}
    def parameter(name: str) -> dict[str, Any] | None:
        item = parameters.get(name)
        if not isinstance(item, dict):
            return None
        value, unit = item.get('normalized_value', item.get('value')), item.get('normalized_unit') or item.get('unit')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not unit:
            return None
        return {'value': value, 'unit': unit}
    components = {
        'geometry': {'id': ids['geometry'], 'length': parameter('length') or parameter('width'),
                     'height': parameter('height'), 'verification': 'unavailable'},
        'material': {'id': ids['material'], 'name': (plan.get('materials') or [None])[0],
                     'properties': {name: value for name in ('thermal_conductivity', 'density', 'heat_capacity',
                                                               'diffusion_coefficient', 'conductivity')
                                    if (value := parameter(name)) is not None},
                     'verification': 'unavailable'},
        'physics': {'id': ids['physics'], 'interface': (plan.get('physics') or [None])[0],
                    'verification': 'unavailable'},
        'boundaries': [{'id': ids['boundaries'][side], 'location': side,
                        'value': parameter(f'{side}_temperature') or parameter(f'{side}_concentration')
                                 or parameter(f'{side}_potential'), 'verification': 'unavailable'}
                       for side in ids['boundaries']],
        'mesh': {'id': ids['mesh'], 'verification': 'unavailable'},
        'solver': {'id': ids['solver'], 'verification': 'unavailable'},
    }
    readback = (completion or {}).get('build_readback') or {}
    validation = (completion or {}).get('validation') or {}
    if run and verified_verdict(run, completion) and isinstance(readback, dict):
        edges = readback.get('geometry_edges')
        if isinstance(edges, int) and not isinstance(edges, bool) and edges > 0:
            components['geometry']['boundaryCount'] = edges
            components['geometry']['boundaryCountSource'] = 'COMSOL geometry readback'
        physics_type = readback.get('physics_type')
        if isinstance(physics_type, str) and physics_type:
            components['physics']['activeInterfaceReadback'] = physics_type
            components['physics']['selections'] = readback.get('physics_domain_selection')
            components['physics']['verification'] = 'verified_readback'
        edge_map = readback.get('edge_map') or (completion or {}).get('boundary_map') or {}
        if isinstance(edge_map, dict):
            for boundary in components['boundaries']:
                side = boundary['location']
                selection = edge_map.get(side)
                if isinstance(selection, (int, list)):
                    boundary['selectionReadback'] = selection
                    boundary['verification'] = 'verified_readback'
                feature = readback.get(f'{side}_bc')
                if isinstance(feature, dict):
                    boundary['featureTag'] = feature.get('tag')
                    boundary['typeReadback'] = feature.get('type')
                    boundary['expressionReadback'] = feature.get('expression')
    if run and verified_verdict(run, completion) and isinstance(validation, dict):
        for source, target in (('node_count', 'nodeCount'), ('element_count', 'elementCount')):
            count = validation.get(source)
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                components['mesh'][target] = count
                components['mesh']['verification'] = 'solver_result'
        dof = validation.get('dof')
        if isinstance(dof, int) and not isinstance(dof, bool) and dof > 0:
            components['solver']['degreesOfFreedom'] = dof
            components['solver']['verification'] = 'solver_result'
    snapshot = project_scientific_snapshot(
        session_id, run, completion or _live_core_candidate(run, session['events'], session_id),
        session['events'])
    gaps = []
    if (plan or model) and snapshot is None:
        gaps.append('CONTRACT_GAP:full_model_snapshot_requires_proven_geometry_mesh_solver_fields')
    review = project_review(store.open_review(session_id))
    if review and (not run or run.get('outcome') is not None or run.get('cancel_requested')
                   or review['runId'] != run.get('run_id')
                   or review['attemptId'] != run.get('attempt_id')):
        review = None
    if session.get('state') in {'NEEDS_REVIEW', 'PAUSED'} and review is None:
        gaps.append('CONTRACT_GAP:structured_review_request_not_persisted')
    evidence_state = ('unavailable' if not run or (run.get('outcome') is not None and not artifacts) else
                      'stale' if any(item['evidenceState'] == 'stale' for item in artifacts) else
                      'invalid' if any(item['evidenceState'] == 'invalid' for item in artifacts) else
                      'verified' if artifacts and all(item['evidenceState'] == 'verified' for item in artifacts) else
                      'pending')
    return {
        'contractVersion': 'rc42-v1', 'studyId': session_id,
        'modelId': f'model-{session_id}', 'modelSnapshot': snapshot,
        'objectIds': ids, 'modelComponents': components,
        'runState': project_run(session, run, estimate_remaining_time(store, session_id)),
        'reviewRequest': review, 'resultArtifacts': artifacts,
        'evidenceState': evidence_state,
        'authoritativeVerdict': (completion or {}).get('execution_verdict'),
        'contractGaps': gaps,
    }


def project_event(session_id: str, event: dict[str, Any],
                  run: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Map durable runtime facts to the frozen domain vocabulary only."""
    source = event.get('type')
    raw = event.get('payload') or {}
    run_id = raw.get('run_id') or (run or {}).get('run_id')
    at = event.get('created_at', '')
    mapping: dict[str, tuple[str, dict[str, Any]] | None] = {
        'run.started': ('run.started', {'runId': run_id, 'studyId': session_id, 'at': at}),
        'execution.pause_requested': ('run.pause_requested', {'runId': run_id, 'at': at}),
        'execution.safe_boundary': ('run.paused', {'runId': run_id, 'at': at,
                                                   'safePoint': str(raw.get('node_id') or '')})
        if raw.get('state') == 'PAUSED' else None,
        'execution.resumed': ('run.resumed', {'runId': run_id, 'at': at}),
        'execution.correction_verified': ('model.object_changed', {
            'modelId': f'model-{session_id}',
            'objectId': str(raw.get('object_id') or ''),
            'change': 'parameter correction verified by COMSOL readback',
            'revision': int(event.get('sequence') or 0),
        }),
        'evidence.invalidated': ('evidence.invalidated', {
            'evidenceId': str(raw.get('evidence_id') or ''),
            'summary': {'state': 'invalidated', 'backendVerified': False,
                        'reason': 'A model input changed; downstream evidence requires revalidation.'},
        }),
    }
    if source == 'review.required':
        review = project_review(raw)
        if review:
            mapping[source] = ('review.required', {'request': review})
    if source == 'review.resolved' and raw.get('reviewId') and raw.get('affectedObjectId'):
        mapping[source] = ('review.resolved', {'decision': {
            'id': str(event.get('id') or ''), 'requestId': raw['reviewId'],
            'objectId': raw['affectedObjectId'], 'outcome': {
                'approve': 'approved', 'modify': 'modified', 'reject': 'rejected',
                'defer': 'deferred'}.get(raw.get('decision'), 'deferred'),
            'by': 'local user', 'at': raw.get('timestamp', at),
            **({'chosenValue': str(raw['editedValue'])} if 'editedValue' in raw else {}),
        }})
    if source == 'model.readback_updated' and raw.get('verified_real_comsol') is True and raw.get('object_id'):
        mapping[source] = ('model.readback_updated', {
            'modelId': f'model-{session_id}',
            'artifactId': f"readback-{raw.get('run_id')}-{raw.get('attempt_id')}-{raw['object_id']}",
            'evidence': {'state': 'invalidated', 'backendVerified': False,
                         'reason': 'COMSOL model readback matched; scientific evidence still requires revalidation.'},
        })
    if source == 'model.object_changed' and raw.get('object_id') and raw.get('feature_id'):
        mapping[source] = ('model.object_changed', {
            'modelId': f'model-{session_id}', 'objectId': str(raw['object_id']),
            'change': 'COMSOL boundary feature changed and directly read back; scientific result remains stale',
            'revision': int(event.get('sequence') or 0),
        })
    if source == 'result.available' and run_id and raw.get('attempt_id') == (run or {}).get('attempt_id'):
        digest = raw.get('sha256')
        low, high = raw.get('minimum'), raw.get('maximum')
        if (raw.get('evidence_state') == 'pending' and isinstance(digest, str) and len(digest) == 64
                and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) for value in (low, high)) and low <= high):
            artifact_id = str(raw.get('artifact_id') or '')
            if artifact_id == f"artifact-{run_id}-{raw['attempt_id']}-temperature_image":
                mapping[source] = ('result.available', {'artifact': {
                    'id': artifact_id, 'artifactId': artifact_id,
                    'modelId': f'model-{session_id}', 'runId': run_id,
                    'attemptId': raw['attempt_id'], 'quantity': 'Temperature',
                    'variable': 'Temperature', 'title': 'Temperature',
                    'metadata': {'plotKind': 'surface', 'unit': 'K',
                                 'range': {'min': low, 'max': high}},
                    'numbers': {'min': low, 'max': high}, 'sha256': digest,
                    'uri': f'/api/artifacts/{run_id}/temperature.png',
                    'provenance': 'New COMSOL field; certification pending',
                    'createdAt': at, 'evidenceState': 'pending',
                    'evidence': {'id': f'evidence-{artifact_id}', 'artifact': artifact_id,
                                 'runId': run_id, 'attemptId': raw['attempt_id'],
                                 'semanticState': 'pending', 'state': 'claimed',
                                 'backendVerified': False, 'readbackHash': digest,
                                 'claims': [], 'reason': 'Fresh scientific checks are pending.'},
                }})
    if source == 'state.changed' and run_id:
        state = str(raw.get('state') or '')
        phase = PHASES.get(state)
        if phase:
            snapshot = project_run({'created_at': at, 'events': []}, {**(run or {}), 'phase': state,
                                                                    'run_id': run_id})
            mapping[source] = ('run.progress', {'progress': snapshot})
    if source in {'run.failed', 'run.interrupted', 'run.canceled'} and run_id:
        code = 'solver_failed' if source == 'run.failed' else 'unknown'
        mapping[source] = ('error.raised', {'error': safe_error(code, location='Runtime',
                                                                diagnostic_id=str(event.get('id') or ''))})
    selected = mapping.get(source)
    if selected is None:
        return None
    name, payload = selected
    if not run_id and name.startswith('run.'):
        return None
    return {'name': name, 'payload': payload, 'sequence': int(event['sequence']), 'at': at}


def project_event_batch(store: Any, session_id: str, after: int = 0) -> dict[str, Any]:
    """Resumable source cursor with monotonic projected event sequence."""
    raw_events = store.events_after(session_id, after)
    run = store.latest_run(session_id)
    view = store.get(session_id)
    completion, completed_sequence = current_completion(run, view['events'])
    run_start = next((int(event['sequence']) for event in view['events']
                      if event.get('type') == 'run.started'
                      and (event.get('payload') or {}).get('run_id') == (run or {}).get('run_id')), None)
    output: list[dict[str, Any]] = []
    for source in raw_events:
        if run_start is None or int(source['sequence']) < run_start:
            continue
        projected = project_event(session_id, source, run)
        if projected:
            output.append({**projected, 'sequence': int(source['sequence']) * 100})
        if run and source['type'] == 'run.completed' and int(source['sequence']) == completed_sequence:
            early_result = any(item.get('type') == 'result.available'
                               and (item.get('payload') or {}).get('run_id') == run['run_id']
                               and (item.get('payload') or {}).get('attempt_id') == run['attempt_id']
                               and int(item['sequence']) < completed_sequence
                               for item in view['events'])
            artifacts = [artifact for key in ARTIFACTS
                         if (artifact := project_artifact(run, completion, view['events'], key))]
            if artifacts and verified_verdict(run, completion):
                for index, artifact in enumerate(artifacts, 1):
                    if artifact['evidenceState'] == 'verified':
                        output.append({'name': 'evidence.verified', 'payload': {
                            'evidenceId': artifact['evidence']['id'], 'summary': artifact['evidence']},
                            'sequence': int(source['sequence']) * 100 + index * 2 - 1,
                            'at': source['created_at']})
                    if not early_result:
                        output.append({'name': 'result.available', 'payload': {'artifact': artifact},
                                       'sequence': int(source['sequence']) * 100 + index * 2,
                                       'at': source['created_at']})
    return {'events': output, 'sourceCursor': max([after, *(int(e['sequence']) for e in raw_events)])}
