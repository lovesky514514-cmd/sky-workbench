"""RC4.2 regressions from the independent RC4.1 product audit."""

from __future__ import annotations

import pytest

from app.agent.intent_compiler import compile_intent
from app.agent.planner import extract_heat_transfer_plan
from app.scientific.quantity import valid_geometry
from app.scientific.schemas import ScientificPlan
from app.validators.plan_quality_validator import plan_quality_validator


@pytest.mark.parametrize("separator", ["x", "×", "by", "*"])
def test_rc42_geometry_separators_share_normalization(separator: str) -> None:
    text = f"2D stationary heat transfer rectangle 0.1 m {separator} 5 cm, left 373 K right 293 K insulated"
    quantities = valid_geometry(text)
    assert quantities is not None
    assert [item.normalized_value for item in quantities] == pytest.approx([0.1, 0.05])
    intent = compile_intent(text)
    assert [item["normalized_value"] for item in intent.geometry_quantities] == pytest.approx([0.1, 0.05])


def test_rc42_explicit_conductivity_not_replaced_by_aluminum_benchmark() -> None:
    text = (
        "Create a 2D stationary heat transfer rectangle 0.1 m by 0.05 m. "
        "Use k=15 W/(m*K), density 2700 kg/m^3 and heat capacity 900 J/(kg*K). "
        "Set left boundary to 373 K, right boundary to 293 K; insulate the rest."
    )
    intent = compile_intent(text)
    assert intent.unknowns == []
    assert intent.parameters["thermal_conductivity"].value == 15
    plan = ScientificPlan.model_validate(extract_heat_transfer_plan(text))
    assert plan.parameters["thermal_conductivity"].value == 15
    assert plan.parameters["thermal_conductivity"].assumption is False
    assert any("insulat" in item.lower() for item in plan.boundary_conditions)
    assert plan_quality_validator.validate(plan).status != "INVALID"


def test_rc42_potential_before_side_label_is_preserved() -> None:
    text = (
        "Create a 2D stationary primary current distribution rectangle 10 mm by 5 mm "
        "with conductivity 1 S/m, 0 V on the left boundary and 1 V on the right boundary."
    )
    intent = compile_intent(text)
    assert "Right potential boundary" not in intent.unknowns
    assert "Left potential boundary" not in intent.unknowns
    assert len(intent.geometry_quantities) == 2


@pytest.mark.parametrize("word", ["insulated", "insulate", "adiabatic", "no-flux", "thermal insulation"])
def test_rc42_heat_insulation_semantics(word: str) -> None:
    text = f"2D stationary heat transfer 10 mm x 5 mm Aluminum left 373 K right 293 K {word} top/bottom"
    intent = compile_intent(text)
    assert any(item.condition == "Thermal Insulation" for item in intent.boundary_conditions)
    plan = ScientificPlan.model_validate(extract_heat_transfer_plan(text))
    assert any("insulat" in item.lower() for item in plan.boundary_conditions)
