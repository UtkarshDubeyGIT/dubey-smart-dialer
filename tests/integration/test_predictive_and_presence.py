from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from smart_dialer.db.models import Agent, Borrower, BorrowerState, CallIntent, Campaign, Incident, IntentMode
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.services.allocation import attach_agent_on_answer, reserve_predictive_borrower
from smart_dialer.services.presence import handle_graceful_departure, reap_silent_agents
from smart_dialer.providers.base import NormalizedProviderEvent
from smart_dialer.services.events import ingest_provider_event

pytestmark = pytest.mark.integration


def seed(session_factory) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        campaign = Campaign(name="predictive", mode="predictive", language="en-IN")
        session.add(campaign)
        session.flush()
        agent = Agent(
            campaign_id=campaign.id, name="Human Agent", language="en-IN",
            state=AgentState.AVAILABLE, last_heartbeat_at=now, available_since=now,
        )
        borrower = Borrower(
            campaign_id=campaign.id, external_id="borrower", phone="+919111111111", language="en-IN",
        )
        session.add_all([agent, borrower])
        session.flush()
        return campaign.id, agent.id, borrower.id


def test_predictive_dial_reserves_borrower_without_pre_reserving_agent(session_factory) -> None:
    campaign_id, agent_id, _ = seed(session_factory)
    with session_factory.begin() as session:
        intent = reserve_predictive_borrower(
            session, campaign_id=campaign_id, worker_id="pacer", now=datetime.now(UTC)
        )
        assert intent is not None
        assert intent.mode is IntentMode.PREDICTIVE
        assert intent.agent_id is None
        assert session.get(Agent, agent_id).state is AgentState.AVAILABLE


def test_answered_predictive_call_atomically_claims_human_agent(session_factory) -> None:
    campaign_id, agent_id, _ = seed(session_factory)
    with session_factory.begin() as session:
        intent = reserve_predictive_borrower(
            session, campaign_id=campaign_id, worker_id="pacer", now=datetime.now(UTC)
        )
        intent.state = CallState.ANSWERED
        intent_id = intent.id
    with session_factory.begin() as session:
        attached = attach_agent_on_answer(session, intent_id=intent_id, now=datetime.now(UTC))
        assert attached is not None and attached.id == agent_id
        assert attached.state is AgentState.CONNECTED
        assert session.get(CallIntent, intent_id).agent_id == agent_id


def test_answered_event_automatically_attaches_human_agent(session_factory) -> None:
    campaign_id, agent_id, _ = seed(session_factory)
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        intent = reserve_predictive_borrower(session, campaign_id=campaign_id, worker_id="pacer", now=now)
        intent.state = CallState.INITIATED
        intent_id = intent.id
    with session_factory.begin() as session:
        result = ingest_provider_event(session, NormalizedProviderEvent(
            provider_name="bland_mock", provider_event_id="answered-auto",
            call_intent_id=intent_id, target_state=CallState.ANSWERED,
            occurred_at=now, semantic_fingerprint="answered-auto", payload={},
        ))
        assert result == "applied"
    with session_factory() as session:
        intent = session.get(CallIntent, intent_id)
        assert intent.state is CallState.CONNECTED
        assert intent.agent_id == agent_id
        assert intent.answer_observation == "observed"


def test_answer_with_no_human_agent_creates_overload_incident(session_factory) -> None:
    campaign_id, agent_id, _ = seed(session_factory)
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        session.get(Agent, agent_id).state = AgentState.OFFLINE
        intent = reserve_predictive_borrower(session, campaign_id=campaign_id, worker_id="pacer", now=now)
        intent.state = CallState.INITIATED
        intent_id = intent.id
    with session_factory.begin() as session:
        ingest_provider_event(session, NormalizedProviderEvent(
            provider_name="bland_mock", provider_event_id="overload",
            call_intent_id=intent_id, target_state=CallState.ANSWERED,
            occurred_at=now, semantic_fingerprint="overload", payload={},
        ))
    with session_factory() as session:
        intent = session.get(CallIntent, intent_id)
        assert intent.state is CallState.FAILED
        assert intent.manual_review_reason == "answered call had no human agent capacity"
        assert session.scalar(select(Incident).where(Incident.call_intent_id == intent_id)).kind == "overload"


def test_graceful_departure_is_immediate(session_factory) -> None:
    campaign_id, agent_id, _ = seed(session_factory)
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        changed = handle_graceful_departure(session, agent_id=agent_id, target=AgentState.PAUSED, now=now)
        assert changed.state is AgentState.PAUSED
        assert changed.last_heartbeat_at == now


def test_silent_available_agent_is_offline_after_fifteen_seconds(session_factory) -> None:
    _, agent_id, _ = seed(session_factory)
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        session.get(Agent, agent_id).last_heartbeat_at = now - timedelta(seconds=16)
    with session_factory.begin() as session:
        reaped = reap_silent_agents(session, now=now)
        assert reaped == [agent_id]
        assert session.get(Agent, agent_id).state is AgentState.OFFLINE


def test_ringing_reservation_gets_ten_second_cancel_lease_before_release(session_factory) -> None:
    campaign_id, agent_id, borrower_id = seed(session_factory)
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        agent = session.get(Agent, agent_id)
        borrower = session.get(Borrower, borrower_id)
        intent = CallIntent(
            campaign_id=campaign_id, borrower_id=borrower_id, agent_id=agent_id,
            mode=IntentMode.PROGRESSIVE, state=CallState.RINGING, provider_name="plivo_mock",
            provider_idempotency_key="ringing-key", lease_owner="worker",
            lease_expires_at=now + timedelta(seconds=30),
        )
        session.add(intent)
        session.flush()
        agent.state = AgentState.DIALING
        agent.reservation_owner_id = intent.id
        agent.last_heartbeat_at = now - timedelta(seconds=16)
        borrower.state = BorrowerState.RESERVED
        borrower.reservation_owner_id = intent.id
        intent_id = intent.id
    with session_factory.begin() as session:
        reaped = reap_silent_agents(session, now=now)
        intent = session.get(CallIntent, intent_id)
        assert reaped == []
        assert intent.state is CallState.CANCELLED
        assert intent.lease_expires_at == now + timedelta(seconds=10)
        assert session.get(Agent, agent_id).reservation_owner_id == intent_id
        assert session.scalar(select(Incident).where(Incident.call_intent_id == intent_id)) is not None
