"""Distinct transient physics and its input/approval boundaries."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from app.agent.planner import ScientificPlanner
from app.scientific.schemas import ScientificPlan
from app.scientific.solver_gate import require_solver_inputs
from app.scientific.transient_diffusion import transient_diffusion_plan
from app.validators.physics.transient_diffusion_validator import (
    TransientDiffusionValidator,
)
from test_general_modeling import Offline

PROMPT = "二维瞬态扩散 10 mm x 5 mm，扩散系数 1e-8 m^2/s，平均浓度 1 mol/m^3，振幅 0.5 mol/m^3，初态 cos(pi*x/L)，全部边界无通量，无源项无反应，时长 1000 s，输出间隔 50 s"


def test_complete_transient_input_selects_its_own_equation_and_study():
    payload, _, _ = asyncio.run(ScientificPlanner(Offline()).plan(PROMPT, []))
    plan = ScientificPlan.model_validate(payload)
    assert plan.workflow_id == "tds_2d_transient_closed"
    assert plan.study == ["Time Dependent"] and plan.completion is None
    assert TransientDiffusionValidator().precheck(plan).passed
    require_solver_inputs(plan)


@pytest.mark.parametrize("replacement", ["cos(2*pi*x/L)", "sin(pi*x/L)"])
def test_other_initial_profiles_cannot_be_silently_replaced(replacement):
    assert transient_diffusion_plan(PROMPT.replace("cos(pi*x/L)", replacement)) is None


def test_invalid_initial_range_and_units_block_the_new_executor():
    plan = ScientificPlan.model_validate(transient_diffusion_plan(PROMPT))
    plan.parameters["concentration_amplitude"].value = 2
    assert not TransientDiffusionValidator().precheck(plan).passed
    plan.parameters["diffusion_coefficient"].unit = "eV"
    with pytest.raises(ValueError):
        require_solver_inputs(plan)


def test_additional_coupling_cannot_be_ignored_by_the_diffusion_executor():
    plan = ScientificPlan.model_validate(transient_diffusion_plan(PROMPT))
    plan.variables = {"reaction_rate": "1"}
    with pytest.raises(ValueError):
        require_solver_inputs(plan)


def test_formula_ast_rejects_host_operations_before_evaluation():
    path = Path(__file__).parents[1] / "mathics_worker.py"
    spec = importlib.util.spec_from_file_location("reviewed_mathics_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Expression:
        elements = ()

        def __init__(self, head):
            self.head = head

        def get_head_name(self):
            return self.head

    for head in (
        "System`Run",
        "System`Get",
        "System`Import",
        "System`ToExpression",
        "System`Set",
        "Global`fake",
    ):
        with pytest.raises(ValueError):
            module.validate_formula(Expression(head))
