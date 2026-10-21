"""Round 0 model-routing seam. Learned routing is deliberately unavailable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelRoute:
    policy: str
    requested_model: str | None
    selected_model: str | None
    reason_tags: tuple[str, ...]
    advisory: str | None = None


class ModelRouter:
    allowed_policies = {"fixed_model", "explicit_escalation", "rule_router_stub"}

    def route(self, policy: str, state: dict[str, Any]) -> ModelRoute:
        if policy not in self.allowed_policies:
            if "learned" in policy:
                raise ValueError("learned model routing is forbidden in Round 0")
            raise ValueError(f"unknown model routing policy: {policy}")
        requested = state.get("requested_model")
        if policy == "fixed_model":
            return ModelRoute(policy, requested, requested, ("fixed_model",))
        if policy == "explicit_escalation":
            target = state.get("escalate_to")
            reason = state.get("reason")
            if not target or not reason:
                raise ValueError("explicit escalation requires target and reason")
            return ModelRoute(policy, requested, str(target), (str(reason),))
        advisory = "escalation_suggested_not_applied" if str(state.get("risk", "")).lower() in {"high", "critical"} else None
        reasons = ("high_risk",) if advisory else ("no_rule_triggered",)
        return ModelRoute(policy, requested, requested, reasons, advisory)
