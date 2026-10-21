"""Independent gates for the real uniaxial Solid Mechanics benchmark."""

from __future__ import annotations

import math
from typing import Any

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import BaseValidator, ValidationCheck, ValidationReport


REQUIRED = ("length", "width", "height", "youngs_modulus", "poissons_ratio", "density", "traction")


def check(category: str, name: str, passed: bool, message: str, observed: Any, expected: Any) -> ValidationCheck:
    return ValidationCheck(category=category, name=name, status="passed" if passed else "failed", message=message, observed=observed, expected=expected)


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def value(plan: ScientificPlan, name: str) -> float | None:
    item = plan.parameters.get(name)
    return number(item.value) if item else None


class SolidMechanicsValidator(BaseValidator):
    def precheck(self, plan: ScientificPlan) -> ValidationReport:
        missing = [name for name in REQUIRED if name not in plan.parameters]
        dimensions = [value(plan, name) for name in ("length", "width", "height")]
        young, poisson, density, traction = (value(plan, name) for name in ("youngs_modulus", "poissons_ratio", "density", "traction"))
        checks = [
            check("Physics", "workflow", plan.workflow_id == "solid_mechanics_uniaxial_bar", "Verified Solid Mechanics workflow is selected", plan.workflow_id, "solid_mechanics_uniaxial_bar"),
            check("Physics", "interface", any("solid mechanics" in item.lower() or "固体力学" in item for item in plan.physics), "Solid Mechanics is explicit", plan.physics, "Solid Mechanics"),
            check("Parameters", "required", not missing, "All benchmark parameters are present", missing, list(REQUIRED)),
            check("Geometry", "positive_dimensions", all(item is not None and item > 0 for item in dimensions), "Bar dimensions are positive", dimensions, "> 0 m"),
            check("Units", "dimensions", all(plan.parameters[name].unit == "m" for name in ("length", "width", "height") if name in plan.parameters), "Geometry is normalized to metres", [plan.parameters[name].unit for name in ("length", "width", "height") if name in plan.parameters], "m"),
            check("Material", "youngs_modulus", young is not None and young > 0 and "youngs_modulus" in plan.parameters and plan.parameters["youngs_modulus"].unit == "Pa", "Young's modulus is positive and in Pa", young, "> 0 Pa"),
            check("Material", "poissons_ratio", poisson is not None and 0 <= poisson < 0.5, "Poisson's ratio is in the stable isotropic range", poisson, "0 <= nu < 0.5"),
            check("Material", "density", density is not None and density > 0, "Density is positive", density, "> 0 kg/m^3"),
            check("Load", "traction", traction is not None and traction > 0 and "traction" in plan.parameters and plan.parameters["traction"].unit == "Pa", "Axial traction is positive and in Pa", traction, "> 0 Pa"),
            check("Boundary", "fixed_and_loaded", any("fixed" in item.lower() or "固定" in item for item in plan.boundary_conditions) and any("traction" in item.lower() or "载荷" in item for item in plan.boundary_conditions), "Fixed and loaded faces are explicit", plan.boundary_conditions, "left fixed; right traction"),
            check("Study", "stationary", any("stationary" in item.lower() or "稳态" in item for item in plan.study), "Stationary study is explicit", plan.study, "Stationary"),
            check("Mesh", "strategy", bool(plan.mesh_strategy), "Mesh strategy is explicit", plan.mesh_strategy, "physics-controlled tetrahedral"),
            check("Completeness", "runnable", not plan.missing_information, "No blocking information is missing", plan.missing_information, []),
        ]
        return ValidationReport.from_checks("precheck", checks)

    def validate_execution(self, plan: ScientificPlan, execution: dict[str, Any]) -> ValidationReport:
        build = execution.get("build_readback", {}).get("checks", {})
        scientific = execution.get("validation", {}).get("checks", {})
        checks: list[ValidationCheck] = []
        for name in ("solid_interface", "domain_nonempty", "elastic_model", "youngs_modulus", "poissons_ratio", "density", "fixed_face", "loaded_face", "traction", "stationary_study", "parameter_readback"):
            checks.append(check("Model Readback", name, build.get(name) is True, f"Real COMSOL readback: {name}", build.get(name), True))
        for name in ("solver_completed", "finite_fields", "positive_displacement", "displacement_analytical", "axial_stress_analytical", "von_mises_analytical", "force_equilibrium"):
            checks.append(check("Scientific Result", name, scientific.get(name) is True, f"Independent benchmark check: {name}", scientific.get(name), True))

        validation = execution.get("validation", {})
        expected_u = float(plan.parameters["traction"].value) * float(plan.parameters["length"].value) / float(plan.parameters["youngs_modulus"].value)
        independent = [
            check("Independent", "displacement", _relative(validation.get("right_displacement_m"), expected_u) <= 0.05, "Displacement agrees with sigma L / E", validation.get("right_displacement_m"), expected_u),
            check("Independent", "stress", _relative(validation.get("axial_stress_Pa"), float(plan.parameters["traction"].value)) <= 0.05, "Axial stress agrees with applied traction", validation.get("axial_stress_Pa"), float(plan.parameters["traction"].value)),
            check("Independent", "force", _relative(validation.get("internal_force_N"), float(plan.parameters["traction"].value) * float(plan.parameters["width"].value) * float(plan.parameters["height"].value)) <= 0.05, "Internal force agrees with traction times area", validation.get("internal_force_N"), float(plan.parameters["traction"].value) * float(plan.parameters["width"].value) * float(plan.parameters["height"].value)),
        ]
        checks.extend(independent)
        report = ValidationReport.from_checks("solution", checks, command_success=execution.get("command_success") is True, model_state_verified=bool(build) and all(value is True for value in build.values()))
        report.scientific_validation_success = bool(report.passed and execution.get("scientific_validation_success") is True)
        return report


def _relative(observed: Any, expected: float) -> float:
    numeric = number(observed)
    return math.inf if numeric is None else abs(numeric - expected) / max(abs(expected), 1e-30)
