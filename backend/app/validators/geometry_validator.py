"""Geometry consistency checks."""

from __future__ import annotations

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import ValidationCheck


def validate_geometry(plan: ScientificPlan) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    for name in ("width", "height"):
        parameter = plan.parameters.get(name)
        numeric = isinstance(parameter.value, (int, float)) if parameter else False
        positive = numeric and float(parameter.value) > 0
        checks.append(
            ValidationCheck(
                category="Geometry",
                name=f"{name}_positive",
                status="passed" if positive else "failed",
                message=f"{name} has an explicit positive dimension" if positive else f"{name} must be a positive number",
                observed=parameter.value if parameter else None,
                expected="> 0",
            )
        )
    units = {plan.parameters[name].unit for name in ("width", "height") if name in plan.parameters}
    checks.append(
        ValidationCheck(
            category="Geometry",
            name="geometry_units",
            status="passed" if len(units) == 1 and units.issubset({"mm", "cm", "m"}) else "failed",
            message="Geometry dimensions use one supported unit" if len(units) == 1 else "Geometry units are missing or inconsistent",
            observed=sorted(units),
            expected=["mm", "cm", "m"],
        )
    )
    return checks
