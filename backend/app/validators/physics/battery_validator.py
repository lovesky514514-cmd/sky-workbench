"""Independent gates for the real minimal lithium-ion battery baseline."""

from __future__ import annotations

import math
from typing import Any

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import BaseValidator, ValidationCheck, ValidationReport
from app.validators.execution_verdict import required_validation_checks


REQUIRED = ("applied_current_density", "duration", "initial_electrolyte_concentration")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _check(category: str, name: str, passed: bool, observed: Any, expected: Any) -> ValidationCheck:
    return ValidationCheck(category=category, name=name, status="passed" if passed else "failed", message=f"Battery baseline check: {name}", observed=observed, expected=expected)


class BatteryValidator(BaseValidator):
    def precheck(self, plan: ScientificPlan) -> ValidationReport:
        missing = [name for name in REQUIRED if name not in plan.parameters]
        positive = all(_number(plan.parameters[name].value) is not None and float(plan.parameters[name].value) > 0 for name in REQUIRED if name in plan.parameters)
        checks = [
            _check("Physics", "workflow", plan.workflow_id == "lithium_ion_battery_baseline", plan.workflow_id, "lithium_ion_battery_baseline"),
            _check("Physics", "interface", any("battery" in value.lower() or "电池" in value for value in plan.physics), plan.physics, "Lithium-Ion Battery"),
            _check("Parameters", "required", not missing, missing, []),
            _check("Parameters", "positive", not missing and positive, {name: plan.parameters[name].value for name in REQUIRED if name in plan.parameters}, "> 0"),
            _check("Study", "time_dependent", any("time" in value.lower() or "瞬态" in value for value in plan.study), plan.study, "Time Dependent"),
            _check("Provenance", "battery_cell", plan.battery_cell is not None and bool(plan.battery_cell.parameters), plan.battery_cell.model_dump(mode="json") if plan.battery_cell else None, "explicit baseline provenance"),
            _check("Completeness", "runnable", not plan.missing_information, plan.missing_information, []),
        ]
        return ValidationReport.from_checks("precheck", checks)

    def validate_execution(self, plan: ScientificPlan, execution: dict[str, Any]) -> ValidationReport:
        build = execution.get("build_readback", {}).get("checks", {})
        scientific = execution.get("validation", {}).get("checks", {})
        validation = execution.get("validation", {})
        checks = required_validation_checks('lithium_ion_battery_baseline', execution)
        initial = _number(validation.get("voltage_initial_V")); final = _number(validation.get("voltage_final_V"))
        current_error = _number(validation.get("current_relative_error"))
        checks.extend([
            _check("Independent", "discharge_voltage_direction", initial is not None and final is not None and final < initial, {"initial": initial, "final": final}, "final < initial"),
            _check("Independent", "current_error", current_error is not None and current_error <= 0.02, current_error, "<= 0.02"),
        ])
        report = ValidationReport.from_checks("solution", checks, command_success=execution.get("command_success") is True, model_state_verified=bool(build) and all(value is True for value in build.values()))
        report.scientific_validation_success = bool(report.passed and execution.get("scientific_validation_success") is True)
        return report
