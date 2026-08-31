from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smart_dialer.db.models import Agent, CallIntent, Campaign, SafetyDecision
from smart_dialer.domain.pacing import PacingSnapshot, SafetyContext, SafetyReceipt
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.services.allocation import reserve_predictive_borrower, reserve_progressive_pair
from smart_dialer.services.campaign_statistics import load_answer_history
from smart_dialer.services.pacing import PredictivePacingEngine, ProgressivePacingEngine
from smart_dialer.services.provider_health import provider_is_healthy
from smart_dialer.services.safety import SafetyController


@dataclass(frozen=True)
class PacingTickResult:
    receipt: SafetyReceipt
    created_intents: int


def run_pacing_tick(
    session: Session,
    *,
    campaign_id: str,
    worker_id: str,
    now: datetime,
    observed_answers: int | None = None,
    observed_attempts: int | None = None,
    provider_healthy: bool | None = None,
    agent_data_stale: bool = False,
    rapid_agent_drop: bool = False,
) -> PacingTickResult:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise LookupError(campaign_id)
    if (observed_answers is None) != (observed_attempts is None):
        raise ValueError("answer history overrides require both answers and attempts")
    if observed_answers is None:
        history = load_answer_history(session, campaign_id=campaign_id)
        observed_answers = history.observed_answers
        observed_attempts = history.observed_attempts
        inferred_answers = history.inferred_answers
        statistics_source = "persisted_calls"
    else:
        inferred_answers = 0
        statistics_source = "explicit_override"
    if provider_healthy is None:
        provider_healthy = provider_is_healthy(
            session, provider_name=campaign.provider_name
        )
    available_agents = session.scalar(
        select(func.count(Agent.id)).where(
            Agent.campaign_id == campaign_id,
            Agent.state == AgentState.AVAILABLE,
            Agent.last_heartbeat_at >= now - timedelta(seconds=15),
        )
    ) or 0
    ringing = session.scalar(
        select(func.count(CallIntent.id)).where(
            CallIntent.campaign_id == campaign_id,
            CallIntent.state == CallState.RINGING,
        )
    ) or 0
    snapshot = PacingSnapshot(
        available_agents=available_agents,
        ringing_calls=ringing,
        observed_answers=observed_answers,
        observed_attempts=observed_attempts,
    )
    engine = PredictivePacingEngine() if campaign.mode == "predictive" else ProgressivePacingEngine()
    proposal = engine.propose(snapshot)
    receipt = SafetyController().evaluate(
        proposal,
        SafetyContext(
            available_agents=available_agents,
            observed_answers=observed_answers,
            observed_attempts=observed_attempts,
            requested_risk=campaign.risk_tolerance,
            provider_healthy=provider_healthy,
            agent_data_stale=agent_data_stale,
            rapid_agent_drop=rapid_agent_drop,
        ),
    )
    # This durable receipt is the authorization record. Allocation receives only
    # its approved count; pacing code has no provider/call-creation dependency.
    session.add(SafetyDecision(
        campaign_id=campaign_id,
        requested_calls=receipt.requested_calls,
        approved_calls=receipt.approved_calls,
        decision=receipt.decision,
        effective_mode=receipt.effective_mode,
        effective_risk=receipt.effective_risk,
        overload_probability=receipt.overload_probability,
        inputs={
            "available_agents": available_agents,
            "ringing_calls": ringing,
            "observed_answers": observed_answers,
            "observed_attempts": observed_attempts,
            "inferred_answers_excluded": inferred_answers,
            "statistics_source": statistics_source,
            "provider_healthy": provider_healthy,
            "answer_rate_upper_bound": receipt.answer_rate_upper_bound,
        },
        reasons=list(receipt.reasons),
    ))
    created = 0
    allocator = reserve_progressive_pair if receipt.effective_mode == "progressive" else reserve_predictive_borrower
    for index in range(receipt.approved_calls):
        intent = allocator(
            session,
            campaign_id=campaign_id,
            worker_id=f"{worker_id}:{index}",
            now=now,
        )
        if intent is None:
            break
        created += 1
    session.flush()
    return PacingTickResult(receipt=receipt, created_intents=created)
