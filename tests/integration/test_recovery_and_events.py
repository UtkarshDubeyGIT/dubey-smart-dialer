from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from smart_dialer.db.models import Agent, Borrower, BorrowerState, CallIntent, Campaign, Incident, ProviderEvent
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.providers.base import NormalizedProviderEvent
from smart_dialer.services.allocation import reserve_progressive_pair
from smart_dialer.services.events import ingest_provider_event
from smart_dialer.services.recovery import claim_next_intent, fail_poison_intent

pytestmark = pytest.mark.integration


def seed_campaign(session_factory, *, pairs: int) -> str:
    with session_factory.begin() as session:
        campaign = Campaign(name="collections", mode="progressive", language="en-IN")
        session.add(campaign)
        session.flush()
        for index in range(pairs):
            session.add(Agent(
                campaign_id=campaign.id, name=f"Agent {index}", language="en-IN",
                state=AgentState.AVAILABLE, last_heartbeat_at=datetime.now(UTC),
                available_since=datetime.now(UTC),
            ))
            session.add(Borrower(
                campaign_id=campaign.id, external_id=f"borrower-{index}",
                phone=f"+91900000{index:04d}", language="en-IN",
            ))
        return campaign.id


def create_intent(session_factory) -> str:
    campaign_id = seed_campaign(session_factory, pairs=1)
    with session_factory.begin() as session:
        intent = reserve_progressive_pair(
            session,
            campaign_id=campaign_id,
            worker_id="allocator",
            now=datetime.now(UTC),
        )
        assert intent is not None
        return intent.id


def event(intent_id: str, event_id: str, state: CallState, fingerprint: str | None = None):
    return NormalizedProviderEvent(
        provider_name="bland_mock",
        provider_event_id=event_id,
        call_intent_id=intent_id,
        target_state=state,
        occurred_at=datetime.now(UTC),
        payload={},
        semantic_fingerprint=fingerprint or event_id,
    )


def test_expired_lease_can_be_claimed_and_attempt_count_increments(session_factory) -> None:
    intent_id = create_intent(session_factory)
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        intent = session.get(CallIntent, intent_id)
        assert intent is not None
        intent.lease_expires_at = now - timedelta(seconds=1)

    with session_factory.begin() as session:
        claimed = claim_next_intent(session, worker_id="recovery", now=now)
        assert claimed is not None and claimed.id == intent_id
        assert claimed.processing_attempts == 1
        assert claimed.lease_owner == "recovery"
        assert claimed.lease_expires_at == now + timedelta(seconds=30)


def test_poison_intent_fails_and_releases_owned_reservations(session_factory) -> None:
    intent_id = create_intent(session_factory)
    with session_factory.begin() as session:
        intent = session.get(CallIntent, intent_id)
        assert intent is not None
        intent.processing_attempts = 3
        fail_poison_intent(session, intent, reason="max attempts exceeded")

    with session_factory() as session:
        intent = session.get(CallIntent, intent_id)
        agent = session.get(Agent, intent.agent_id)
        borrower = session.get(Borrower, intent.borrower_id)
        assert intent.state is CallState.FAILED
        assert intent.manual_review_reason == "max attempts exceeded"
        assert agent.state is AgentState.AVAILABLE and agent.reservation_owner_id is None
        assert borrower.reservation_owner_id is None
        assert borrower.state is BorrowerState.MANUAL_REVIEW
        assert session.scalar(select(func.count(Incident.id))) == 1


def test_duplicate_provider_event_is_inserted_and_applied_once(session_factory) -> None:
    intent_id = create_intent(session_factory)
    with session_factory.begin() as session:
        intent = session.get(CallIntent, intent_id)
        intent.state = CallState.INITIATED

    with session_factory.begin() as session:
        assert ingest_provider_event(session, event(intent_id, "evt-1", CallState.ANSWERED)) == "applied"
    with session_factory.begin() as session:
        assert ingest_provider_event(session, event(intent_id, "evt-1", CallState.ANSWERED)) == "duplicate"

    with session_factory() as session:
        assert session.scalar(select(func.count(ProviderEvent.id))) == 1
        assert session.get(CallIntent, intent_id).state is CallState.ANSWERED


def test_duplicate_semantic_fingerprint_is_deduplicated_even_with_new_id(session_factory) -> None:
    intent_id = create_intent(session_factory)
    with session_factory.begin() as session:
        session.get(CallIntent, intent_id).state = CallState.INITIATED
    with session_factory.begin() as session:
        assert ingest_provider_event(session, event(intent_id, "evt-a", CallState.RINGING, "same")) == "applied"
    with session_factory.begin() as session:
        assert ingest_provider_event(session, event(intent_id, "evt-b", CallState.RINGING, "same")) == "duplicate"


def test_late_backward_event_is_audited_without_regression(session_factory) -> None:
    intent_id = create_intent(session_factory)
    with session_factory.begin() as session:
        session.get(CallIntent, intent_id).state = CallState.COMPLETED
    with session_factory.begin() as session:
        assert ingest_provider_event(session, event(intent_id, "late-ringing", CallState.RINGING)) == "stale"
    with session_factory() as session:
        assert session.get(CallIntent, intent_id).state is CallState.COMPLETED
        stored = session.scalar(select(ProviderEvent).where(ProviderEvent.provider_event_id == "late-ringing"))
        assert stored.processing_result == "stale"


def test_forward_jump_infers_answer_but_does_not_mark_it_observed(session_factory) -> None:
    intent_id = create_intent(session_factory)
    with session_factory.begin() as session:
        session.get(CallIntent, intent_id).state = CallState.INITIATED
    with session_factory.begin() as session:
        assert ingest_provider_event(session, event(intent_id, "completed", CallState.COMPLETED)) == "applied"
    with session_factory() as session:
        intent = session.get(CallIntent, intent_id)
        assert intent.answer_observation == "inferred"


def test_completed_call_releases_human_but_does_not_requeue_borrower(session_factory) -> None:
    intent_id = create_intent(session_factory)
    with session_factory.begin() as session:
        intent = session.get(CallIntent, intent_id)
        intent.state = CallState.INITIATED
        agent_id, borrower_id = intent.agent_id, intent.borrower_id
    with session_factory.begin() as session:
        assert ingest_provider_event(session, event(intent_id, "terminal", CallState.COMPLETED)) == "applied"
    with session_factory() as session:
        agent = session.get(Agent, agent_id)
        borrower = session.get(Borrower, borrower_id)
        assert agent.state is AgentState.AVAILABLE
        assert agent.available_since is not None
        assert borrower.state is BorrowerState.COMPLETED
        assert borrower.reservation_owner_id is None
