"""One immutable execution gate. ValidationReport retains its PRECHECK semantics.

Worker claims are necessary, never sufficient: the workflow validator and the
required evidence envelope must both agree. No scientific thresholds live here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.validators.base_validator import ValidationCheck, ValidationReport


def _requirements(build: str, science: str, report: str = ''):
    return MappingProxyType({'build_readback': tuple(build.split()),
                             'validation': tuple(science.split()), 'report': tuple(report.split())})


REQUIRED_CHECKS = MappingProxyType({
    'heat_transfer_2d_stationary': _requirements('', '',
        'geometry_tree material_tree physics_tree boundary_tree mesh_tree stationary_study solver_convergence left_boundary_temperature right_boundary_temperature temperature_range analytical_solution'),
    'tds_2d_stationary_diffusion': _requirements(
        'interface_exists interface_type domain_selection diffusion_feature_type diffusion_mode diffusion_expression zero_velocity left_bc_type left_bc_selection left_bc_species left_bc_expression right_bc_type right_bc_selection right_bc_species right_bc_expression no_flux_type no_flux_selection stationary_study parameter_readback',
        'solver_completed nonzero_field left_boundary_concentration right_boundary_concentration physical_range analytical_rmse analytical_mae analytical_max_abs_error monotonicity y_uniformity flux_sign flux_relative_error mass_balance'),
    'solid_mechanics_uniaxial_bar': _requirements(
        'solid_interface domain_nonempty elastic_model youngs_modulus poissons_ratio density fixed_face loaded_face traction stationary_study parameter_readback',
        'solver_completed finite_fields positive_displacement displacement_analytical axial_stress_analytical von_mises_analytical force_equilibrium'),
    'electrochem_primary_current': _requirements(
        'primary_interface domain_nonempty electrolyte conductivity left_potential right_potential stationary parameter_readback',
        'solver_completed finite_fields potential_linear current_density potential_bounds'),
    'lithium_ion_battery_baseline': _requirements(
        'battery_license battery_specific_interface separator_domain negative_electrode_domain positive_electrode_domain ground_boundary current_boundary initial_concentration time_dependent_study parameter_readback',
        'solver_completed finite_fields positive_concentration physical_cell_voltage discharge_voltage_direction boundary_current_matches current_conservation electrode_soc_changes opposite_electrode_inventory_direction unit_consistency parameter_provenance'),
    'heat_tds_temperature_coupling': _requirements(
        'heat_interface tds_interface shared_domain temperature_variable_real dependency_readback stationary_study parameters_readback',
        'solver_off_completed solver_on_completed finite_fields heat_analytical variable_diffusion_analytical constant_diffusion_flux variable_diffusion_flux mass_balance off_on_solutions_differ physical_direction coupling_dependency_verified'),
})


def required_validation_checks(workflow_id: str, execution: dict[str, Any]) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    for section, category in [('build_readback', 'Model Readback'), ('validation', 'Scientific Result')]:
        block = execution.get(section)
        supplied = block.get('checks', {}) if isinstance(block, dict) else {}
        supplied = supplied if isinstance(supplied, dict) else {}
        names = dict.fromkeys((*REQUIRED_CHECKS[workflow_id][section], *supplied))
        checks.extend(ValidationCheck(category=category, name=name, status='passed' if supplied.get(name) is True else 'failed',
            message=f'Required execution check: {name}', observed=supplied.get(name), expected=True) for name in names)
    return checks


@dataclass(frozen=True)
class ExecutionVerdict:
    run_id: str
    workflow_id: str
    command_success: bool
    model_state_verified: bool
    scientific_validation_success: bool
    issues: tuple[str, ...]
    evidence_contract_passed: bool = False

    @property
    def completed(self) -> bool:
        return all((self.command_success, self.model_state_verified,
                    self.scientific_validation_success,
                    self.evidence_contract_passed)) and not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {'schema_version': 2, 'run_id': self.run_id, 'workflow_id': self.workflow_id,
                'command_success': self.command_success, 'model_state_verified': self.model_state_verified,
                'scientific_validation_success': self.scientific_validation_success,
                'evidence_contract_passed': self.evidence_contract_passed,
                'completed': self.completed, 'issues': list(self.issues)}

    def summary(self) -> str:
        yes = lambda value: 'yes' if value else 'no'
        return (f'Command success: {yes(self.command_success)}. '
                f'Model state verified: {yes(self.model_state_verified)}. '
                f'Scientific validation passed: {yes(self.scientific_validation_success)}. '
                f'Evidence contract passed: {yes(self.evidence_contract_passed)}.')

    def reject(self, issue: str) -> ExecutionVerdict:
        """Invalidate completion without rewriting previously observed facts."""
        return replace(self, issues=(*self.issues, issue))

    def invalidate_scientific_evidence(self, issue: str) -> ExecutionVerdict:
        """Project a model-changing correction onto the current certification state."""
        issues = self.issues if issue in self.issues else (*self.issues, issue)
        return replace(
            self,
            scientific_validation_success=False,
            evidence_contract_passed=False,
            issues=issues,
        )

    def fields(self) -> dict[str, Any]:
        """Compatibility fields are projections, not independent success sources."""
        return {'execution_verdict': self.as_dict(), 'command_success': self.command_success,
                'model_state_verified': self.model_state_verified,
                'scientific_validation_success': self.scientific_validation_success,
                'evidence_contract_passed': self.evidence_contract_passed}

    def validation_report(self, report: ValidationReport) -> dict[str, Any]:
        return {**report.model_dump(mode='json'), 'passed': self.completed,
                **{key: value for key, value in self.fields().items() if key != 'execution_verdict'},
                'execution_verdict': self.as_dict()}


def _numeric_issues(value: Any, path: str = 'validation') -> list[str]:
    issues: list[str] = []
    if isinstance(value, dict):
        for name, child in value.items():
            issues.extend(_numeric_issues(child, f'{path}.{name}'))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            issues.extend(_numeric_issues(child, f'{path}[{index}]'))
    elif not isinstance(value, bool) and isinstance(value, (int, float, str)):
        try:
            number = float(value)
        except (ValueError, OverflowError):
            return issues
        if not math.isfinite(number):
            issues.append(f'nonfinite:{path}')
        elif number < 0 and any(token in path.lower() for token in ('error', 'rmse', 'mae', 'spread', 'violations')):
            issues.append(f'negative_error:{path}')
    return issues


def evaluate_evidence_contract(execution: dict[str, Any], expected_run_id: str,
                               expected_attempt_id: str | None = None) -> dict[str, Any]:
    """Derive product evidence from runtime-owned bytes and readback.

    Worker booleans are ignored. Directories, missing/empty files, and a claimed
    manifest are not accepted as artifact evidence.
    """
    issues: list[str] = []
    attempt_id = execution.get('attempt_id')
    if not isinstance(attempt_id, str) or not attempt_id:
        issues.append('evidence:attempt_id_missing')
    if expected_attempt_id is not None and attempt_id != expected_attempt_id:
        issues.append('evidence:attempt_id_mismatch')
    if execution.get('run_id') != expected_run_id:
        issues.append('evidence:run_id_mismatch')

    model = execution.get('model_state')
    for field in ('geometry', 'physics', 'boundary_conditions', 'mesh', 'study', 'solver', 'results'):
        if not isinstance(model, dict) or not isinstance(model.get(field), list) or not model[field]:
            issues.append(f'evidence:model_state.{field}')

    validation = execution.get('validation')
    dof = validation.get('dof') if isinstance(validation, dict) else None
    if isinstance(dof, bool) or not isinstance(dof, (int, float)) or not math.isfinite(float(dof)) or float(dof) <= 0:
        issues.append('evidence:positive_dof_missing')

    artifacts = execution.get('artifacts')
    manifest: dict[str, dict[str, Any]] = {}
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get('model'), str):
        issues.append('evidence:model_artifact_missing')
    else:
        for name, raw_path in sorted(artifacts.items()):
            if not isinstance(raw_path, str) or not raw_path:
                continue
            path = Path(raw_path)
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
                if size <= 0:
                    issues.append(f'evidence:empty_artifact.{name}')
                    continue
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                manifest[name] = {'path': str(path), 'size_bytes': size, 'sha256': digest.hexdigest()}
            except OSError:
                issues.append(f'evidence:unreadable_artifact.{name}')
        if 'model' not in manifest:
            issues.append('evidence:model_artifact_unverified')
        if len(manifest) < 2:
            issues.append('evidence:insufficient_artifact_manifest')
    return {'schema_version': 1, 'run_id': expected_run_id, 'attempt_id': attempt_id,
            'passed': not issues, 'issues': issues, 'artifacts': manifest}


def verify_artifact_integrity(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    """Reopen a completed artifact and compare its current bytes to frozen evidence."""

    issues: list[str] = []
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            issues.append('artifact:not_file')
            raise OSError('not a file')
        size = resolved.stat().st_size
        digest = hashlib.sha256()
        with resolved.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        actual_sha256 = digest.hexdigest()
    except OSError:
        return {'schema_version': 1, 'passed': False, 'path': str(path), 'size_bytes': None,
                'sha256': None, 'issues': [*issues, 'artifact:missing_or_unreadable']}

    expected_path = expected.get('path')
    if isinstance(expected_path, str):
        try:
            if Path(expected_path).resolve() != resolved:
                issues.append('artifact:path_mismatch')
        except OSError:
            issues.append('artifact:path_mismatch')
    if expected.get('size_bytes') != size:
        issues.append('artifact:size_mismatch')
    if expected.get('sha256') != actual_sha256:
        issues.append('artifact:sha256_mismatch')
    return {'schema_version': 1, 'passed': not issues, 'path': str(resolved),
            'size_bytes': size, 'sha256': actual_sha256, 'issues': issues}


def execution_verdict(workflow_id: str, expected_run_id: str, execution: dict[str, Any],
                      report: ValidationReport, *, expected_attempt_id: str | None = None,
                      derived_evidence: dict[str, Any] | None = None) -> ExecutionVerdict:
    issues: list[str] = []
    identity_valid = bool(expected_run_id) and execution.get('run_id') == expected_run_id
    if not identity_valid:
        issues.append('run_id_mismatch')
    required = REQUIRED_CHECKS.get(workflow_id)
    if required is None:
        issues.append('unknown_workflow')
    missing_build: list[str] = []
    missing_science: list[str] = []
    for section, names in (required or {}).items():
        if section == 'report':
            checks = {item.name: item.status == 'passed' for item in report.checks}
        else:
            block = execution.get(section)
            checks = block.get('checks', {}) if isinstance(block, dict) else {}
        for name in names:
            if not isinstance(checks, dict) or checks.get(name) is not True:
                issues.append(f'required_check:{section}.{name}')
                (missing_build if section == 'build_readback' or name.endswith('_tree') else missing_science).append(name)
    validation = execution.get('validation')
    if not isinstance(validation, dict) or validation.get('passed') is not True:
        issues.append('validation_not_passed')
        missing_science.append('passed')
    numeric_issues = _numeric_issues(execution, 'execution')
    issues.extend(numeric_issues)
    independent_valid = report.scope == 'solution' and report.passed and bool(report.checks) and all(check.status != 'failed' for check in report.checks)
    if not independent_valid:
        issues.append('independent_validation_failed')
    if workflow_id == 'heat_tds_temperature_coupling' and execution.get('coupling_validation_success') is not True:
        issues.append('coupling_validation_failed')
        missing_science.append('coupling')
    command = identity_valid and execution.get('command_success') is True and report.command_success is True and execution.get('success') is not False
    model = identity_valid and required is not None and execution.get('model_state_verified') is True and report.model_state_verified is True and not missing_build
    scientific = identity_valid and required is not None and execution.get('scientific_validation_success') is True and report.scientific_validation_success is True and not missing_science and not numeric_issues and independent_valid
    evidence = derived_evidence or evaluate_evidence_contract(execution, expected_run_id, expected_attempt_id)
    execution['evidence_contract'] = evidence
    evidence_passed = evidence['passed'] is True
    issues.extend(evidence['issues'])
    for name, passed in [('command_success', command), ('model_state_verified', model), ('scientific_validation_success', scientific)]:
        if not passed:
            issues.append(f'{name}:not_verified')
    if not evidence_passed:
        issues.append('evidence_contract_passed:not_verified')
    return ExecutionVerdict(expected_run_id, workflow_id, command, model, scientific, tuple(dict.fromkeys(issues)), evidence_passed)
