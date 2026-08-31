from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from smart_dialer.db.models import (
    Agent,
    Borrower,
    BorrowerState,
    CallIntent,
    Campaign,
    IntentMode,
    new_id,
)
from smart_dialer.domain.states import AgentState, CallState


AGENT_HEARTBEAT_TIMEOUT = timedelta(seconds=15)
CALL_INTENT_LEASE = timedelta(seconds=30)


def reserve_progressive_pair(
    session: Session,
    *,
    campaign_id: str,
    worker_id: str,
    now: datetime,
) -> CallIntent | None:
    """Reserve agent then borrower in the caller's transaction.

    This function never commits. A crash before the surrounding transaction commits
    leaves no reservation because PostgreSQL rolls the entire transaction back.
    """
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        return None

    # Global lock order: agent before borrower whenever both rows are needed.
    agent = session.scalar(
        select(Agent)
        .where(
            Agent.campaign_id == campaign_id,
            Agent.state == AgentState.AVAILABLE,
            Agent.language == campaign.language,
            Agent.last_heartbeat_at >= now - AGENT_HEARTBEAT_TIMEOUT,
        )
        .order_by(Agent.available_since.asc().nullsfirst(), Agent.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if agent is None:
        return None

    borrower = session.scalar(
        select(Borrower)
        .where(
            Borrower.campaign_id == campaign_id,
            Borrower.state == BorrowerState.QUEUED,
            Borrower.language == agent.language,
            or_(Borrower.next_attempt_at.is_(None), Borrower.next_attempt_at <= now),
        )
        .order_by(Borrower.next_attempt_at.asc().nullsfirst(), Borrower.created_at.asc(), Borrower.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if borrower is None:
        return None

    intent_id = new_id()
    lease_expires_at = now + CALL_INTENT_LEASE
    intent = CallIntent(
        id=intent_id,
        campaign_id=campaign_id,
        borrower_id=borrower.id,
        agent_id=agent.id,
        mode=IntentMode.PROGRESSIVE,
        state=CallState.RESERVED,
        provider_name=campaign.provider_name,
        provider_idempotency_key=f"intent:{intent_id}:{campaign.provider_name}",
        lease_owner=None,
        lease_expires_at=None,
    )
    agent.state = AgentState.RESERVED
    agent.reservation_owner_id = intent_id
    agent.reservation_expires_at = lease_expires_at
    borrower.state = BorrowerState.RESERVED
    borrower.reservation_owner_id = intent_id
    session.add(intent)
    session.flush()
    return intent


def reserve_predictive_borrower(
    session: Session,
    *,
    campaign_id: str,
    worker_id: str,
    now: datetime,
) -> CallIntent | None:
    """Reserve only a borrower; a human agent is attached on observed ANSWERED."""
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        return None
    borrower = session.scalar(
        select(Borrower)
        .where(
            Borrower.campaign_id == campaign_id,
            Borrower.state == BorrowerState.QUEUED,
            Borrower.language == campaign.language,
            or_(Borrower.next_attempt_at.is_(None), Borrower.next_attempt_at <= now),
        )
        .order_by(Borrower.next_attempt_at.asc().nullsfirst(), Borrower.created_at, Borrower.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if borrower is None:
        return None
    intent_id = new_id()
    intent = CallIntent(
        id=intent_id,
        campaign_id=campaign_id,
        borrower_id=borrower.id,
        mode=IntentMode.PREDICTIVE,
        state=CallState.RESERVED,
        provider_name=campaign.provider_name,
        provider_idempotency_key=f"intent:{intent_id}:{campaign.provider_name}",
        lease_owner=None,
        lease_expires_at=None,
    )
    borrower.state = BorrowerState.RESERVED
    borrower.reservation_owner_id = intent_id
    session.add(intent)
    session.flush()
    return intent


def attach_agent_on_answer(
    session: Session,
    *,
    intent_id: str,
    now: datetime,
) -> Agent | None:
    """Attach a human agent using the global agent-before-borrower lock order."""
    snapshot = session.get(CallIntent, intent_id)
    if snapshot is None or snapshot.state is not CallState.ANSWERED:
        return None
    campaign = session.get(Campaign, snapshot.campaign_id)
    if campaign is None:
        return None
    agent = session.scalar(
        select(Agent)
        .where(
            Agent.campaign_id == campaign.id,
            Agent.state == AgentState.AVAILABLE,
            Agent.language == campaign.language,
            Agent.last_heartbeat_at >= now - AGENT_HEARTBEAT_TIMEOUT,
        )
        .order_by(Agent.available_since.asc().nullsfirst(), Agent.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if agent is None:
        return None
    borrower = session.scalar(
        select(Borrower).where(Borrower.id == snapshot.borrower_id).with_for_update()
    )
    intent = session.scalar(
        select(CallIntent).where(CallIntent.id == intent_id).with_for_update()
    )
    if borrower is None or intent is None or intent.agent_id is not None:
        return None
    agent.state = AgentState.CONNECTED
    agent.reservation_owner_id = intent.id
    agent.reservation_expires_at = intent.lease_expires_at
    borrower.state = BorrowerState.DIALING
    intent.agent_id = agent.id
    intent.state = CallState.CONNECTED
    session.flush()
    return agent
