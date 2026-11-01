"""A mixed corpus must preserve drafts without inventing executable physics."""

import asyncio
import json
from pathlib import Path

import pytest

from app.agent.planner import ScientificPlanner
from app.scientific.quantity import parse_quantity
from app.scientific.schemas import ScientificPlan
from app.scientific.solver_gate import solver_input_issues


CASES = json.loads(
    (Path(__file__).parent / "fixtures/modeling_pairs_25.json").read_text(
        encoding="utf-8"
    )
)


class Offline:
    async def complete(self, *args, **kwargs):
        return None


def draft(prompt):
    payload, _, _ = asyncio.run(ScientificPlanner(Offline()).plan(prompt, []))
    return ScientificPlan.model_validate(payload)


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["id"]))
@pytest.mark.parametrize("group", ["vague", "explicit"])
def test_corpus_has_traceable_drafts_without_unrelated_workflows(case, group):
    prompt = case[group]
    plan = draft(prompt)
    assert plan.physics and plan.geometry and plan.expected_outputs
    assert plan.workflow_id is None or plan.workflow_id in case["allowed_workflows"]
    assert all(
        prompt[slice(*fact.span)] == fact.original for fact in plan.modeling_brief.facts
    )
    if group == "vague":
        assert not plan.runnable
    if plan.runnable:
        assert not solver_input_issues(plan)


def test_natural_cooling_values_do_not_fall_back_to_different_proposals():
    plan = draft(CASES[0]["explicit"])
    for name, expected in {
        "duration": 120,
        "output_interval": 1,
        "emissivity": 0.8,
    }.items():
        assert plan.parameters[name].value == expected
        assert name not in plan.completion.parameters


def test_chinese_cosine_initial_profile_selects_transient_not_stationary():
    plan = draft(CASES[11]["explicit"])
    assert plan.workflow_id == "tds_2d_transient_closed" and plan.runnable
    assert plan.parameters["mean_concentration"].value == 2
    assert plan.parameters["concentration_amplitude"].value == 0.4
    assert not solver_input_issues(plan)


def test_engine_preference_blocks_both_planning_and_direct_execution():
    prompt = "使用 MATLAB 做二维稳态传热，铝板 20×10 mm，左侧 350 K，右侧 300 K，上下绝热，导热系数 200 W/(m*K)，密度 2700 kg/m^3，比热 900 J/(kg*K)"
    assert draft(prompt).workflow_id is None
    forged = draft(prompt.replace("MATLAB", "COMSOL"))
    forged.original_request = prompt
    assert any("Requested engine" in issue for issue in solver_input_issues(forged))


@pytest.mark.parametrize("raw", ["10", "10 bananas", "10 eV"])
def test_dimensionless_shorthand_does_not_relax_dimensional_units(raw):
    assert parse_quantity(raw, "length").issue


def test_engine_names_next_to_chinese_and_negated_alternatives():
    from app.scientific.engine_intent import requested_engines

    assert requested_engines("使用MATLAB求解") == ["MATLAB"]
    assert requested_engines("不用MATLAB，使用COMSOL求解") == ["COMSOL"]
    assert requested_engines("时间步长10 ms") == []


def test_controlled_formula_allows_numeric_evaluation_but_still_rejects_io():
    import importlib.util

    path = Path(__file__).parents[1] / "mathics_worker.py"
    spec = importlib.util.spec_from_file_location("numeric_formula_policy", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    class Expression:
        elements = ()

        def __init__(self, head):
            self.head = head

        def get_head_name(self):
            return self.head

    worker.validate_formula(Expression("System`N"))
    for head in ("System`Get", "System`Run", "System`Set"):
        with pytest.raises(ValueError):
            worker.validate_formula(Expression(head))
