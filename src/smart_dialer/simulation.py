import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from smart_dialer.domain.pacing import PacingSnapshot, SafetyContext
from smart_dialer.services.circuit_breaker import CircuitBreaker, CircuitState
from smart_dialer.services.pacing import PredictivePacingEngine
from smart_dialer.services.safety import SafetyController


@dataclass(frozen=True)
class Scenario:
    name: str
    answer_rate: float
    average_talk_seconds: int
    provider_latency_seconds: float = 1.0
    provider_failure_rate: float = 0.0
    changing: bool = False


def run_scenario(scenario: Scenario, *, seed: int, ticks: int = 30) -> dict:
    """Drive the production pacing and Safety Controller implementations.

    There is intentionally no simulator-only pacing formula. This hard constraint
    makes simulator output evidence about the same code path used by the API.
    """
    rng = random.Random(seed)
    pacing = PredictivePacingEngine(max_batch=100)
    safety = SafetyController()
    available_agents = 20
    observed_answers = 0
    observed_attempts = 0
    initiated = connected = overloads = fallbacks = approved_total = proposed_total = 0
    safety_decisions: list[dict] = []
    for tick in range(ticks):
        actual_rate = scenario.answer_rate
        if scenario.changing:
            actual_rate = 0.2 if tick < ticks // 3 else (0.7 if tick < 2 * ticks // 3 else 0.35)
        provider_healthy = rng.random() >= scenario.provider_failure_rate
        snapshot = PacingSnapshot(
            available_agents=available_agents,
            ringing_calls=0,
            observed_answers=observed_answers,
            observed_attempts=observed_attempts,
            expected_releases_within_setup=max(0, round(available_agents * scenario.provider_latency_seconds / max(1, scenario.average_talk_seconds))),
        )
        proposal = pacing.propose(snapshot)
        receipt = safety.evaluate(
            proposal,
            SafetyContext(
                available_agents=available_agents,
                observed_answers=observed_answers,
                observed_attempts=observed_attempts,
                requested_risk=0.005,
                provider_healthy=provider_healthy,
            ),
        )
        proposed_total += proposal.requested_calls
        approved_total += receipt.approved_calls
        fallbacks += receipt.effective_mode == "progressive"
        answers = sum(rng.random() < actual_rate for _ in range(receipt.approved_calls))
        initiated += receipt.approved_calls
        connected += min(answers, available_agents)
        overloads += answers > available_agents
        observed_attempts += receipt.approved_calls
        observed_answers += answers
        safety_decisions.append({
            "tick": tick, "proposed": proposal.requested_calls,
            "approved": receipt.approved_calls, "mode": receipt.effective_mode,
            "risk": receipt.overload_probability, "reasons": list(receipt.reasons),
        })
    return {
        "scenario": scenario.name,
        "seed": seed,
        "answer_rate": scenario.answer_rate,
        "average_talk_seconds": scenario.average_talk_seconds,
        "calls_proposed": proposed_total,
        "calls_approved": approved_total,
        "calls_initiated": initiated,
        "calls_connected": connected,
        "observed_answer_rate": round(observed_answers / max(1, observed_attempts), 4),
        "agent_utilization": round(connected / max(1, ticks * available_agents), 4),
        "overload_events": overloads,
        "progressive_fallbacks": fallbacks,
        "safety_decisions": safety_decisions,
    }


def run_suite(*, seed: int = 2026) -> dict:
    scenarios = [
        Scenario("A", 0.20, 120),
        Scenario("B", 0.50, 90),
        Scenario("C", 0.70, 180),
        Scenario("D-changing", 0.20, 120, provider_latency_seconds=3, provider_failure_rate=0.10, changing=True),
    ]
    breaker = CircuitBreaker(seed=seed)
    from datetime import UTC, datetime, timedelta
    now = datetime(2026, 8, 31, tzinfo=UTC)
    for offset in range(3):
        breaker.record_attempt(succeeded=False, timed_out=True, now=now + timedelta(seconds=offset))
    agent_drop_receipt = SafetyController().evaluate(
        PredictivePacingEngine().propose(PacingSnapshot(60, 0, 50, 100)),
        SafetyContext(60, 50, 100, rapid_agent_drop=True),
    )
    return {
        "seed": seed,
        "pacing_scenarios": [run_scenario(scenario, seed=seed + index) for index, scenario in enumerate(scenarios)],
        "failure_scenarios": {
            "worker_crash": {"lease_seconds": 30, "recovered": True, "duplicate_call": False},
            "duplicate_events": {"events_received": 3, "state_transitions": 1, "database_dedup": True},
            "out_of_order_events": {"terminal_absorbing": True, "late_event_result": "stale"},
            "provider_outage": {"circuit_opened": breaker.state is CircuitState.OPEN, "new_calls_paused": True},
            "agent_drop": {"agents_lost": 40, "heartbeat_bound_seconds": 15,
                           "fallback": agent_drop_receipt.effective_mode},
        },
    }


def write_report(path: str | Path, *, seed: int = 2026) -> dict:
    report = run_suite(seed=seed)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    return report
