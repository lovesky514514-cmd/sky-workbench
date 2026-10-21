"""Fresh numerical Heat checks after an owned COMSOL boundary correction.

The Java Bridge only transports COMSOL field/readback/export facts. Python owns
the scientific comparison and the existing Validator/Verdict remain authority.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from pathlib import Path
from typing import Any
import math
import re

import numpy as np

from app.scientific.schemas import ScientificPlan


def calculate_corrected_heat_validation(field: dict[str, Any], width: float, height: float,
                                        left_K: float, right_K: float) -> dict[str, Any]:
    if any(not math.isfinite(value) or value <= 0 for value in (width, height, left_K, right_K)):
        raise ValueError('Corrected Heat geometry and temperatures must be finite and positive')
    arrays = []
    for key in ('x', 'y', 'temperature_K'):
        raw = field.get(key)
        if not isinstance(raw, list) or not raw:
            raise ValueError(f'Post-correction COMSOL field {key} is absent')
        try:
            array = np.asarray(raw, dtype=float).reshape(-1)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Post-correction COMSOL field {key} is malformed') from exc
        if not np.all(np.isfinite(array)):
            raise ValueError(f'Post-correction COMSOL field {key} contains non-finite values')
        arrays.append(array)
    x, y, temperature = arrays
    if not (x.size == y.size == temperature.size) or x.size < 2:
        raise ValueError('Post-correction COMSOL field arrays are misaligned')
    tolerance = max(width, height) * 1e-5 + 1e-8
    left_mask = np.abs(x) <= tolerance
    right_mask = np.abs(x - width) <= tolerance
    if not np.any(left_mask) or not np.any(right_mask):
        raise ValueError('Post-correction COMSOL field lacks physical boundary samples')
    if np.min(x) < -tolerance or np.max(x) > width + tolerance \
            or np.min(y) < -tolerance or np.max(y) > height + tolerance:
        raise ValueError('Post-correction COMSOL coordinates exceed the proven geometry')
    left_median = float(np.median(temperature[left_mask]))
    right_median = float(np.median(temperature[right_mask]))
    expected = left_K + (right_K - left_K) * (x / width)
    rmse = float(np.sqrt(np.mean((temperature - expected) ** 2)))
    low = float(np.min(temperature))
    high = float(np.max(temperature))
    bounded = low >= min(left_K, right_K) - 1.0 and high <= max(left_K, right_K) + 1.0
    boundary = abs(left_median - left_K) <= 1.0 and abs(right_median - right_K) <= 1.0
    analytical = rmse <= 1.0
    # The canonical plate is one-dimensional at steady state. Checking the
    # residual to its analytical line is stronger than inferring monotonicity
    # from only the two Dirichlet boundaries.
    monotonic = bool(np.max(np.abs(temperature - expected)) <= 1.0)
    return {
        'passed': bool(bounded and boundary and analytical and monotonic),
        'temperature_min_K': low, 'temperature_max_K': high,
        'left_boundary_median_K': left_median, 'right_boundary_median_K': right_median,
        'analytical_rmse_K': rmse, 'range_check': bool(bounded),
        'boundary_check': bool(boundary), 'analytical_check': bool(analytical),
        'monotonicity_check': monotonic, 'node_count': int(temperature.size),
        'dof': int(temperature.size),
    }


def _kelvin(expression: Any) -> float:
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\[K\]\s*', str(expression))
    if not match:
        raise ValueError('Corrected COMSOL boundary readback is not a literal Kelvin value')
    value = float(match.group(1))
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Corrected COMSOL boundary readback is not finite and positive')
    return value


def normalize_heat_core(raw: dict[str, Any], left: dict[str, Any],
                        right: dict[str, Any]) -> dict[str, Any]:
    """Convert direct Java Model API readback to the provider's core schema."""
    if raw.get('model_state_verified') is not True or raw.get('geometry_kind') != 'Rectangle':
        raise ValueError('Real COMSOL Heat core readback is unavailable')
    core = {key: raw.get(key) for key in (
        'model_tag', 'geometry_tag', 'geometry_feature_tag', 'geometry_kind',
        'geometry_size', 'geometry_unit', 'geometry_domains', 'geometry_edges',
        'material_tag', 'material_name', 'material_origin', 'physics_tag',
        'physics_type', 'study_tag', 'study_type', 'solver_tag', 'mesh_tag')}
    core['source'] = 'direct_comsol_readback'
    properties = []
    for symbol, key, unit in (
        ('k', 'k_expression', 'W/(m*K)'),
        ('rho', 'rho_expression', 'kg/m^3'),
        ('Cp', 'Cp_expression', 'J/(kg*K)'),
    ):
        expression = raw.get(key)
        match = re.fullmatch(
            r'\s*(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\[' + re.escape(unit) + r'\]\s*',
            str(expression))
        if match is None:
            raise ValueError(f'COMSOL {symbol} property has no supported direct literal readback')
        value = float(match.group(1))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'COMSOL {symbol} property is non-finite or non-positive')
        properties.append({'symbol': symbol, 'value': value, 'unit': unit})
    core['material_properties'] = properties
    for side, feature in (('left', left), ('right', right)):
        if feature.get('model_state_verified') is not True \
                or feature.get('model_tag') != raw.get('model_tag') \
                or feature.get('feature_tag') != f'temp_{side}' \
                or feature.get('feature_type') != 'TemperatureBoundary' \
                or feature.get('unit') != 'K':
            raise ValueError(f'COMSOL {side} boundary direct readback is unavailable')
        _kelvin(feature.get('expression'))
        core[f'{side}_bc'] = {'tag': feature['feature_tag'], 'type': feature['feature_type'],
                              'selection': feature.get('selection'),
                              'expression': feature['expression']}
    return core


def corrected_heat_execution(*, plan: ScientificPlan, original: dict[str, Any],
                             correction: dict[str, Any], left_readback: dict[str, Any],
                             right_readback: dict[str, Any], field: dict[str, Any],
                             exported: dict[str, Any], study_date: int,
                             run_id: str, attempt_id: str, attempt_workspace: Path,
                             model_tree: dict[str, Any]) -> tuple[ScientificPlan, dict[str, Any]]:
    """Build a new execution candidate; caller must still run all three gates."""
    core = original.get('build_readback')
    if not isinstance(core, dict) or core.get('source') != 'direct_comsol_readback':
        raise ValueError('Original Heat core COMSOL readback is unavailable')
    if field.get('model_tag') != model_tree.get('model_tag') \
            or field.get('model_tag') != left_readback.get('model_tag') \
            or field.get('model_tag') != right_readback.get('model_tag') \
            or not all(model_tree.get(key) for key in ('geometry', 'physics', 'mesh', 'studies', 'solvers')):
        raise ValueError('Post-correction COMSOL model identity/tree readback is incomplete')
    if not isinstance(study_date, int) or study_date <= 0 \
            or field.get('last_computation_date') != study_date \
            or exported.get('last_computation_date') != study_date:
        raise ValueError('Field/export are not bound to the new COMSOL study solve')
    if exported.get('run_id') != run_id or exported.get('attempt_id') != attempt_id \
            or exported.get('command_success') is not True or exported.get('model_state_verified') is not True:
        raise ValueError('Export does not belong to the current run and attempt')
    corrected = plan.model_copy(deep=True)
    readback = deepcopy(core)
    for side, fresh in (('left', left_readback), ('right', right_readback)):
        old = core.get(f'{side}_bc')
        if not isinstance(old, dict) or fresh.get('feature_tag') != old.get('tag') \
                or fresh.get('feature_type') != 'TemperatureBoundary' \
                or fresh.get('selection') != old.get('selection') \
                or fresh.get('model_state_verified') is not True:
            raise ValueError(f'Post-correction {side} boundary identity/selection changed')
        value = _kelvin(fresh.get('expression'))
        readback[f'{side}_bc'] = {'tag': old['tag'], 'type': 'TemperatureBoundary',
                                 'selection': fresh['selection'], 'expression': fresh['expression']}
        parameter = corrected.parameters[f'{side}_temperature']
        parameter.value = value
        parameter.normalized_value = value
        parameter.normalized_unit = 'K'
        if side == correction.get('path', '').removeprefix('boundary_conditions.'):
            if value != float(correction.get('value')) or correction.get('unit') != 'K':
                raise ValueError('Corrected Heat boundary does not match human choice')
            parameter.source = 'user_correction_verified_comsol_readback'
    width, height = readback['geometry_size']
    validation = calculate_corrected_heat_validation(
        field, float(width), float(height),
        float(corrected.parameters['left_temperature'].value),
        float(corrected.parameters['right_temperature'].value),
    )
    artifact_paths = {}
    for key in ('model', 'temperature_image'):
        value = exported.get(key)
        if not isinstance(value, str):
            raise ValueError(f'Corrected Heat {key} artifact is missing')
        path = Path(value).resolve()
        if not path.is_relative_to(attempt_workspace.resolve()) or not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f'Corrected Heat {key} artifact is absent or outside current attempt')
        if path == Path((original.get('artifacts') or {}).get(key, '')).resolve():
            raise ValueError('Corrected Heat artifact reused its pre-correction path')
        artifact_paths[key] = str(path)
    if Path(artifact_paths['temperature_image']).name != 'temperature.png':
        raise ValueError('Corrected Heat image does not match the product artifact contract')
    # COMSOL auto-scales the temperature colormap, so a genuinely new 350 K
    # field can render byte-identically to the old 373 K gradient. Preserve the
    # raw post-solve values as a separate attempt-scoped, non-overwriting file.
    samples = [field[key] for key in ('x', 'y', 'temperature_K')]
    csv_path = Path(artifact_paths['model']).parent / 'temperature_field.csv'
    with csv_path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(('x_model_unit', 'y_model_unit', 'temperature_K'))
        writer.writerows(zip(*samples, strict=True))
    artifact_paths['temperature_field_csv'] = str(csv_path)
    execution = deepcopy(original)
    execution.pop('evidence_contract', None)
    execution.pop('execution_verdict', None)
    execution.update(run_id=run_id, attempt_id=attempt_id, success=True,
                     command_success=True, model_state_verified=True,
                     scientific_validation_success=validation['passed'],
                     validation=validation, artifacts=artifact_paths,
                     build_readback=readback,
                     boundary_feature_readback={'left': left_readback, 'right': right_readback},
                     corrected_study_date=study_date,
                     corrected_field_sample_count=field.get('sample_count'))
    model_state = execution['model_state']
    model_state['parameters'] = corrected.execution_payload()['parameters']
    model_state['boundary_conditions'] = [
        f"Left: {corrected.parameters['left_temperature'].value:g} K (COMSOL edge {left_readback['selection'][0]})",
        f"Right: {corrected.parameters['right_temperature'].value:g} K (COMSOL edge {right_readback['selection'][0]})",
        'Top and bottom: Thermal insulation',
    ]
    model_state['results'] = [f"Temperature {validation['temperature_min_K']:.3f} to {validation['temperature_max_K']:.3f} K"]
    model_state['solver'] = [f'COMSOL stationary solver; converged; fresh computation date {study_date}']
    return corrected, execution
