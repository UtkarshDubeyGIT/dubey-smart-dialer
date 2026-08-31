from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from smart_dialer.db.models import (
    Agent,
    Borrower,
    CallIntent,
    Campaign,
    ProviderEvent,
    ProviderHealth,
    SafetyDecision,
)
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.providers.mocks import BlandMockProvider, PlivoMockProvider
from smart_dialer.providers.registry import BLAND, PLIVO
from smart_dialer.services.allocation import reserve_progressive_pair, reserve_predictive_borrower
from smart_dialer.services.coordinator import run_pacing_tick
from smart_dialer.services.events import ingest_provider_event
from smart_dialer.services.presence import reap_silent_agents
from smart_dialer.services.recovery import claim_next_intent
from smart_dialer.services.worker import initiate_intent_with_reconciliation
from smart_dialer.worker_loop import run_once


EVIDENCE_SOURCE = "executed_postgresql_production_path"


def _failure_fixture_authorization(
    session: Session,
    *,
    campaign_id: str,
    mode: str,
    approved_calls: int,
) -> str:
    """Persist explicit authorization for a non-pacing failure fixture."""
    decision = SafetyDecision(
        campaign_id=campaign_id,
        requested_calls=approved_calls,
        approved_calls=approved_calls,
        decision="approved",
        effective_mode=mode,
        effective_risk=0.0 if mode == "progressive" else 0.005,
        overload_probability=0.0,
        inputs={"source": "failure_simulation_fixture"},
        reasons=["fixture isolates post-authorization failure handling"],
    )
    session.add(decision)
    session.flush()
    return decision.id


def run_failure_scenarios(
    factory: sessionmaker[Session],
    *,
    seed: int = 2026,
) -> dict:
    return {
        "worker_crash": _worker_crash(factory, seed=seed),
        **_provider_event_disorder(factory, seed=seed + 1),
        "provider_outage": _provider_outage(factory),
        "agent_drop": _agent_drop(factory),
    }


def _worker_crash(factory: sessionmaker[Session], *, seed: int) -> dict:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    with factory.begin() as session:
        campaign = Campaign(
            name="failure-worker-crash",
            mode="progressive",
            language="en-IN",
            provider_name="plivo_mock",
        )
        session.add(campaign)
        session.flush()
        session.add(Agent(
            campaign_id=campaign.id,
            name="Crash Recovery Human",
            language="en-IN",
            state=AgentState.AVAILABLE,
            last_heartbeat_at=now,
            available_since=now,
        ))
        session.add(Borrower(
            campaign_id=campaign.id,
            external_id="crash-borrower",
            phone="+919400000008",
            language="en-IN",
        ))
        session.flush()
        safety_decision_id = _failure_fixture_authorization(
            session,
            campaign_id=campaign.id,
            mode="progressive",
            approved_calls=1,
        )
        reserve_progressive_pair(
            session,
            campaign_id=campaign.id,
            safety_decision_id=safety_decision_id,
            worker_id="failure-simulator",
            now=now,
        )

    provider = PlivoMockProvider(seed=seed)
    crashed_session = factory()
    crashed_transaction = crashed_session.begin()
    try:
        claimed = claim_next_intent(
            crashed_session, worker_id="crashed-worker", now=now
        )
        borrower = crashed_session.get(Borrower, claimed.borrower_id)
        first_outcome = initiate_intent_with_reconciliation(
            claimed,
            phone=borrower.phone,
            provider=provider,
        )
        crashed_transaction.rollback()
    finally:
        crashed_session.close()

    with factory.begin() as session:
        recovered = claim_next_intent(
            session, worker_id="recovery-worker", now=now + timedelta(seconds=1)
        )
        borrower = session.get(Borrower, recovered.borrower_id)
        recovery_outcome = initiate_intent_with_reconciliation(
            recovered,
            phone=borrower.phone,
            provider=provider,
        )
        persisted_state = recovered.state.value
        processing_attempts = recovered.processing_attempts

    return {
        "evidence_source": EVIDENCE_SOURCE,
        "crashed_attempt_outcome_before_rollback": first_outcome,
        "recovery_outcome": recovery_outcome,
        "persisted_state": persisted_state,
        "processing_attempts_after_recovery": processing_attempts,
        "provider_calls_created": provider.calls_created,
        "duplicate_call": provider.calls_created != 1,
    }


def _provider_event_disorder(
    factory: sessionmaker[Session], *, seed: int
) -> dict:
    now = datetime(2026, 8, 31, 12, 5, tzinfo=UTC)
    with factory.begin() as session:
        campaign = Campaign(
            name="failure-provider-events",
            mode="predictive",
            language="en-IN",
            provider_name="bland_mock",
        )
        session.add(campaign)
        session.flush()
        borrower = Borrower(
            campaign_id=campaign.id,
            external_id="event-disorder-borrower",
            phone="+919400000001",
            language="en-IN",
        )
        session.add(borrower)
        session.flush()
        safety_decision_id = _failure_fixture_authorization(
            session,
            campaign_id=campaign.id,
            mode="predictive",
            approved_calls=1,
        )
        intent = reserve_predictive_borrower(
            session,
            campaign_id=campaign.id,
            safety_decision_id=safety_decision_id,
            worker_id="failure-simulator",
            now=now,
        )
        intent.state = CallState.INITIATED
        intent_id = intent.id

    provider = BlandMockProvider(
        seed=seed,
        duplicate_events=True,
        out_of_order_events=True,
    )
    handle = provider.place_call(_request_for(factory, intent_id=intent_id))
    with factory.begin() as session:
        session.get(CallIntent, intent_id).provider_call_id = handle.provider_call_id
    results: list[str] = []
    for event in provider.events_for(
        handle,
        call_intent_id=intent_id,
        answered=True,
        disposition="COMPLETED_ACTION",
        occurred_at=now,
    ):
        with factory.begin() as session:
            results.append(ingest_provider_event(session, event))

    with factory() as session:
        final_state = session.get(CallIntent, intent_id).state.value
        persisted_events = session.scalar(
            select(func.count(ProviderEvent.id)).where(
                ProviderEvent.call_intent_id == intent_id
            )
        )

    common = {
        "evidence_source": EVIDENCE_SOURCE,
        "provider_events_emitted": len(results),
        "provider_events_persisted": persisted_events,
        "applied_transitions": results.count("applied"),
        "duplicate_events_ignored": results.count("duplicate"),
        "stale_events": results.count("stale"),
        "final_state": final_state,
    }
    return {
        "duplicate_events": dict(common),
        "out_of_order_events": dict(common),
    }


def _provider_outage(factory: sessionmaker[Session]) -> dict:
    with factory.begin() as session:
        campaign = Campaign(
            name="failure-provider-outage",
            mode="predictive",
            language="en-IN",
            provider_name="plivo_mock",
        )
        session.add(campaign)
        session.flush()
        safety_decision_id = _failure_fixture_authorization(
            session,
            campaign_id=campaign.id,
            mode="predictive",
            approved_calls=4,
        )
        intent_ids: list[str] = []
        for index in range(4):
            borrower = Borrower(
                campaign_id=campaign.id,
                external_id=f"outage-borrower-{index}",
                phone=f"+91950000{index:03d}8",
                language="en-IN",
            )
            session.add(borrower)
            session.flush()
            intent = reserve_predictive_borrower(
                session,
                campaign_id=campaign.id,
                safety_decision_id=safety_decision_id,
                worker_id="failure-simulator",
                now=datetime.now(UTC),
            )
            intent_ids.append(intent.id)

    PLIVO.healthy = False
    BLAND.healthy = True
    try:
        for _ in range(3):
            run_once(factory)
        with factory() as session:
            circuit_opened = session.get(ProviderHealth, "plivo_mock").state == "open"

        run_once(factory)
        with factory() as session:
            deferred = session.get(CallIntent, intent_ids[3])
            deferred_attempts = deferred.processing_attempts
            deferred_state = deferred.state.value

        with factory.begin() as session:
            health = session.get(ProviderHealth, "plivo_mock")
            health.opened_at = datetime.now(UTC) - timedelta(seconds=31)
            deferred = session.get(CallIntent, intent_ids[3])
            deferred.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        PLIVO.healthy = True
        run_once(factory)
        with factory() as session:
            recovered_health = session.get(ProviderHealth, "plivo_mock")
            recovered_intent = session.get(CallIntent, intent_ids[3])
            health_probe_recovered = (
                recovered_health.state == "closed"
                and recovered_health.last_probe_at is not None
                and recovered_intent.state in {CallState.COMPLETED, CallState.FAILED}
            )
    finally:
        PLIVO.healthy = True
        BLAND.healthy = True

    return {
        "evidence_source": EVIDENCE_SOURCE,
        "circuit_opened": circuit_opened,
        "deferred_state": deferred_state,
        "deferred_attempts_consumed": deferred_attempts,
        "health_probe_recovered": health_probe_recovered,
    }


def _agent_drop(factory: sessionmaker[Session]) -> dict:
    now = datetime(2026, 8, 31, 12, 10, tzinfo=UTC)
    disappearance_at = now - timedelta(seconds=15)
    with factory.begin() as session:
        campaign = Campaign(
            name="failure-agent-drop",
            mode="predictive",
            language="en-IN",
            provider_name="plivo_mock",
        )
        session.add(campaign)
        session.flush()
        scenario_agents: list[Agent] = []
        for index in range(100):
            last_heartbeat = disappearance_at if index < 40 else now
            agent = Agent(
                campaign_id=campaign.id,
                name=f"Drop Human {index}",
                language="en-IN",
                state=AgentState.AVAILABLE,
                last_heartbeat_at=last_heartbeat,
                available_since=now - timedelta(minutes=1),
            )
            scenario_agents.append(agent)
            session.add(agent)
            session.add(Borrower(
                campaign_id=campaign.id,
                external_id=f"drop-borrower-{index}",
                phone=f"+91960000{index:04d}",
                language="en-IN",
            ))
        session.flush()
        campaign_id = campaign.id
        scenario_agent_ids = {agent.id for agent in scenario_agents}

    with factory.begin() as session:
        released = reap_silent_agents(session, now=now)
    with factory.begin() as session:
        pacing = run_pacing_tick(
            session,
            campaign_id=campaign_id,
            worker_id="failure-simulator",
            now=now,
            observed_answers=50,
            observed_attempts=100,
        )

    return {
        "evidence_source": EVIDENCE_SOURCE,
        "agents_before": 100,
        "agents_lost": sum(
            agent.last_heartbeat_at == disappearance_at for agent in scenario_agents
        ),
        "agents_released": len(set(released) & scenario_agent_ids),
        "heartbeat_release_seconds": (now - disappearance_at).total_seconds(),
        "fallback": pacing.receipt.effective_mode,
        "approved_progressive_calls": pacing.receipt.approved_calls,
    }


def _request_for(factory: sessionmaker[Session], *, intent_id: str):
    from smart_dialer.providers.base import PlaceCallRequest

    with factory() as session:
        intent = session.get(CallIntent, intent_id)
        borrower = session.get(Borrower, intent.borrower_id)
        return PlaceCallRequest(
            idempotency_key=intent.provider_idempotency_key,
            call_intent_id=intent.id,
            phone=borrower.phone,
            callback_url="http://api:8000/v1/provider-events",
        )
