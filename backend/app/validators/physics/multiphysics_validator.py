"""Independent gates for the real one-way Heat → TDS benchmark."""

from __future__ import annotations

import math
from typing import Any

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import BaseValidator, ValidationCheck, ValidationReport
from app.validators.execution_verdict import required_validation_checks


REQUIRED = ("length", "height", "diffusion_coefficient", "reference_temperature", "left_temperature", "right_temperature", "left_concentration", "right_concentration")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _check(category: str, name: str, passed: bool, observed: Any, expected: Any) -> ValidationCheck:
    return ValidationCheck(category=category, name=name, status="passed" if passed else "failed", message=f"Heat → TDS coupling check: {name}", observed=observed, expected=expected)


class MultiphysicsValidator(BaseValidator):
    def precheck(self, plan: ScientificPlan) -> ValidationReport:
        missing = [name for name in REQUIRED if name not in plan.parameters]
        positive = not missing and all(_number(plan.parameters[name].value) is not None and float(plan.parameters[name].value) > 0 for name in REQUIRED[:6])
        coupling = any(edge.source == "Heat Transfer" and edge.target == "Transport of Diluted Species" and "T" in edge.variables for edge in plan.couplings)
        checks = [
            _check("Physics", "workflow", plan.workflow_id == "heat_tds_temperature_coupling", plan.workflow_id, "heat_tds_temperature_coupling"),
            _check("Physics", "interfaces", any("heat" in value.lower() for value in plan.physics) and any("diluted" in value.lower() for value in plan.physics), plan.physics, "Heat Transfer + TDS"),
            _check("Parameters", "required", not missing, missing, []),
            _check("Parameters", "positive", positive, {name: plan.parameters[name].value for name in REQUIRED if name in plan.parameters}, "positive geometry, D and temperature"),
            _check("Coupling", "edge", coupling, [edge.model_dump(mode="json") for edge in plan.couplings], "Heat Transfer -> TDS using T"),
            _check("Study", "stationary", any("stationary" in value.lower() or "稳态" in value for value in plan.study), plan.study, "Stationary OFF/ON"),
            _check("Completeness", "runnable", not plan.missing_information, plan.missing_information, []),
        ]
        return ValidationReport.from_checks("precheck", checks)

    def validate_execution(self, plan: ScientificPlan, execution: dict[str, Any]) -> ValidationReport:
        build = execution.get("build_readback", {}).get("checks", {})
        scientific = execution.get("validation", {}).get("checks", {})
        validation = execution.get("validation", {})
        checks = required_validation_checks('heat_tds_temperature_coupling', execution)
        on_error = _number(validation.get("on_flux_relative_error")); balance = _number(validation.get("mass_balance_relative_error"))
        difference = _number(validation.get("off_on_concentration_rmse_mol_per_m3"))
        checks.extend([
            _check("Independent", "on_flux_error", on_error is not None and on_error <= 2e-4, on_error, "<= 2e-4"),
            _check("Independent", "mass_balance", balance is not None and balance <= 1e-6, balance, "<= 1e-6"),
            _check("Independent", "off_on_difference", difference is not None and difference > 0, difference, "> 0"),
        ])
        report = ValidationReport.from_checks("solution", checks, command_success=execution.get("command_success") is True, model_state_verified=bool(build) and all(value is True for value in build.values()))
        report.scientific_validation_success = bool(report.passed and execution.get("scientific_validation_success") is True and execution.get("coupling_validation_success") is True)
        return report
