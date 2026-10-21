"""Non-executing restricted PTC policy prototype for Round 0."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True, slots=True)
class PTCExecutionReport:
    program_hash: str
    policy_version: str
    internal_calls: list[dict[str, Any]]
    policy_violations: list[str]
    rollback_count: int
    closed_loop_required: bool
    completed: bool
    final_observation_refs: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RestrictedPTC:
    policy_version = "restricted-comsol-ptc-v0.2"
    allowed = {"set_param", "add_primitive_geometry", "build_geometry", "assign_material_verified_selection",
               "create_mesh", "create_study", "evaluate_result_readonly"}
    high_risk = {"topology_boolean", "selection_rebuild", "assign_boundary", "multiphysics_coupling"}

    def dry_run(self, program: list[dict[str, Any]]) -> PTCExecutionReport:
        encoded = json.dumps(program, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        violations: list[str] = []
        calls: list[dict[str, Any]] = []
        closed_loop = False
        for index, call in enumerate(program):
            operation = str(call.get("op", ""))
            calls.append({"index": index, "op": operation, "executed": False, "mode": "dry_run"})
            if operation in self.high_risk:
                closed_loop = True
                violations.extend(("closed_loop_required", f"closed_loop_required:{operation}"))
            elif operation not in self.allowed:
                violations.append(f"operation_not_whitelisted:{operation or 'missing'}")
        return PTCExecutionReport(hashlib.sha256(encoded).hexdigest(), self.policy_version, calls, violations, 0,
                                  closed_loop, not violations, [])
