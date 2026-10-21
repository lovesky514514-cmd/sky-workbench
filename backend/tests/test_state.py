import pytest

from app.agent.state import InvalidStateTransition, assert_transition
from app.models import AgentState


def test_scientific_state_machine_accepts_verified_run_path():
    path = [
        AgentState.IDLE,
        AgentState.RESEARCHING,
        AgentState.PLANNING,
        AgentState.WAITING_FOR_EXECUTION,
        AgentState.BUILDING,
        AgentState.SOLVING,
        AgentState.VALIDATING,
        AgentState.EXPORTING,
        AgentState.COMPLETED,
    ]
    for current, target in zip(path, path[1:]):
        assert_transition(current, target)


def test_scientific_state_machine_rejects_natural_language_shortcut():
    with pytest.raises(InvalidStateTransition):
        assert_transition(AgentState.IDLE, AgentState.COMPLETED)

