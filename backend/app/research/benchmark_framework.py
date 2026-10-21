"""Non-certifying benchmark contracts for future 3D and multiphysics work.

This module deliberately describes evidence requirements without executing or
claiming support for a workflow.  Real scientific certification remains owned
by ExecutionVerdict and the deterministic validators.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BenchmarkStatus(StrEnum):
    FRAMEWORK_ONLY_UNVERIFIED = "FRAMEWORK_ONLY_UNVERIFIED"
    REAL_EVIDENCE_REQUIRED = "REAL_EVIDENCE_REQUIRED"


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    family: str
    workflow_id: str
    status: BenchmarkStatus
    geometry: str
    evidence_required: tuple[str, ...]


def validate_benchmark_case(case: BenchmarkCase) -> dict[str, object]:
    """Return a fail-closed planning result for an unimplemented benchmark.

    Framework cases are never promoted into executable workflows and cannot
    produce a scientific pass.  This is intentionally not a capability router.
    """

    required = tuple(dict.fromkeys(item.strip() for item in case.evidence_required if item.strip()))
    return {
        "case_id": case.case_id,
        "family": case.family,
        "workflow_id": case.workflow_id,
        "status": case.status.value,
        "geometry": case.geometry,
        "evidence_required": list(required),
        "execution_allowed": False,
        "command_success": False,
        "model_state_verified": False,
        "scientific_validation_passed": False,
        "evidence_contract_passed": False,
        "reason": "benchmark_framework_is_not_an_execution_capability",
    }
