"""Adversarial execution-envelope tests. Synthetic checks are never scientific evidence."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from itertools import product
from pathlib import Path
import tempfile

import pytest

from app.validators.base_validator import ValidationCheck, ValidationReport
from app.validators.execution_verdict import REQUIRED_CHECKS, execution_verdict


_ARTIFACT_ROOT = tempfile.TemporaryDirectory(prefix='workbench-verdict-fixtures-')


def payload_for(workflow, root=None):
    root = Path(root or _ARTIFACT_ROOT.name)
    root.mkdir(parents=True, exist_ok=True)
    model_path = root / f'{workflow}.mph'
    evidence_path = root / f'{workflow}.json'
    model_path.write_bytes(b'public-safe model contract fixture')
    evidence_path.write_text('{"fixture":true}', encoding='utf-8')
    required = REQUIRED_CHECKS[workflow]
    payload = {'run_id': 'current-run', 'command_success': True, 'model_state_verified': True,
               'scientific_validation_success': True, 'coupling_validation_success': True,
               'attempt_id': 'attempt-1',
               'validation': {'passed': True, 'checks': {}, 'dof': 12}, 'build_readback': {'checks': {}},
               'artifacts': {'model': str(model_path), 'validation': str(evidence_path)},
               'model_state': {field: ['fixture'] for field in ('geometry', 'physics', 'boundary_conditions', 'mesh', 'study', 'solver', 'results')}}
    checks = []
    for section, names in required.items():
        for name in names:
            if section != 'report':
                payload[section]['checks'][name] = True
            checks.append(ValidationCheck(category='Test', name=name, status='passed', message='synthetic contract input'))
    report = ValidationReport.from_checks('solution', checks, command_success=True, model_state_verified=True)
    return payload, report


@pytest.mark.parametrize('workflow', list(REQUIRED_CHECKS))
def test_incomplete_negative_matrix(workflow):
    payload, report = payload_for(workflow)
    assert execution_verdict(workflow, 'current-run', payload, report, expected_attempt_id='attempt-1').completed
    for section, names in REQUIRED_CHECKS[workflow].items():
        for name in names:
            damaged = deepcopy(payload)
            reduced = report.model_copy(deep=True)
            if section == 'report':
                reduced.checks = [check for check in reduced.checks if check.name != name]
            else:
                del damaged[section]['checks'][name]
            assert not execution_verdict(workflow, 'current-run', damaged, reduced, expected_attempt_id='attempt-1').completed, (workflow, section, name)
            if section != 'report':
                damaged[section]['checks'][name] = False
                assert not execution_verdict(workflow, 'current-run', damaged, report, expected_attempt_id='attempt-1').completed
            else:
                failed = report.model_copy(deep=True)
                next(check for check in failed.checks if check.name == name).status = 'failed'
                assert not execution_verdict(workflow, 'current-run', payload, failed, expected_attempt_id='attempt-1').completed


@pytest.mark.parametrize('workflow', list(REQUIRED_CHECKS))
@pytest.mark.parametrize('flags', list(product([False, True], repeat=3)))
def test_eight_independent_states(workflow, flags):
    payload, report = payload_for(workflow)
    for name, flag in zip(('command_success', 'model_state_verified', 'scientific_validation_success'), flags):
        payload[name] = flag
    verdict = execution_verdict(workflow, 'current-run', payload, report, expected_attempt_id='attempt-1')
    assert verdict.completed == all(flags)
    assert (verdict.command_success, verdict.model_state_verified, verdict.scientific_validation_success) == flags
    with pytest.raises((FrozenInstanceError, AttributeError)):
        verdict.command_success = True


@pytest.mark.parametrize('workflow', list(REQUIRED_CHECKS))
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), -0.01, 'NaN', 'Inf'])
def test_invalid_metrics_rejected(workflow, bad):
    payload, report = payload_for(workflow)
    payload['validation']['analytical_rmse'] = bad
    assert not execution_verdict(workflow, 'current-run', payload, report, expected_attempt_id='attempt-1').completed


@pytest.mark.parametrize('workflow', list(REQUIRED_CHECKS))
@pytest.mark.parametrize('field', ['run_id', 'command_success', 'model_state_verified', 'scientific_validation_success'])
def test_missing_flags_and_wrong_run_rejected(workflow, field):
    payload, report = payload_for(workflow)
    del payload[field]
    assert not execution_verdict(workflow, 'current-run', payload, report, expected_attempt_id='attempt-1').completed
    payload['run_id'] = 'previous-run'
    assert not execution_verdict(workflow, 'current-run', payload, report, expected_attempt_id='attempt-1').completed


def test_precheck_unchanged():
    report = ValidationReport.from_checks('precheck', [])
    assert report.passed and not report.command_success


def test_audit_incomplete_payloads_fail_at_physics_validator_too():
    from app.validators.physics.battery_validator import BatteryValidator
    from app.validators.physics.multiphysics_validator import MultiphysicsValidator
    assert not BatteryValidator().validate_execution(None, {'validation': {'voltage_initial_V': 4, 'voltage_final_V': 3.9, 'current_relative_error': 0}}).passed
    assert not MultiphysicsValidator().validate_execution(None, {'validation': {'on_flux_relative_error': 0, 'mass_balance_relative_error': 0, 'off_on_concentration_rmse_mol_per_m3': .1}}).passed


@pytest.mark.parametrize('workflow,worker', [
    ('tds_2d_stationary_diffusion', 'tds'), ('solid_mechanics_uniaxial_bar', 'solid'),
    ('electrochem_primary_current', 'electrochem'), ('lithium_ion_battery_baseline', 'battery'),
    ('heat_tds_temperature_coupling', 'multiphysics')])
def test_requirements_match_existing_worker_check_contract(workflow, worker):
    import ast
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / 'app' / 'comsol' / f'{worker}_worker.py'
    dictionaries = {}
    for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    dictionaries[target.id] = {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
    assert set(REQUIRED_CHECKS[workflow]['build_readback']) == dictionaries.get('build_checks', dictionaries.get('checks'))
    assert set(REQUIRED_CHECKS[workflow]['validation']) == dictionaries['solution_checks']
