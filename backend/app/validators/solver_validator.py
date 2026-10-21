"""Solver and study-tree checks."""

from __future__ import annotations

from typing import Any

from app.validators.base_validator import ValidationCheck


def validate_solver(model: dict[str, Any]) -> list[ValidationCheck]:
    study = model.get("study", [])
    solver = model.get("solver", [])
    stationary = any("stationary" in str(item).lower() for item in study)
    converged = any("converged" in str(item).lower() for item in solver)
    return [
        ValidationCheck(category="Study", name="stationary_study", status="passed" if stationary else "failed", message="Stationary study is present" if stationary else "Stationary study was not confirmed", observed=study, expected="Stationary"),
        ValidationCheck(category="Solver", name="solver_convergence", status="passed" if converged else "failed", message="Solver convergence is recorded" if converged else "Solver convergence was not confirmed", observed=solver, expected="converged"),
    ]
