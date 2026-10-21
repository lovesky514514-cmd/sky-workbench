"""Base validation result contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.scientific.schemas import ScientificPlan


class ValidationCheck(BaseModel):
    category: str
    name: str
    status: Literal["passed", "failed", "warning"]
    message: str
    observed: Any = None
    expected: Any = None


class ValidationReport(BaseModel):
    scope: Literal["precheck", "build", "solution"]
    passed: bool
    command_success: bool
    model_state_verified: bool
    scientific_validation_success: bool
    checks: list[ValidationCheck] = Field(default_factory=list)

    @classmethod
    def from_checks(
        cls,
        scope: Literal["precheck", "build", "solution"],
        checks: list[ValidationCheck],
        *,
        command_success: bool = False,
        model_state_verified: bool = False,
    ) -> "ValidationReport":
        passed = all(check.status != "failed" for check in checks)
        return cls(
            scope=scope,
            passed=passed,
            command_success=command_success,
            model_state_verified=model_state_verified,
            scientific_validation_success=passed and command_success and model_state_verified,
            checks=checks,
        )


class BaseValidator(ABC):
    @abstractmethod
    def precheck(self, plan: ScientificPlan) -> ValidationReport: ...

    @abstractmethod
    def validate_execution(self, plan: ScientificPlan, execution: dict[str, Any]) -> ValidationReport: ...
