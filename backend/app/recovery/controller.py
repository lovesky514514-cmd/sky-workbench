"""Safety-first deterministic action selection; never a learned router."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecoverabilityAction(StrEnum):
    CONTINUE = "CONTINUE"
    RETRY_SAME_MODEL = "RETRY_SAME_MODEL"
    APPLY_SKILL = "APPLY_SKILL"
    RECALL_MEMORY = "RECALL_MEMORY"
    ESCALATE_MODEL = "ESCALATE_MODEL"
    ASK_HUMAN = "ASK_HUMAN"
    PAUSE = "PAUSE"
    ROLLBACK = "ROLLBACK"
    NONMODEL_ESCALATION = "NONMODEL_ESCALATION"
    CERTIFY = "CERTIFY"


@dataclass(frozen=True, slots=True)
class ControllerInput:
    plan_node_id: str = ""
    safety_blocked: bool = False
    cancellation_requested: bool = False
    domain_sensitive_ambiguity: bool = False
    all_certification_gates_passed: bool = False
    missing_required_evidence: bool = False
    evidence_acquisition_available: bool = False
    failure_class: str = ""
    attempt_count: int = 0
    max_attempts: int = 0
    applicable_skill_ids: tuple[str, ...] = ()
    validated_memory_ids: tuple[str, ...] = ()
    larger_model_allowed: bool = False
    rollback_available: bool = False


@dataclass(frozen=True, slots=True)
class ControllerDecision:
    action: RecoverabilityAction
    priority: str
    reason: str


class RecoverabilityActionController:
    """Priority is fixed: Safety > evidence acquisition > recovery > cost."""

    def decide(self, state: ControllerInput) -> ControllerDecision:
        if state.safety_blocked or state.cancellation_requested:
            return ControllerDecision(RecoverabilityAction.PAUSE, "SAFETY", "safety_or_cancellation_gate")
        if state.domain_sensitive_ambiguity:
            return ControllerDecision(RecoverabilityAction.ASK_HUMAN, "SAFETY", "domain_sensitive_ambiguity")
        if state.all_certification_gates_passed:
            return ControllerDecision(RecoverabilityAction.CERTIFY, "EVIDENCE", "all_certification_gates_passed")
        if state.missing_required_evidence:
            if state.evidence_acquisition_available:
                return ControllerDecision(RecoverabilityAction.CONTINUE, "EVIDENCE", "acquire_missing_required_evidence")
            return ControllerDecision(RecoverabilityAction.NONMODEL_ESCALATION, "EVIDENCE", "required_evidence_unavailable")
        if state.failure_class == "retryable" and state.attempt_count < state.max_attempts:
            return ControllerDecision(RecoverabilityAction.RETRY_SAME_MODEL, "RECOVERY", "bounded_retry_available")
        if state.failure_class == "repairable" and state.applicable_skill_ids:
            return ControllerDecision(RecoverabilityAction.APPLY_SKILL, "RECOVERY", "validated_skill_available")
        if state.failure_class == "repairable" and state.validated_memory_ids:
            return ControllerDecision(RecoverabilityAction.RECALL_MEMORY, "RECOVERY", "validated_memory_available")
        if state.failure_class == "state_corruption" and state.rollback_available:
            return ControllerDecision(RecoverabilityAction.ROLLBACK, "RECOVERY", "verified_rollback_available")
        if state.failure_class == "capacity" and state.larger_model_allowed:
            return ControllerDecision(RecoverabilityAction.ESCALATE_MODEL, "COST", "capacity_escalation_allowed_not_assumed_safer")
        return ControllerDecision(RecoverabilityAction.NONMODEL_ESCALATION, "COST", "no_safe_automatic_action")
