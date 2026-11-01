"""Parameter value, unit and provenance checks."""

from __future__ import annotations

from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import ValidationCheck
from app.scientific.static_contracts import HEAT_REQUIRED, HEAT_OPTIONAL


REQUIRED_HEAT_PARAMETERS = HEAT_REQUIRED


def validate_parameters(plan: ScientificPlan) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    for name in (*REQUIRED_HEAT_PARAMETERS, *(name for name in HEAT_OPTIONAL if name in plan.parameters)):
        parameter = plan.parameters.get(name)
        present = bool(parameter and parameter.unit and parameter.source)
        checks.append(
            ValidationCheck(
                category="Parameter",
                name=f"{name}_defined",
                status="passed" if present else "failed",
                message=f"{name} has value, unit and provenance" if present else f"{name} lacks value, unit or provenance",
                observed=parameter.model_dump(mode="json") if parameter else None,
                expected="value + unit + source",
            )
        )
        if parameter and parameter.assumption:
            checks.append(
                ValidationCheck(
                    category="Parameter",
                    name=f"{name}_assumption",
                    status="warning",
                    message=f"{name} is explicitly marked as an assumption",
                    observed=parameter.source,
                    expected="Traceable literature or verified user value before publication",
                )
            )
    return checks
