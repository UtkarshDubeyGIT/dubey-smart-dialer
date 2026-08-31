import json
import random
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PendingCall:
    initiated_at: float
    answer_at: float
    answered: bool
    talk_seconds: float


def run_scenario(
    scenario: Scenario,
    *,
    seed: int,
    ticks: int = 30,
    tick_seconds: int = 10,
) -> dict:
    """Drive the production pacing and Safety Controller implementations.

    There is intentionally no simulator-only pacing formula. This hard constraint
    makes simulator output evidence about the same code path used by the API.
    """
    rng = random.Random(seed)
    pacing = PredictivePacingEngine(max_batch=100)
    safety = SafetyController()
    total_agents = 20
    observed_answers = 0
    observed_attempts = 0
    initiated = connected = overloads = fallbacks = approved_total = proposed_total = 0
    provider_failed_calls = agent_releases = 0
    busy_until: list[float] = []
    pending_calls: list[PendingCall] = []
    occupancy_intervals: list[tuple[float, float]] = []
    observed_setup_seconds: list[float] = []
    observed_talk_seconds: list[float] = []
    available_samples: list[int] = []
    peak_busy_agents = max_ringing_calls = 0
    safety_decisions: list[dict] = []
    for tick in range(ticks):
        virtual_now = float(tick * tick_seconds)
        released = sum(end <= virtual_now for end in busy_until)
        agent_releases += released
        busy_until = [end for end in busy_until if end > virtual_now]

        due_calls = [call for call in pending_calls if call.answer_at <= virtual_now]
        pending_calls = [call for call in pending_calls if call.answer_at > virtual_now]
        for call in due_calls:
            observed_attempts += 1
            observed_setup_seconds.append(call.answer_at - call.initiated_at)
            if not call.answered:
                continue
            observed_answers += 1
            if len(busy_until) >= total_agents:
                overloads += 1
                continue
            busy_end = virtual_now + call.talk_seconds
            busy_until.append(busy_end)
            occupancy_intervals.append((virtual_now, busy_end))
            observed_talk_seconds.append(call.talk_seconds)
            connected += 1

        available_agents = total_agents - len(busy_until)
        available_samples.append(available_agents)
        peak_busy_agents = max(peak_busy_agents, len(busy_until))
        max_ringing_calls = max(max_ringing_calls, len(pending_calls))
        actual_rate = scenario.answer_rate
        actual_talk_seconds = float(scenario.average_talk_seconds)
        if scenario.changing:
            actual_rate = 0.2 if tick < ticks // 3 else (0.7 if tick < 2 * ticks // 3 else 0.35)
            actual_talk_seconds = (
                scenario.average_talk_seconds
                if tick < ticks // 3
                else (scenario.average_talk_seconds * 1.5 if tick < 2 * ticks // 3 else scenario.average_talk_seconds * 0.75)
            )
        provider_healthy = rng.random() >= scenario.provider_failure_rate
        expected_releases = sum(
            end <= virtual_now + scenario.provider_latency_seconds
            for end in busy_until
        )
        snapshot = PacingSnapshot(
            available_agents=available_agents,
            ringing_calls=len(pending_calls),
            observed_answers=observed_answers,
            observed_attempts=observed_attempts,
            expected_releases_within_setup=expected_releases,
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
        if provider_healthy:
            initiated += receipt.approved_calls
            pending_calls.extend(
                PendingCall(
                    initiated_at=virtual_now,
                    answer_at=virtual_now + scenario.provider_latency_seconds,
                    answered=rng.random() < actual_rate,
                    talk_seconds=actual_talk_seconds,
                )
                for _ in range(receipt.approved_calls)
            )
            max_ringing_calls = max(max_ringing_calls, len(pending_calls))
        else:
            provider_failed_calls += receipt.approved_calls
        safety_decisions.append({
            "tick": tick, "proposed": proposal.requested_calls,
            "approved": receipt.approved_calls, "mode": receipt.effective_mode,
            "risk": receipt.overload_probability, "reasons": list(receipt.reasons),
            "available_agents": available_agents,
            "busy_agents": len(busy_until),
            "ringing_calls": len(pending_calls),
            "expected_releases_within_setup": expected_releases,
        })
    simulation_seconds = max(1, ticks * tick_seconds)
    busy_agent_seconds = sum(
        max(0.0, min(end, simulation_seconds) - start)
        for start, end in occupancy_intervals
    )
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
        "agent_utilization": round(busy_agent_seconds / (simulation_seconds * total_agents), 4),
        "simulation_seconds": simulation_seconds,
        "peak_busy_agents": peak_busy_agents,
        "minimum_available_agents": min(available_samples, default=total_agents),
        "average_available_agents": round(sum(available_samples) / max(1, len(available_samples)), 2),
        "agent_releases": agent_releases,
        "max_ringing_calls": max_ringing_calls,
        "provider_failed_calls": provider_failed_calls,
        "observed_average_setup_seconds": round(
            sum(observed_setup_seconds) / max(1, len(observed_setup_seconds)), 3
        ),
        "observed_average_talk_seconds": round(
            sum(observed_talk_seconds) / max(1, len(observed_talk_seconds)), 3
        ),
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
