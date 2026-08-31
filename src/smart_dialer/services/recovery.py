from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from smart_dialer.db.models import Agent, Borrower, BorrowerState, CallIntent, Incident, utc_now
from smart_dialer.domain.states import AgentState, CallState, TERMINAL_CALL_STATES


CALL_INTENT_LEASE = timedelta(seconds=30)
MAX_PROCESSING_ATTEMPTS = 3


def claim_next_intent(session: Session, *, worker_id: str, now: datetime) -> CallIntent | None:
    """Claim work with SKIP LOCKED; lease expiry provides liveness, not correctness."""
    intent = session.scalar(
        select(CallIntent)
        .where(
            CallIntent.state == CallState.RESERVED,
            or_(CallIntent.lease_expires_at.is_(None), CallIntent.lease_expires_at <= now),
        )
        .order_by(CallIntent.created_at, CallIntent.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if intent is None:
        return None
    if intent.processing_attempts >= MAX_PROCESSING_ATTEMPTS:
        fail_poison_intent(session, intent, reason="maximum processing attempts exceeded")
        return None
    intent.processing_attempts += 1
    intent.lease_owner = worker_id
    intent.lease_expires_at = now + CALL_INTENT_LEASE
    session.flush()
    return intent


def release_owned_reservations(
    session: Session,
    intent: CallIntent,
    *,
    borrower_state: BorrowerState = BorrowerState.QUEUED,
) -> None:
    """Idempotently release only rows still owned by this intent."""
    if intent.agent_id:
        agent = session.get(Agent, intent.agent_id)
        if agent is not None and agent.reservation_owner_id == intent.id:
            agent.state = AgentState.AVAILABLE
            agent.available_since = utc_now()
            agent.reservation_owner_id = None
            agent.reservation_expires_at = None
    borrower = session.get(Borrower, intent.borrower_id)
    if borrower is not None and borrower.reservation_owner_id == intent.id:
        borrower.state = borrower_state
        borrower.reservation_owner_id = None


def fail_poison_intent(session: Session, intent: CallIntent, *, reason: str) -> None:
    intent.state = CallState.FAILED
    intent.manual_review_reason = reason
    intent.lease_owner = None
    intent.lease_expires_at = None
    release_owned_reservations(session, intent, borrower_state=BorrowerState.MANUAL_REVIEW)
    session.add(
        Incident(
            call_intent_id=intent.id,
            kind="manual_review",
            detail={"reason": reason, "processing_attempts": intent.processing_attempts},
        )
    )
    session.flush()
