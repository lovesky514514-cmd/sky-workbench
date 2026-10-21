"""Explicit scientific workflow state machine."""

from __future__ import annotations

from app.models import AgentState


ALLOWED_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE: {AgentState.RESEARCHING, AgentState.PLANNING, AgentState.PLAN_READY, AgentState.VALIDATION_FAILED, AgentState.FAILED},
    AgentState.RESEARCHING: {AgentState.PLANNING, AgentState.FAILED},
    AgentState.PLANNING: {AgentState.PLAN_READY, AgentState.WAITING_FOR_EXECUTION, AgentState.FAILED},
    AgentState.PLAN_READY: {AgentState.PRECHECK, AgentState.RESEARCHING, AgentState.PLANNING, AgentState.FAILED},
    AgentState.PRECHECK: {AgentState.BUILDING, AgentState.VALIDATION_FAILED, AgentState.FAILED},
    AgentState.WAITING_FOR_EXECUTION: {
        AgentState.RESEARCHING,
        AgentState.PLAN_READY,
        AgentState.PRECHECK,
        AgentState.BUILDING,
        AgentState.FAILED,
    },
    AgentState.BUILDING: {AgentState.BUILD_VALIDATION, AgentState.SOLVING, AgentState.REPAIRING, AgentState.VALIDATION_FAILED, AgentState.PAUSED, AgentState.FAILED},
    AgentState.BUILD_VALIDATION: {AgentState.SOLVING, AgentState.VALIDATION_FAILED, AgentState.REPAIRING, AgentState.PAUSED, AgentState.FAILED},
    AgentState.SOLVING: {AgentState.SOLUTION_VALIDATION, AgentState.VALIDATING, AgentState.REPAIRING, AgentState.PAUSED, AgentState.FAILED},
    AgentState.SOLUTION_VALIDATION: {AgentState.EXPORTING, AgentState.VALIDATION_FAILED, AgentState.REPAIRING, AgentState.PAUSED, AgentState.FAILED},
    AgentState.VALIDATING: {
        AgentState.REPAIRING,
        AgentState.EXPORTING,
        AgentState.VALIDATION_FAILED,
        AgentState.PAUSED,
        AgentState.FAILED,
    },
    AgentState.VALIDATION_FAILED: {AgentState.REPAIRING, AgentState.PRECHECK, AgentState.RESEARCHING, AgentState.PLANNING, AgentState.PLAN_READY, AgentState.FAILED},
    AgentState.REPAIRING: {
        AgentState.REVALIDATING,
        AgentState.BUILDING,
        AgentState.SOLVING,
        AgentState.VALIDATING,
        AgentState.FAILED,
    },
    AgentState.REVALIDATING: {AgentState.BUILDING, AgentState.SOLVING, AgentState.SOLUTION_VALIDATION, AgentState.VALIDATION_FAILED, AgentState.FAILED},
    AgentState.EXPORTING: {AgentState.REPORTING, AgentState.COMPLETED, AgentState.REPAIRING, AgentState.VALIDATION_FAILED, AgentState.PAUSED, AgentState.FAILED},
    AgentState.REPORTING: {AgentState.COMPLETED, AgentState.PAUSED, AgentState.FAILED},
    AgentState.COMPLETED: {
        AgentState.RESEARCHING,
        AgentState.PLANNING,
        AgentState.PLAN_READY,
        AgentState.PRECHECK,
        AgentState.BUILDING,
    },
    AgentState.FAILED: {
        AgentState.RESEARCHING,
        AgentState.PLANNING,
        AgentState.PLAN_READY,
        AgentState.PRECHECK,
        AgentState.BUILDING,
    },
    AgentState.PAUSED: {
        AgentState.BUILDING,
        AgentState.CANCEL_REQUESTED,
        AgentState.CANCELED,
        AgentState.INTERRUPTED,
        AgentState.FAILED,
    },
}


for _terminal in (AgentState.CANCELED, AgentState.INTERRUPTED):
    ALLOWED_TRANSITIONS[_terminal] = set(ALLOWED_TRANSITIONS[AgentState.FAILED])
ALLOWED_TRANSITIONS[AgentState.CANCEL_REQUESTED] = {AgentState.CANCELED, AgentState.INTERRUPTED, AgentState.FAILED}
for _state in (AgentState.PRECHECK, AgentState.BUILDING, AgentState.BUILD_VALIDATION,
               AgentState.SOLVING, AgentState.SOLUTION_VALIDATION, AgentState.EXPORTING,
               AgentState.REPAIRING, AgentState.REVALIDATING, AgentState.REPORTING, AgentState.VALIDATING):
    ALLOWED_TRANSITIONS[_state].update({AgentState.CANCEL_REQUESTED, AgentState.CANCELED, AgentState.INTERRUPTED})


class InvalidStateTransition(RuntimeError):
    """Raised when code attempts a transition not present in the state graph."""


def assert_transition(current: AgentState, target: AgentState) -> None:
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStateTransition(f"Invalid agent transition: {current} -> {target}")
