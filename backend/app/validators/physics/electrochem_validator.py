"""Validation gates for the primary-current Electrochemistry benchmark."""

from __future__ import annotations

import math
from typing import Any

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import BaseValidator, ValidationCheck, ValidationReport
from app.validators.execution_verdict import required_validation_checks


REQUIRED = ("length", "height", "electrolyte_conductivity", "left_potential", "right_potential")


def check(category: str, name: str, passed: bool, observed: Any, expected: Any) -> ValidationCheck:
    return ValidationCheck(category=category, name=name, status="passed" if passed else "failed", message=f"Electrochemistry check: {name}", observed=observed, expected=expected)


def numeric(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


class ElectrochemistryValidator(BaseValidator):
    def precheck(self, plan: ScientificPlan) -> ValidationReport:
        missing = [name for name in REQUIRED if name not in plan.parameters]
        length = numeric(plan.parameters["length"].value) if "length" in plan.parameters else None
        height = numeric(plan.parameters["height"].value) if "height" in plan.parameters else None
        conductivity = numeric(plan.parameters["electrolyte_conductivity"].value) if "electrolyte_conductivity" in plan.parameters else None
        checks = [
            check("Physics", "workflow", plan.workflow_id == "electrochem_primary_current", plan.workflow_id, "electrochem_primary_current"),
            check("Physics", "interface", any("primary current" in item.lower() or "一次电流" in item for item in plan.physics), plan.physics, "Primary Current Distribution"),
            check("Parameters", "required", not missing, missing, []),
            check("Geometry", "positive", length is not None and height is not None and length > 0 and height > 0, {"length": length, "height": height}, "> 0 m"),
            check("Material", "conductivity", conductivity is not None and conductivity > 0, conductivity, "> 0 S/m"),
            check("Boundary", "potentials", sum("potential" in item.lower() or "电势" in item for item in plan.boundary_conditions) >= 2, plan.boundary_conditions, "two electrolyte potentials"),
            check("Study", "stationary", any("stationary" in item.lower() or "稳态" in item for item in plan.study), plan.study, "Stationary"),
            check("Completeness", "runnable", not plan.missing_information, plan.missing_information, []),
        ]
        return ValidationReport.from_checks("precheck", checks)

    def validate_execution(self, plan: ScientificPlan, execution: dict[str, Any]) -> ValidationReport:
        build = execution.get("build_readback", {}).get("checks", {})
        scientific = execution.get("validation", {}).get("checks", {})
        checks = required_validation_checks('electrochem_primary_current', execution)
        conductivity = float(plan.parameters["electrolyte_conductivity"].value)
        voltage = abs(float(plan.parameters["right_potential"].value) - float(plan.parameters["left_potential"].value))
        expected_current = conductivity * voltage / float(plan.parameters["length"].value)
        observed = execution.get("validation", {}).get("current_density_mean_A_per_m2")
        relative = math.inf if numeric(observed) is None else abs(float(observed) - expected_current) / max(expected_current, 1e-30)
        checks.append(check("Independent", "ohms_law", relative <= 1e-4, {"current": observed, "relative_error": relative}, expected_current))
        report = ValidationReport.from_checks("solution", checks, command_success=execution.get("command_success") is True, model_state_verified=bool(build) and all(value is True for value in build.values()))
        report.scientific_validation_success = bool(report.passed and execution.get("scientific_validation_success") is True)
        return report
