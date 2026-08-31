import pytest

from smart_dialer.domain.states import (
    AgentState,
    CallState,
    InvalidTransition,
    transition_agent,
    transition_call,
)


def test_call_forward_jump_marks_answer_as_inferred() -> None:
    result = transition_call(CallState.INITIATED, CallState.COMPLETED)

    assert result.current is CallState.COMPLETED
    assert result.answer_observation == "inferred"


def test_terminal_call_state_is_absorbing() -> None:
    with pytest.raises(InvalidTransition):
        transition_call(CallState.COMPLETED, CallState.ANSWERED)


def test_call_transition_table_rejects_unlisted_forward_jump() -> None:
    with pytest.raises(InvalidTransition):
        transition_call(CallState.QUEUED, CallState.CONNECTED)


def test_agent_can_be_reserved_only_from_available() -> None:
    assert transition_agent(AgentState.AVAILABLE, AgentState.RESERVED) is AgentState.RESERVED

    with pytest.raises(InvalidTransition):
        transition_agent(AgentState.PAUSED, AgentState.RESERVED)
