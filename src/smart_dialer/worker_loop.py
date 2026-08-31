import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from smart_dialer.db.models import Agent, Borrower, BorrowerState, CallIntent, Incident
from smart_dialer.db.session import build_session_factory
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.providers.mocks import BlandMockProvider, PlivoMockProvider
from smart_dialer.providers.registry import get_alternate, get_provider
from smart_dialer.services.events import ingest_provider_event
from smart_dialer.services.presence import reap_silent_agents, reconcile_cancelled_leases
from smart_dialer.services.provider_health import provider_allows_initiation, record_provider_attempt
from smart_dialer.services.recovery import claim_next_intent
from smart_dialer.services.worker import initiate_intent_with_reconciliation


logger = logging.getLogger("smart_dialer.worker")


def run_once(factory: sessionmaker[Session]) -> bool:
    now = datetime.now(UTC)
    with factory.begin() as session:
        intent = claim_next_intent(session, worker_id="dialer-worker", now=now)
        if intent is None:
            reap_silent_agents(session, now=now)
            reconcile_cancelled_leases(session, now=now)
            return False
        borrower = session.get(Borrower, intent.borrower_id)
        original_provider_name = intent.provider_name
        provider = get_provider(intent.provider_name)
        if not provider_allows_initiation(
            session, provider=provider, now=now
        ):
            intent.processing_attempts = max(0, intent.processing_attempts - 1)
            intent.lease_owner = None
            intent.lease_expires_at = now + timedelta(seconds=30)
            return True
        alternate = get_alternate(intent.provider_name)
        outcome = initiate_intent_with_reconciliation(
            intent, phone=borrower.phone, provider=provider, alternate=alternate
        )
        original_succeeded = outcome in {"initiated", "reconciled"}
        record_provider_attempt(
            session,
            provider_name=original_provider_name,
            succeeded=original_succeeded,
            timed_out=not original_succeeded,
            now=now,
        )
        if outcome == "failed-over":
            record_provider_attempt(
                session,
                provider_name=intent.provider_name,
                succeeded=True,
                timed_out=False,
                now=now,
            )
        if outcome == "ambiguous":
            session.add(Incident(
                call_intent_id=intent.id, kind="ambiguous_provider_result",
                detail={"provider": intent.provider_name},
            ))
        if intent.state is not CallState.INITIATED:
            return True
        provider = get_provider(intent.provider_name)
        intent.lease_owner = None
        intent.lease_expires_at = None
        borrower.state = BorrowerState.DIALING
        if intent.agent_id:
            agent = session.get(Agent, intent.agent_id)
            if agent and agent.reservation_owner_id == intent.id:
                agent.state = AgentState.DIALING
        # Deterministic local provider callback simulation. Both provider objects
        # still use the same normalized event inbox as an external callback.
        answered = int(''.join(filter(str.isdigit, borrower.phone))[-1]) < 7
        handle = provider.lookup_by_idempotency_key(intent.provider_idempotency_key)
        if isinstance(provider, PlivoMockProvider):
            events = provider.events_for(
                handle, call_intent_id=intent.id, answered=answered, occurred_at=now
            )
        else:
            events = provider.events_for(
                handle, call_intent_id=intent.id, answered=answered,
                disposition="COMPLETED_ACTION" if answered else "NO_ANSWER", occurred_at=now,
            )
        for event in events:
            ingest_provider_event(session, event)
        return True


def run_forever(*, poll_seconds: float = 0.5) -> None:
    factory = build_session_factory()
    logger.info("Worker started; polling every %.2fs.", poll_seconds)
    while True:
        try:
            worked = run_once(factory)
        except Exception:
            logger.exception(
                "Worker iteration failed; retrying in %.2fs.", poll_seconds
            )
            time.sleep(poll_seconds)
            continue
        if not worked:
            time.sleep(poll_seconds)
