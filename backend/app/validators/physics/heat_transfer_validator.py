"""Validated contract for the 2D stationary heat-transfer benchmark."""

from __future__ import annotations

from typing import Any

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import BaseValidator, ValidationCheck, ValidationReport
from app.validators.boundary_validator import validate_boundaries
from app.validators.geometry_validator import validate_geometry
from app.validators.parameter_validator import validate_parameters
from app.validators.result_validator import validate_results
from app.validators.solver_validator import validate_solver


class HeatTransferValidator(BaseValidator):
    def precheck(self, plan: ScientificPlan) -> ValidationReport:
        checks = [
            ValidationCheck(
                category="Physics",
                name="workflow",
                status="passed" if plan.workflow_id == "heat_transfer_2d_stationary" else "failed",
                message="Verified heat-transfer workflow selected" if plan.workflow_id == "heat_transfer_2d_stationary" else "Unsupported workflow",
                observed=plan.workflow_id,
                expected="heat_transfer_2d_stationary",
            ),
            *validate_geometry(plan),
            *validate_parameters(plan),
            *validate_boundaries(plan),
        ]
        return ValidationReport.from_checks("precheck", checks)

    def validate_execution(self, plan: ScientificPlan, execution: dict[str, Any]) -> ValidationReport:
        model = execution.get("model_state", {})
        command_success = bool(execution.get("command_success", execution.get("success", True)))
        tree_checks = [
            ("geometry_tree", "Geometry", model.get("geometry", []), "rectangle"),
            ("material_tree", "Material", model.get("materials", []), "aluminum"),
            ("physics_tree", "Physics", model.get("physics", []), "heat transfer"),
            ("boundary_tree", "Boundary", model.get("boundary_conditions", []), "left:"),
            ("mesh_tree", "Mesh", model.get("mesh", []), "mesh"),
        ]
        checks: list[ValidationCheck] = []
        for name, category, observed, token in tree_checks:
            present = any(token in str(item).lower() for item in observed)
            checks.append(ValidationCheck(category=category, name=name, status="passed" if present else "failed", message=f"{category} state confirmed" if present else f"{category} state missing", observed=observed, expected=token))
        checks.extend(validate_solver(model))
        checks.extend(validate_results(plan, execution.get("validation", {})))
        model_state_verified = all(check.status == "passed" for check in checks[: len(tree_checks)])
        return ValidationReport.from_checks(
            "solution",
            checks,
            command_success=command_success,
            model_state_verified=model_state_verified,
        )
