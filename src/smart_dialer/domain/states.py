from dataclasses import dataclass
from enum import StrEnum


class InvalidTransition(ValueError):
    """Raised when an event attempts a transition outside the explicit table."""


class AgentState(StrEnum):
    OFFLINE = "offline"
    AVAILABLE = "available"
    RESERVED = "reserved"
    DIALING = "dialing"
    CONNECTED = "connected"
    WRAP_UP = "wrap_up"
    PAUSED = "paused"


class CallState(StrEnum):
    QUEUED = "queued"
    RESERVED = "reserved"
    INITIATED = "initiated"
    RINGING = "ringing"
    ANSWERED = "answered"
    CONNECTED = "connected"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AMBIGUOUS = "ambiguous"


TERMINAL_CALL_STATES = frozenset(
    {CallState.COMPLETED, CallState.FAILED, CallState.CANCELLED, CallState.AMBIGUOUS}
)

AGENT_TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.OFFLINE: frozenset({AgentState.AVAILABLE, AgentState.PAUSED}),
    AgentState.AVAILABLE: frozenset(
        {AgentState.RESERVED, AgentState.PAUSED, AgentState.OFFLINE}
    ),
    AgentState.RESERVED: frozenset(
        {AgentState.DIALING, AgentState.AVAILABLE, AgentState.PAUSED, AgentState.OFFLINE}
    ),
    AgentState.DIALING: frozenset(
        {AgentState.CONNECTED, AgentState.AVAILABLE, AgentState.PAUSED, AgentState.OFFLINE}
    ),
    AgentState.CONNECTED: frozenset({AgentState.WRAP_UP, AgentState.OFFLINE}),
    AgentState.WRAP_UP: frozenset(
        {AgentState.AVAILABLE, AgentState.PAUSED, AgentState.OFFLINE}
    ),
    AgentState.PAUSED: frozenset({AgentState.AVAILABLE, AgentState.OFFLINE}),
}

# Forward jumps are deliberately enumerated. Provider events cannot advance a call
# merely because the destination appears later in the lifecycle.
CALL_TRANSITIONS: dict[CallState, frozenset[CallState]] = {
    CallState.QUEUED: frozenset(
        {CallState.RESERVED, CallState.CANCELLED, CallState.FAILED, CallState.AMBIGUOUS}
    ),
    CallState.RESERVED: frozenset(
        {CallState.INITIATED, CallState.CANCELLED, CallState.FAILED, CallState.AMBIGUOUS}
    ),
    CallState.INITIATED: frozenset(
        {
            CallState.RINGING,
            CallState.ANSWERED,
            CallState.CONNECTED,
            CallState.COMPLETED,
            CallState.FAILED,
            CallState.CANCELLED,
            CallState.AMBIGUOUS,
        }
    ),
    CallState.RINGING: frozenset(
        {
            CallState.ANSWERED,
            CallState.CONNECTED,
            CallState.COMPLETED,
            CallState.FAILED,
            CallState.CANCELLED,
            CallState.AMBIGUOUS,
        }
    ),
    CallState.ANSWERED: frozenset(
        {CallState.CONNECTED, CallState.COMPLETED, CallState.FAILED, CallState.AMBIGUOUS}
    ),
    CallState.CONNECTED: frozenset({CallState.COMPLETED, CallState.FAILED, CallState.AMBIGUOUS}),
    CallState.COMPLETED: frozenset(),
    CallState.FAILED: frozenset(),
    CallState.CANCELLED: frozenset(),
    CallState.AMBIGUOUS: frozenset(),
}


@dataclass(frozen=True)
class CallTransition:
    previous: CallState
    current: CallState
    answer_observation: str | None = None


def transition_agent(current: AgentState, target: AgentState) -> AgentState:
    if target not in AGENT_TRANSITIONS[current]:
        raise InvalidTransition(f"agent transition {current.value} -> {target.value} is not allowed")
    return target


def transition_call(current: CallState, target: CallState) -> CallTransition:
    if target not in CALL_TRANSITIONS[current]:
        raise InvalidTransition(f"call transition {current.value} -> {target.value} is not allowed")

    answer_observation: str | None = None
    if target is CallState.ANSWERED:
        answer_observation = "observed"
    elif target in {CallState.CONNECTED, CallState.COMPLETED}:
        answer_observation = (
            "observed"
            if current in {CallState.ANSWERED, CallState.CONNECTED}
            else "inferred"
        )
    return CallTransition(current, target, answer_observation)
