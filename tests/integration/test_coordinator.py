from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from smart_dialer.db.models import (
    Agent,
    Borrower,
    BorrowerState,
    CallIntent,
    Campaign,
    IntentMode,
    SafetyDecision,
)
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.services.coordinator import run_pacing_tick
from smart_dialer.services.campaign_statistics import load_answer_history

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


def test_pacing_automatically_uses_persisted_observed_answer_history(session_factory) -> None:
    campaign_id = seed(
        session_factory, mode="predictive", risk=0.005, agents=5, borrowers=30
    )
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        historical_borrower = Borrower(
            campaign_id=campaign_id,
            external_id="historical-borrower",
            phone="+919300000008",
            language="en-IN",
            state=BorrowerState.COMPLETED,
        )
        session.add(historical_borrower)
        session.flush()
        for index in range(40):
            session.add(CallIntent(
                campaign_id=campaign_id,
                borrower_id=historical_borrower.id,
                mode=IntentMode.PREDICTIVE,
                state=CallState.COMPLETED,
                provider_name="plivo_mock",
                provider_idempotency_key=f"history:{index}:plivo_mock",
                provider_call_id=f"history-call-{index}",
                answer_observation="observed" if index < 12 else None,
            ))
        session.flush()

        result = run_pacing_tick(
            session,
            campaign_id=campaign_id,
            worker_id="automatic-history-test",
            now=now,
        )
        decision = session.scalar(
            select(SafetyDecision)
            .where(SafetyDecision.campaign_id == campaign_id)
            .order_by(SafetyDecision.created_at.desc())
        )

    assert result.receipt.effective_mode == "predictive"
    assert decision.inputs["observed_answers"] == 12
    assert decision.inputs["observed_attempts"] == 40
    assert decision.inputs["statistics_source"] == "persisted_calls"


def test_inferred_answers_are_a_separate_non_statistical_bucket(session_factory) -> None:
    campaign_id = seed(
        session_factory, mode="predictive", risk=0.005, agents=0, borrowers=1
    )
    with session_factory.begin() as session:
        borrower = session.scalar(
            select(Borrower).where(Borrower.campaign_id == campaign_id)
        )
        for index in range(30):
            observation = "observed" if index < 10 else ("inferred" if index < 20 else None)
            session.add(CallIntent(
                campaign_id=campaign_id,
                borrower_id=borrower.id,
                mode=IntentMode.PREDICTIVE,
                state=CallState.COMPLETED,
                provider_name="plivo_mock",
                provider_idempotency_key=f"inference-history:{index}",
                provider_call_id=f"inference-provider-call:{index}",
                answer_observation=observation,
            ))
        session.flush()

        history = load_answer_history(session, campaign_id=campaign_id)

    assert history.observed_answers == 10
    assert history.observed_attempts == 20
    assert getattr(history, "inferred_answers", None) == 10
