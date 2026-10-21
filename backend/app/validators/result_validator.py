"""Numerical range, boundary and analytical checks."""

from __future__ import annotations

from typing import Any

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import ValidationCheck


def validate_results(plan: ScientificPlan, validation: dict[str, Any]) -> list[ValidationCheck]:
    left = float(plan.parameters["left_temperature"].value)
    right = float(plan.parameters["right_temperature"].value)
    checks: list[ValidationCheck] = []
    specifications = [
        ("left_boundary_temperature", validation.get("left_boundary_median_K"), left, 1.0),
        ("right_boundary_temperature", validation.get("right_boundary_median_K"), right, 1.0),
    ]
    for name, observed, expected, tolerance in specifications:
        passed = isinstance(observed, (int, float)) and abs(float(observed) - expected) <= tolerance
        checks.append(ValidationCheck(category="Result", name=name, status="passed" if passed else "failed", message=f"{name} is within {tolerance:g} K" if passed else f"{name} is outside tolerance", observed=observed, expected=expected))
    t_min = validation.get("temperature_min_K")
    t_max = validation.get("temperature_max_K")
    low, high = sorted((left, right))
    range_ok = isinstance(t_min, (int, float)) and isinstance(t_max, (int, float)) and float(t_min) >= low - 1.0 and float(t_max) <= high + 1.0
    checks.append(ValidationCheck(category="Result", name="temperature_range", status="passed" if range_ok else "failed", message="Temperature remains inside imposed bounds" if range_ok else "Temperature exceeds imposed bounds", observed=[t_min, t_max], expected=[low, high]))
    rmse = validation.get("analytical_rmse_K")
    analytical = isinstance(rmse, (int, float)) and float(rmse) <= 1.0
    checks.append(ValidationCheck(category="Result", name="analytical_solution", status="passed" if analytical else "failed", message="Numerical solution agrees with the 1D analytical solution" if analytical else "Analytical error exceeds 1 K RMSE", observed=rmse, expected="<= 1 K RMSE"))
    return checks
