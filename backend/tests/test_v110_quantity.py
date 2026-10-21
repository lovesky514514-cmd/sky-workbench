"""Public-safe quantity regressions: generated cases are not COMSOL results."""
import math

import pytest

from app.agent.intent_compiler import compile_intent


def parse_quantity(*args, **kwargs):
    from app.scientific.quantity import parse_quantity as parse
    return parse(*args, **kwargs)


NUMBERS = [('0', 0), ('1', 1), ('-2.5', -2.5), ('.125', .125),
           ('1e-9', 1e-9), ('1E+3', 1000), ('1×10^-9', 1e-9),
           ('2.5×10^-3', .0025), ('−3e−2', -.03), ('123.456', 123.456)]
UNITS = [('diffusion', 'm²/s', 1, 0, 'm²/s'), ('diffusion', 'cm^2/s', 1e-4, 0, 'm²/s'),
         ('length', 'mm', .001, 0, 'm'), ('length', 'cm', .01, 0, 'm'),
         ('concentration', 'mol/m³', 1, 0, 'mol/m³'), ('temperature', '°C', 1, 273.15, 'K'),
         ('temperature', 'K', 1, 0, 'K'), ('temperature_difference', '°C', 1, 0, 'K'),
         ('diffusion', 'cm²/s', 1e-4, 0, 'm²/s'), ('temperature', '℃', 1, 273.15, 'K')]


@pytest.mark.parametrize('prefix', ['value: ', '数值：'])
@pytest.mark.parametrize('kind,unit,factor,offset,si', UNITS)
@pytest.mark.parametrize('literal,value', NUMBERS)
def test_quantity_normalization(prefix, kind, unit, factor, offset, si, literal, value):
    from app.scientific.quantity import find_quantity
    raw = f'{literal} {unit}'
    result = parse_quantity(raw, kind, start=len(prefix))
    assert result.issue is None
    assert math.isclose(result.normalized_value, value * factor + offset, rel_tol=1e-12, abs_tol=0.0)
    assert result.original_span == (len(prefix), len(prefix) + len(raw))
    assert result.original_unit == unit and result.si_unit == si
    assert result.original == raw
    assert find_quantity(prefix + raw, r'value|数值', kind) == result


@pytest.mark.parametrize('kind', ['diffusion', 'temperature', 'length', 'concentration'])
@pytest.mark.parametrize('unit', ['furlong', 'banana', 'mystery', 'cm2/sec', 'm²/suffix'])
def test_unknown_units_rejected(kind, unit):
    result = parse_quantity(f'1e-9 {unit}', kind)
    assert result.issue == 'unsupported_unit'
    assert result.normalized_value is None


@pytest.mark.parametrize('raw', ['1.2.3 m²/s', '1e m²/s', 'NaN m²/s', 'Inf m²/s', '1e999 m²/s', '1×10^- m²/s', '1e-999 m²/s', '1.0'])
def test_bad_quantity_is_structured(raw):
    result = parse_quantity(raw, 'diffusion')
    assert result.issue and result.normalized_value is None


@pytest.mark.parametrize('literal,expected', [('1e-5 cm^2/s', 1e-9), ('1×10^-9 m^2/s', 1e-9)])
def test_intent_diffusion_challenges(literal, expected):
    intent = compile_intent('2D stationary TDS left 1 mol/m^3 right 0 mol/m^3 D=' + literal)
    assert intent.parameters['diffusion_coefficient'].value == pytest.approx(expected, rel=1e-12, abs=0)
    assert not intent.validation_issues


def test_celsius_boundaries_and_bad_number():
    intent = compile_intent('2D stationary Heat Transfer left 20 °C right 40 °C insulated')
    values = {b.location: b.value for b in intent.boundary_conditions}
    assert values['Left'] == pytest.approx(293.15) and values['Right'] == pytest.approx(313.15)
    assert compile_intent('2D stationary TDS D=1.2.3 m^2/s').validation_issues
    assert compile_intent('2D stationary TDS D=1e-9 banana').validation_issues


def test_planner_preserves_normalization_and_provenance():
    from app.agent.planner import extract_tds_plan, extract_heat_transfer_plan
    prompt = '2D stationary TDS 10 mm x 5 mm left 1 mol/m^3 right 0 mol/m^3 no flux D=1×10^-5 cm²/s'
    parameter = extract_tds_plan(prompt)['parameters']['diffusion_coefficient']
    assert parameter['value'] == pytest.approx(1e-9, rel=1e-12, abs=0)
    assert parameter['original_unit'] == 'cm²/s'
    assert parameter['quantity']['original'] == '1×10^-5 cm²/s'
    heat = extract_heat_transfer_plan('2D stationary Heat Transfer 10 mm x 5 mm aluminum left 20 °C right 40 °C insulated')
    assert heat['parameters']['left_temperature']['original_unit'] == '°C'
    assert heat['parameters']['left_temperature']['value'] == pytest.approx(293.15)


def test_malformed_geometry_and_reversed_temperature_do_not_parse_suffix():
    from app.agent.planner import _temperature_for_side
    assert compile_intent('2D stationary Heat Transfer 1.2.3 mm x 5 mm left 300 K right 310 K').validation_issues
    assert _temperature_for_side('1.2.3 K left boundary', 'left') is None
    assert _temperature_for_side('20 °C left boundary', 'left') == (293.15, 'K')


@pytest.mark.parametrize('number', ['1e-2', '1×10^-2'])
def test_planner_scientific_length(number):
    from app.agent.planner import extract_tds_plan, extract_heat_transfer_plan
    geometry = f'{number} m x 5 mm'
    heat = extract_heat_transfer_plan(f'2D stationary Heat Transfer {geometry} aluminum left 373 K right 293 K insulated')
    tds = extract_tds_plan(f'2D stationary TDS {geometry} left 1 mol/m^3 right 0 mol/m^3 no flux D=1e-9 m^2/s')
    assert heat['parameters']['width']['normalized_value'] == pytest.approx(.01)
    assert tds['parameters']['length']['normalized_value'] == pytest.approx(.01)
