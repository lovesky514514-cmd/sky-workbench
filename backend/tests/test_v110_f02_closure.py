"""Geometry normalization closure: no COMSOL or private input required."""
import pytest

from app.agent.intent_compiler import compile_intent
from app.agent.planner import (extract_heat_transfer_plan, extract_tds_plan,
    extract_solid_plan, extract_electrochem_plan, _tds_dimensions)
from app.validators.plan_quality_validator import _extract_requested_facts

PARSERS = {'heat': extract_heat_transfer_plan, 'tds': extract_tds_plan,
           'solid': extract_solid_plan, 'electrochem': extract_electrochem_plan}
FORMATS = ['1e-2', '1E-2', '1×10^-2', '1*10^-2']


def prompt_for(workflow, first, form='product', language='en'):
    if form == 'product':
        geometry = f'{first} x 5 mm' + (' x 2 mm' if workflow == 'solid' else '')
    elif language == 'zh':
        geometry = f'长度={first}，' + ('宽度=5 mm，高度=2 mm' if workflow == 'solid' else '高度=5 mm')
    else:
        geometry = f'L={first}; ' + ('W=5 mm; H=2 mm' if workflow == 'solid' else 'H=5 mm')
    prefix = {
        'heat': '2D stationary Heat Transfer', 'tds': '2D stationary TDS',
        'solid': '3D stationary Solid Mechanics', 'electrochem': '2D stationary primary current distribution',
    }[workflow]
    if language == 'zh':
        prefix = {'heat': '二维稳态传热', 'tds': '二维稳态稀物质传递',
                  'solid': '三维稳态固体力学', 'electrochem': '二维稳态一次电流分布'}[workflow]
    suffix = {'heat': 'aluminum left 373 K right 293 K insulated',
        'tds': 'D=1e-9 m^2/s left 1 mol/m^3 right 0 mol/m^3 no flux',
        'solid': 'E=200 GPa nu=0.3 density=7800 kg/m^3 traction=1 MPa left fixed',
        'electrochem': 'conductivity 1 S/m left 0 V right 1 V'}[workflow]
    return f'{prefix} {geometry} {suffix}'


@pytest.mark.parametrize('workflow', PARSERS)
@pytest.mark.parametrize('literal', FORMATS)
@pytest.mark.parametrize('form', ['product', 'labeled'])
@pytest.mark.parametrize('language', ['en', 'zh'])
def test_cross_workflow_geometry_contract(workflow, literal, form, language):
    prompt = prompt_for(workflow, literal + ' m', form, language)
    plan = PARSERS[workflow](prompt)
    assert plan['runnable'], plan['missing_information']
    intent = compile_intent(prompt)
    expected = [.01, .005, .002] if workflow == 'solid' else [.01, .005]
    names = ['length', 'width', 'height'] if workflow == 'solid' else ['width' if workflow == 'heat' else 'length', 'height']
    assert len(intent.geometry_quantities) == len(expected)
    for quantity, name, value in zip(intent.geometry_quantities, names, expected, strict=True):
        parameter = plan['parameters'][name]
        assert quantity['normalized_value'] == pytest.approx(value, rel=1e-12, abs=0)
        assert parameter['normalized_unit'] == quantity['si_unit'] == 'm'
        assert parameter['normalized_value'] == quantity['normalized_value']
        assert tuple(parameter['quantity']['original_span']) == tuple(quantity['original_span'])
        start, end = quantity['original_span']
        assert prompt[start:end] == quantity['original']


@pytest.mark.parametrize('workflow', PARSERS)
@pytest.mark.parametrize('form', ['product', 'labeled'])
@pytest.mark.parametrize('first', ['1.2.3 m', '1e--2 m', '1×10^oops-2 m', '1*10^- m',
    '1×10^-2 furlong', '1e-2 mmBAD', '1e-2 m/s', '1e999 m', '-2 m', '0 m',
    '1×10^oops 2 m', '1e-- 2 m'])
def test_bad_geometry_never_uses_numeric_suffix(workflow, form, first):
    plan = PARSERS[workflow](prompt_for(workflow, first, form))
    assert not plan['runnable']
    assert plan['missing_information']
    assert ('width' if workflow == 'heat' else 'length') not in plan['parameters']


@pytest.mark.parametrize('literal', FORMATS)
def test_tds_labeled_and_heat_fidelity_parser(literal):
    dims = _tds_dimensions(f'L={literal} m; H=5 mm')
    assert dims is not None and dims[0] == pytest.approx(.01)
    facts = _extract_requested_facts(prompt_for('heat', literal + ' m'))
    assert facts['width'] == pytest.approx(.01)
    assert facts['height'] == pytest.approx(.005)


def test_exact_independent_audit_reproducer():
    text = '2D stationary primary current distribution 1×10^-2 m x 5 mm conductivity 1 S/m left 0 V right 1 V'
    plan = extract_electrochem_plan(text)
    assert plan['parameters']['length']['value'] == pytest.approx(.01)


@pytest.mark.parametrize('geometry', ['宽10 mm × 5 mm', '宽10 mm，高5 mm',
    '10 mm wide and 5 mm high', '10 mm wide by 5 mm high'])
def test_heat_legacy_geometry_preserves_native_units(geometry):
    plan = extract_heat_transfer_plan('二维稳态传热铝板 ' + geometry + ' left 373 K right 293 K insulated')
    assert plan['runnable']
    assert plan['parameters']['width']['value'] == 10
    assert plan['parameters']['width']['unit'] == 'mm'
    assert plan['parameters']['width']['normalized_value'] == .01
