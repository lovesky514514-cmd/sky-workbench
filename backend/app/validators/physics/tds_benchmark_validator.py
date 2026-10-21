"""Scientific gates for the real 2D stationary TDS diffusion workflow."""

from __future__ import annotations

import math
from typing import Any

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import BaseValidator, ValidationCheck, ValidationReport


REQUIRED_PARAMETERS = (
    "length",
    "height",
    "diffusion_coefficient",
    "left_concentration",
    "right_concentration",
)


def check(category: str, name: str, passed: bool, message: str, observed: Any, expected: Any) -> ValidationCheck:
    return ValidationCheck(
        category=category,
        name=name,
        status="passed" if passed else "failed",
        message=message,
        observed=observed,
        expected=expected,
    )


def numeric(plan: ScientificPlan, name: str) -> float | None:
    value = plan.parameters.get(name)
    if value is None or isinstance(value.value, bool):
        return None
    try:
        return float(value.value)
    except (TypeError, ValueError):
        return None


class TDSBenchmarkValidator(BaseValidator):
    def precheck(self, plan: ScientificPlan) -> ValidationReport:
        checks: list[ValidationCheck] = []
        checks.append(check("Physics", "workflow", plan.workflow_id == "tds_2d_stationary_diffusion", "TDS stationary diffusion workflow selected", plan.workflow_id, "tds_2d_stationary_diffusion"))
        checks.append(check("Physics", "interface", any("diluted species" in item.lower() or "稀物质" in item for item in plan.physics), "Transport of Diluted Species is explicit", plan.physics, "Transport of Diluted Species"))
        missing = [name for name in REQUIRED_PARAMETERS if name not in plan.parameters]
        checks.append(check("Parameters", "required_parameters", not missing, "All required TDS parameters are present", missing, list(REQUIRED_PARAMETERS)))

        length = numeric(plan, "length")
        height = numeric(plan, "height")
        diffusion = numeric(plan, "diffusion_coefficient")
        left = numeric(plan, "left_concentration")
        right = numeric(plan, "right_concentration")
        checks.append(check("Geometry", "positive_dimensions", length is not None and height is not None and length > 0 and height > 0, "Geometry dimensions are positive", {"length": length, "height": height}, "> 0 m"))
        geometry_units = {plan.parameters[name].unit for name in ("length", "height") if name in plan.parameters}
        checks.append(check("Units", "geometry_units", geometry_units == {"m"}, "Executable geometry is normalized to metres", sorted(geometry_units), ["m"]))
        checks.append(check("Parameters", "positive_diffusion", diffusion is not None and diffusion > 0, "Diffusion coefficient is positive", diffusion, "> 0"))
        diffusion_unit = plan.parameters.get("diffusion_coefficient").unit if "diffusion_coefficient" in plan.parameters else None
        checks.append(check("Units", "diffusion_unit", diffusion_unit in {"m^2/s", "m²/s"}, "Diffusion coefficient unit is valid", diffusion_unit, "m^2/s"))
        concentration_units = {plan.parameters[name].unit for name in ("left_concentration", "right_concentration") if name in plan.parameters}
        checks.append(check("Units", "concentration_units", bool(concentration_units) and concentration_units.issubset({"mol/m^3", "mol/m³"}), "Boundary concentration units are valid", sorted(concentration_units), "mol/m^3"))
        checks.append(check("Boundary", "left_concentration", left is not None and math_finite(left), "Left concentration is finite", left, "finite mol/m^3"))
        checks.append(check("Boundary", "right_concentration", right is not None and math_finite(right), "Right concentration is finite", right, "finite mol/m^3"))
        no_flux = any("no flux" in item.lower() or "无通量" in item for item in plan.boundary_conditions)
        checks.append(check("Boundary", "top_bottom_no_flux", no_flux, "Top and bottom No Flux behavior is explicit", plan.boundary_conditions, "Top/bottom No Flux"))
        stationary = any("stationary" in item.lower() or "steady" in item.lower() or "稳态" in item for item in plan.study)
        checks.append(check("Study", "stationary", stationary, "Stationary study is explicit", plan.study, "Stationary"))
        checks.append(check("Mesh", "mesh_strategy", bool(plan.mesh_strategy), "A stable mesh strategy is defined", plan.mesh_strategy, "physics-controlled 2D mesh"))
        pure_diffusion = not any(term in " ".join(plan.physics + plan.assumptions).lower() for term in ("migration enabled", "electric field coupling", "reaction enabled", "convection enabled"))
        checks.append(check("Physics", "pure_diffusion_scope", pure_diffusion, "Plan remains inside the pure-diffusion scope", plan.physics + plan.assumptions, "no convection/migration/reaction"))
        checks.append(check("Completeness", "runnable", not plan.missing_information, "No blocking input is missing", plan.missing_information, []))
        return ValidationReport.from_checks("precheck", checks)

    def validate_execution(self, plan: ScientificPlan, execution: dict[str, Any]) -> ValidationReport:
        readback = execution.get("build_readback", {})
        build = readback.get("checks", {})
        validation = execution.get("validation", {})
        scientific = validation.get("checks", {})
        checks: list[ValidationCheck] = []
        build_names = {
            "interface_exists": "TDS interface exists",
            "interface_type": "TDS interface type is DilutedSpecies",
            "domain_selection": "TDS domain selection is non-empty",
            "diffusion_feature_type": "Diffusion domain feature is the verified Fluid type",
            "diffusion_mode": "Diffusion coefficient is user-defined",
            "diffusion_expression": "Diffusion expression readback matches D",
            "zero_velocity": "Velocity readback is zero",
            "left_bc_type": "Left boundary feature type is Concentration",
            "left_bc_selection": "Left boundary selection matches coordinate-derived edge",
            "left_bc_species": "Left Concentration applies to species c",
            "left_bc_expression": "Left concentration expression readback matches c_left",
            "right_bc_type": "Right boundary feature type is Concentration",
            "right_bc_selection": "Right boundary selection matches coordinate-derived edge",
            "right_bc_species": "Right Concentration applies to species c",
            "right_bc_expression": "Right concentration expression readback matches c_right",
            "no_flux_type": "Default boundary feature type is NoFlux",
            "no_flux_selection": "Default No Flux resolves to top and bottom only",
            "stationary_study": "Stationary study exists before solve",
            "parameter_readback": "COMSOL parameter expressions were read back",
        }
        for name, message in build_names.items():
            passed = build.get(name) is True
            checks.append(check("Model Readback", name, passed, message, build.get(name), True))

        scientific_names = {
            "solver_completed": "Real COMSOL stationary solver completed",
            "nonzero_field": "Concentration field is nonzero",
            "left_boundary_concentration": "Left boundary concentration is within tolerance",
            "right_boundary_concentration": "Right boundary concentration is within tolerance",
            "physical_range": "Concentration stays inside imposed bounds",
            "analytical_rmse": "Analytical concentration RMSE is within tolerance",
            "analytical_mae": "Analytical concentration MAE is within tolerance",
            "analytical_max_abs_error": "Maximum analytical error is within tolerance",
            "monotonicity": "Concentration is monotone along x",
            "y_uniformity": "Cross-y concentration deviation is within tolerance",
            "flux_sign": "COMSOL diffusive-flux direction matches the concentration gradient",
            "flux_relative_error": "COMSOL diffusive flux matches the analytical value",
            "mass_balance": "Integrated inflow and outflow balance",
        }
        left = float(plan.parameters["left_concentration"].value)
        right = float(plan.parameters["right_concentration"].value)
        diffusion = float(plan.parameters["diffusion_coefficient"].value)
        length = float(plan.parameters["length"].value)
        solution_tolerance = max(abs(left - right) * 1e-5, 1e-6)
        boundary_tolerance = max(abs(left - right) * 1e-6, 1e-7)
        expected_flux = -diffusion * (right - left) / length
        observed_flux = validation.get("flux_mean_mol_per_m2_s")
        derived = {
            "solver_completed": execution.get("success") is True,
            "nonzero_field": number(validation.get("concentration_max_mol_per_m3")) is not None and float(validation["concentration_max_mol_per_m3"]) > 0,
            "left_boundary_concentration": close(validation.get("left_boundary_median_mol_per_m3"), left, boundary_tolerance),
            "right_boundary_concentration": close(validation.get("right_boundary_median_mol_per_m3"), right, boundary_tolerance),
            "physical_range": range_valid(validation, min(left, right), max(left, right), solution_tolerance),
            "analytical_rmse": at_most(validation.get("analytical_rmse_mol_per_m3"), solution_tolerance),
            "analytical_mae": at_most(validation.get("analytical_mae_mol_per_m3"), solution_tolerance),
            "analytical_max_abs_error": at_most(validation.get("analytical_max_abs_error_mol_per_m3"), solution_tolerance * 10),
            "monotonicity": number(validation.get("monotonic_violations")) == 0,
            "y_uniformity": at_most(validation.get("maximum_y_spread_mol_per_m3"), solution_tolerance),
            "flux_sign": number(observed_flux) is not None and float(observed_flux) * expected_flux > 0,
            "flux_relative_error": relative_error(observed_flux, expected_flux) <= 1e-4,
            "mass_balance": at_most(validation.get("mass_balance_relative_error"), 1e-6),
        }
        for name, message in scientific_names.items():
            passed = scientific.get(name) is True and derived.get(name) is True
            checks.append(check("Scientific Result", name, passed, message, {"worker_check": scientific.get(name), "independent_check": derived.get(name)}, True))

        command_success = execution.get("command_success") is True
        model_state_verified = bool(build) and all(build.get(name) is True for name in build_names)
        report = ValidationReport.from_checks(
            "solution",
            checks,
            command_success=command_success,
            model_state_verified=model_state_verified,
        )
        report.scientific_validation_success = bool(
            report.passed and execution.get("scientific_validation_success") is True
        )
        return report


def math_finite(value: float) -> bool:
    return math.isfinite(value)


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def close(observed: Any, expected: float, tolerance: float) -> bool:
    value = number(observed)
    return value is not None and abs(value - expected) <= tolerance


def at_most(observed: Any, maximum: float) -> bool:
    value = number(observed)
    return value is not None and 0 <= value <= maximum


def relative_error(observed: Any, expected: float) -> float:
    value = number(observed)
    return math.inf if value is None else abs(value - expected) / max(abs(expected), 1e-30)


def range_valid(validation: dict[str, Any], low: float, high: float, tolerance: float) -> bool:
    observed_low = number(validation.get("concentration_min_mol_per_m3"))
    observed_high = number(validation.get("concentration_max_mol_per_m3"))
    return observed_low is not None and observed_high is not None and observed_low >= low - tolerance and observed_high <= high + tolerance
