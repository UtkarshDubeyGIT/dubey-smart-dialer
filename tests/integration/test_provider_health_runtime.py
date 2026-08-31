from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from smart_dialer.api import create_app
from smart_dialer.db.models import (
    Agent,
    Borrower,
    CallIntent,
    Campaign,
    IntentMode,
    ProviderHealth,
)
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.providers.registry import BLAND, PLIVO
from smart_dialer.services.coordinator import run_pacing_tick
from smart_dialer.worker_loop import run_once


pytestmark = pytest.mark.integration


def seed_reserved_intents(session_factory, *, count: int = 3) -> list[str]:
    intent_ids: list[str] = []
    with session_factory.begin() as session:
        campaign = Campaign(
            name="provider-outage",
            mode="predictive",
            language="en-IN",
            provider_name="plivo_mock",
        )
        session.add(campaign)
        session.flush()
        for index in range(count):
            borrower = Borrower(
                campaign_id=campaign.id,
                external_id=f"outage-{index}",
                phone=f"+91910000{index:03d}8",
                language="en-IN",
            )
            session.add(borrower)
            session.flush()
            intent = CallIntent(
                campaign_id=campaign.id,
                borrower_id=borrower.id,
                mode=IntentMode.PREDICTIVE,
                state=CallState.RESERVED,
                provider_name="plivo_mock",
                provider_idempotency_key=f"outage:{index}:plivo_mock",
            )
            session.add(intent)
            session.flush()
            intent_ids.append(intent.id)
    return intent_ids


def test_worker_persists_open_circuit_after_three_provider_timeouts(session_factory) -> None:
    seed_reserved_intents(session_factory)
    PLIVO.healthy = False
    BLAND.healthy = True
    try:
        assert run_once(session_factory)
        assert run_once(session_factory)
        assert run_once(session_factory)
    finally:
        PLIVO.healthy = True

    try:
        with session_factory() as session:
            health = session.execute(text(
                "SELECT state, consecutive_timeouts "
                "FROM provider_health WHERE provider_name = 'plivo_mock'"
            )).mappings().one()
    except SQLAlchemyError:
        pytest.fail("worker did not persist shared provider circuit state")

    assert health["state"] == "open"
    assert health["consecutive_timeouts"] == 3


def test_pacing_reads_open_provider_circuit_and_falls_back_automatically(session_factory) -> None:
    seed_reserved_intents(session_factory)
    PLIVO.healthy = False
    BLAND.healthy = True
    try:
        for _ in range(3):
            assert run_once(session_factory)
    finally:
        PLIVO.healthy = True

    now = datetime.now(UTC)
    with session_factory.begin() as session:
        campaign = Campaign(
            name="pacing-during-outage",
            mode="predictive",
            language="en-IN",
            provider_name="plivo_mock",
        )
        session.add(campaign)
        session.flush()
        for index in range(5):
            session.add(Agent(
                campaign_id=campaign.id,
                name=f"Human {index}",
                language="en-IN",
                state=AgentState.AVAILABLE,
                last_heartbeat_at=now,
                available_since=now,
            ))
        for index in range(20):
            session.add(Borrower(
                campaign_id=campaign.id,
                external_id=f"pacing-{index}",
                phone=f"+919200000{index:02d}",
                language="en-IN",
            ))
        result = run_pacing_tick(
            session,
            campaign_id=campaign.id,
            worker_id="runtime-health-test",
            now=now,
            observed_answers=30,
            observed_attempts=100,
        )

    assert result.receipt.effective_mode == "progressive"
    assert "provider degradation" in result.receipt.reasons
    assert result.created_intents == 5


def test_open_circuit_defers_new_intent_without_burning_an_attempt(session_factory) -> None:
    intent_ids = seed_reserved_intents(session_factory, count=4)
    PLIVO.healthy = False
    BLAND.healthy = True
    try:
        for _ in range(3):
            assert run_once(session_factory)
        alternate_calls_before = BLAND.calls_created

        assert run_once(session_factory)
    finally:
        PLIVO.healthy = True

    with session_factory() as session:
        deferred = session.get(CallIntent, intent_ids[3])

    assert deferred.state is CallState.RESERVED
    assert deferred.processing_attempts == 0
    assert BLAND.calls_created == alternate_calls_before


def test_half_open_health_check_recovers_before_waiting_call_proceeds(session_factory) -> None:
    intent_ids = seed_reserved_intents(session_factory, count=4)
    PLIVO.healthy = False
    BLAND.healthy = True
    try:
        for _ in range(3):
            assert run_once(session_factory)

        with session_factory.begin() as session:
            health = session.get(ProviderHealth, "plivo_mock")
            health.opened_at = datetime.now(UTC) - timedelta(seconds=31)

        PLIVO.healthy = True
        original_calls_before = PLIVO.calls_created
        assert run_once(session_factory)
    finally:
        PLIVO.healthy = True

    with session_factory() as session:
        health = session.get(ProviderHealth, "plivo_mock")
        recovered_intent = session.get(CallIntent, intent_ids[3])

    assert health.state == "closed"
    assert health.last_probe_at is not None
    assert recovered_intent.state is CallState.COMPLETED
    assert PLIVO.calls_created == original_calls_before + 1


def test_provider_health_is_visible_through_operations_api(session_factory) -> None:
    seed_reserved_intents(session_factory)
    PLIVO.healthy = False
    BLAND.healthy = True
    try:
        for _ in range(3):
            assert run_once(session_factory)
    finally:
        PLIVO.healthy = True

    with TestClient(create_app(session_factory=session_factory)) as client:
        response = client.get("/v1/provider-health")

    assert response.status_code == 200
    health_by_provider = {row["provider_name"]: row for row in response.json()}
    assert health_by_provider["plivo_mock"]["state"] == "open"
