from dataclasses import dataclass


@dataclass(frozen=True)
class PacingProposal:
    requested_calls: int
    explanation: str


@dataclass(frozen=True)
class PacingSnapshot:
    available_agents: int
    ringing_calls: int
    observed_answers: int
    observed_attempts: int
    expected_releases_within_setup: int = 0
    average_setup_seconds: float | None = None
    average_talk_seconds: float | None = None


@dataclass(frozen=True)
class SafetyContext:
    available_agents: int
    observed_answers: int
    observed_attempts: int
    requested_risk: float = 0.005
    provider_healthy: bool = True
    agent_data_stale: bool = False
    rapid_agent_drop: bool = False


@dataclass(frozen=True)
class SafetyReceipt:
    requested_calls: int
    approved_calls: int
    decision: str
    effective_mode: str
    effective_risk: float
    answer_rate_upper_bound: float
    overload_probability: float
    reasons: tuple[str, ...]
    explanation: str
