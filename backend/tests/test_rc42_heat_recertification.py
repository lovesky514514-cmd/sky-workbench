"""Synthetic unit checks for recertification logic; never called real COMSOL evidence."""
from __future__ import annotations

import pytest

from app.scientific.heat_recertification import calculate_corrected_heat_validation


def test_new_field_is_validated_against_corrected_boundary():
    field = {'x': [0.0, 0.0, 5.0, 5.0, 10.0, 10.0],
             'y': [0.0, 5.0, 0.0, 5.0, 0.0, 5.0],
             'temperature_K': [350.0, 350.0, 321.5, 321.5, 293.0, 293.0]}
    result = calculate_corrected_heat_validation(field, 10.0, 5.0, 350.0, 293.0)
    assert result['passed'] is True
    assert result['temperature_max_K'] == 350.0
    assert result['monotonicity_check'] is True
    assert result['analytical_rmse_K'] == 0.0


def test_old_373_field_cannot_pass_corrected_350_boundary():
    field = {'x': [0.0, 10.0], 'y': [0.0, 0.0], 'temperature_K': [373.0, 293.0]}
    result = calculate_corrected_heat_validation(field, 10.0, 5.0, 350.0, 293.0)
    assert result['passed'] is False
    assert result['boundary_check'] is False


@pytest.mark.parametrize('field', [
    {'x': [0.0], 'y': [], 'temperature_K': [350.0]},
    {'x': [0.0, 10.0], 'y': [0.0, 0.0], 'temperature_K': [float('nan'), 293.0]},
    {'x': [0.0, 10.0], 'y': [0.0, 0.0], 'temperature_K': [float('inf'), 293.0]},
    {'x': [5.0, 5.0], 'y': [0.0, 5.0], 'temperature_K': [321.5, 321.5]},
])
def test_malformed_or_incomplete_field_is_rejected(field):
    with pytest.raises(ValueError):
        calculate_corrected_heat_validation(field, 10.0, 5.0, 350.0, 293.0)
