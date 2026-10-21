"""Boundary-condition prechecks."""

from __future__ import annotations

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import ValidationCheck


def validate_boundaries(plan: ScientificPlan) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    for name in ("left_temperature", "right_temperature"):
        parameter = plan.parameters.get(name)
        valid = bool(parameter and isinstance(parameter.value, (int, float)) and float(parameter.value) > 0 and parameter.unit == "K")
        checks.append(
            ValidationCheck(
                category="Boundary",
                name=name,
                status="passed" if valid else "failed",
                message=f"{name} is a positive absolute temperature" if valid else f"{name} must be supplied in kelvin",
                observed=f"{parameter.value} {parameter.unit}" if parameter else None,
                expected="positive K",
            )
        )
    insulation = any("insulat" in item.lower() or "绝热" in item for item in plan.boundary_conditions)
    checks.append(
        ValidationCheck(
            category="Boundary",
            name="remaining_boundaries",
            status="passed" if insulation else "warning",
            message="Remaining boundaries are declared thermally insulated" if insulation else "Remaining-boundary behavior is not explicit",
            observed=plan.boundary_conditions,
            expected="Top and bottom thermal insulation",
        )
    )
    return checks
