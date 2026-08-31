from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from smart_dialer.db.models import Agent, Borrower, CallIntent, Campaign, SafetyDecision
from smart_dialer.domain.states import AgentState
from smart_dialer.services.coordinator import run_pacing_tick

pytestmark = pytest.mark.integration


def seed(session_factory, *, mode: str, risk: float, agents: int, borrowers: int) -> str:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        campaign = Campaign(
            name=f"{mode}-{risk}", mode=mode, risk_tolerance=risk,
            language="en-IN", provider_name="plivo_mock",
        )
        session.add(campaign)
        session.flush()
        for index in range(agents):
            session.add(Agent(
                campaign_id=campaign.id, name=f"Agent {index}", language="en-IN",
                state=AgentState.AVAILABLE, last_heartbeat_at=now, available_since=now,
            ))
        for index in range(borrowers):
            session.add(Borrower(
                campaign_id=campaign.id, external_id=f"b-{index}",
                phone=f"+91910000{index:04d}", language="en-IN",
            ))
        return campaign.id


def test_pacing_tick_persists_receipt_and_cannot_create_more_than_approved(session_factory) -> None:
    campaign_id = seed(session_factory, mode="predictive", risk=0.005, agents=10, borrowers=100)
    with session_factory.begin() as session:
        result = run_pacing_tick(
            session, campaign_id=campaign_id, worker_id="api", now=datetime.now(UTC),
            observed_answers=30, observed_attempts=100,
        )
        assert result.created_intents == result.receipt.approved_calls
    with session_factory() as session:
        assert session.scalar(select(func.count(CallIntent.id))) == result.receipt.approved_calls
        assert session.scalar(select(func.count(SafetyDecision.id))) == 1


def test_zero_risk_predictive_campaign_allocates_exactly_like_progressive(session_factory) -> None:
    campaign_id = seed(session_factory, mode="predictive", risk=0.0, agents=7, borrowers=30)
    with session_factory.begin() as session:
        result = run_pacing_tick(
            session, campaign_id=campaign_id, worker_id="api", now=datetime.now(UTC),
            observed_answers=30, observed_attempts=100,
        )
    assert result.receipt.effective_mode == "progressive"
    assert result.created_intents == 7
    with session_factory() as session:
        assert all(intent.agent_id is not None for intent in session.scalars(select(CallIntent)))


def test_degraded_provider_forces_progressive_allocation(session_factory) -> None:
    campaign_id = seed(session_factory, mode="predictive", risk=0.005, agents=5, borrowers=30)
    with session_factory.begin() as session:
        result = run_pacing_tick(
            session, campaign_id=campaign_id, worker_id="api", now=datetime.now(UTC),
            observed_answers=30, observed_attempts=100, provider_healthy=False,
        )
    assert result.receipt.effective_mode == "progressive"
    assert result.created_intents == 5
