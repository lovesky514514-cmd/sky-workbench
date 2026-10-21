from __future__ import annotations

import pytest

from app.agent.planner import extract_tds_plan, planning_schema
from app.scientific.schemas import ParameterStatus, ScientificPlan
from app.validators import plan_quality_validator, validator_registry


ENGLISH = (
    "Phase 3C benchmark: Create a 2D stationary Transport of Diluted Species pure diffusion model, "
    "1 mm x 0.2 mm, D=1e-9 m^2/s, left boundary 1000 mol/m^3, right boundary 100 mol/m^3, "
    "top and bottom no flux."
)
CHINESE = (
    "Phase 3C 基准：建立一个二维稳态稀物质扩散模型，尺寸 1 mm × 0.2 mm，左侧浓度 "
    "1000 mol/m³，右侧浓度 100 mol/m³，扩散系数 D=1e-9 m²/s，上下边界无通量。"
)


def plan_for(prompt: str = ENGLISH) -> ScientificPlan:
    payload = extract_tds_plan(prompt)
    assert payload is not None
    return ScientificPlan.model_validate(payload)


@pytest.mark.parametrize("prompt", [ENGLISH, CHINESE])
def test_tds_benchmark_plan_is_bilingual_and_value_driven(prompt: str) -> None:
    plan = plan_for(prompt)
    assert plan.workflow_id == "tds_2d_stationary_diffusion"
    assert plan.parameters["length"].value == pytest.approx(1e-3)
    assert plan.parameters["height"].value == pytest.approx(2e-4)
    assert plan.parameters["diffusion_coefficient"].value == pytest.approx(1e-9)
    assert plan.parameters["left_concentration"].value == pytest.approx(1000)
    assert plan.parameters["right_concentration"].value == pytest.approx(100)
    assert all(value.status == ParameterStatus.BENCHMARK_DEFINED for value in plan.parameters.values())
    assert plan.runnable
    assert validator_registry.get(plan.workflow_id).precheck(plan).passed
    assert plan_quality_validator.validate(plan).status != "INVALID"


def test_planning_schema_copies_values_but_does_not_insert_a_fixed_benchmark() -> None:
    first = plan_for(ENGLISH)
    changed = plan_for(ENGLISH.replace("1000 mol/m^3", "800 mol/m^3").replace("100 mol/m^3", "50 mol/m^3"))
    first_schema = planning_schema(first.model_dump(mode="json"))
    changed_schema = planning_schema(changed.model_dump(mode="json"))
    first_parameters = first_schema["properties"]["parameters"]["properties"]
    changed_parameters = changed_schema["properties"]["parameters"]["properties"]
    assert first_parameters["left_concentration"]["properties"]["value"]["const"] == 1000
    assert changed_parameters["left_concentration"]["properties"]["value"]["const"] == 800
    assert changed_parameters["right_concentration"]["properties"]["value"]["const"] == 50


def test_missing_parameters_remain_unresolved_and_not_runnable() -> None:
    plan = plan_for("模拟一种溶质在二维材料中的稳态扩散。")
    assert not plan.runnable
    assert "diffusion_coefficient" not in plan.parameters
    assert any("Diffusion coefficient" in item for item in plan.missing_information)
    assert not validator_registry.get(plan.workflow_id).precheck(plan).passed


def test_negative_diffusion_is_rejected_by_precheck() -> None:
    plan = plan_for(CHINESE.replace("D=1e-9", "D=-1e-9"))
    report = validator_registry.get(plan.workflow_id).precheck(plan)
    assert not report.passed
    assert any(check.name == "positive_diffusion" and check.status == "failed" for check in report.checks)


def valid_execution() -> dict:
    build_names = (
        "interface_exists", "interface_type", "domain_selection", "diffusion_feature_type", "diffusion_mode",
        "diffusion_expression", "zero_velocity", "left_bc_type", "left_bc_selection", "left_bc_species",
        "left_bc_expression", "right_bc_type", "right_bc_selection", "right_bc_species", "right_bc_expression",
        "no_flux_type", "no_flux_selection", "stationary_study", "parameter_readback",
    )
    scientific_names = (
        "solver_completed", "nonzero_field", "left_boundary_concentration", "right_boundary_concentration",
        "physical_range", "analytical_rmse", "analytical_mae", "analytical_max_abs_error", "monotonicity",
        "y_uniformity", "flux_sign", "flux_relative_error", "mass_balance",
    )
    return {
        "success": True,
        "command_success": True,
        "scientific_validation_success": True,
        "build_readback": {"checks": {name: True for name in build_names}},
        "validation": {
            "checks": {name: True for name in scientific_names},
            "concentration_min_mol_per_m3": 100.0,
            "concentration_max_mol_per_m3": 1000.0,
            "left_boundary_median_mol_per_m3": 1000.0,
            "right_boundary_median_mol_per_m3": 100.0,
            "analytical_rmse_mol_per_m3": 1e-10,
            "analytical_mae_mol_per_m3": 1e-10,
            "analytical_max_abs_error_mol_per_m3": 1e-9,
            "monotonic_violations": 0,
            "maximum_y_spread_mol_per_m3": 1e-5,
            "flux_mean_mol_per_m2_s": 9e-4,
            "mass_balance_relative_error": 1e-12,
        },
    }


def test_execution_validator_independently_checks_analytical_flux_and_mass_metrics() -> None:
    plan = plan_for()
    validator = validator_registry.get(plan.workflow_id)
    report = validator.validate_execution(plan, valid_execution())
    assert report.passed
    assert report.model_state_verified
    assert report.scientific_validation_success

    corrupted = valid_execution()
    corrupted["validation"]["flux_mean_mol_per_m2_s"] = -9e-4
    corrupted["validation"]["mass_balance_relative_error"] = 1e-2
    bad = validator.validate_execution(plan, corrupted)
    assert not bad.passed
    assert any(check.name == "flux_sign" and check.status == "failed" for check in bad.checks)
    assert any(check.name == "mass_balance" and check.status == "failed" for check in bad.checks)


def test_model_readback_rejects_silent_boundary_selection_failure() -> None:
    plan = plan_for()
    execution = valid_execution()
    execution["build_readback"]["checks"]["left_bc_selection"] = False
    report = validator_registry.get(plan.workflow_id).validate_execution(plan, execution)
    assert not report.passed
    assert not report.model_state_verified
    assert any(check.name == "left_bc_selection" and check.status == "failed" for check in report.checks)
