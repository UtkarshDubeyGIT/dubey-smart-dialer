from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from smart_dialer.db.models import Agent, CallIntent, Incident
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.services.recovery import release_owned_reservations


HEARTBEAT_TIMEOUT = timedelta(seconds=15)
RINGING_CANCELLATION_LEASE = timedelta(seconds=10)


def handle_graceful_departure(
    session: Session,
    *,
    agent_id: str,
    target: AgentState,
    now: datetime,
) -> Agent:
    if target not in {AgentState.PAUSED, AgentState.OFFLINE}:
        raise ValueError("graceful departure target must be paused or offline")
    agent = session.scalar(select(Agent).where(Agent.id == agent_id).with_for_update())
    if agent is None:
        raise LookupError(agent_id)
    agent.last_heartbeat_at = now
    if agent.reservation_owner_id:
        _reconcile_agent_loss(session, agent, target=target, now=now, path="graceful")
    else:
        agent.state = target
    session.flush()
    return agent


def reap_silent_agents(session: Session, *, now: datetime) -> list[str]:
    agents = session.scalars(
        select(Agent)
        .where(
            Agent.state.not_in({AgentState.OFFLINE, AgentState.PAUSED}),
            Agent.last_heartbeat_at <= now - HEARTBEAT_TIMEOUT,
        )
        .order_by(Agent.id)
        .with_for_update(skip_locked=True)
    ).all()
    released: list[str] = []
    for agent in agents:
        if agent.reservation_owner_id:
            released_now = _reconcile_agent_loss(
                session, agent, target=AgentState.OFFLINE, now=now, path="silent"
            )
            if released_now:
                released.append(agent.id)
        else:
            agent.state = AgentState.OFFLINE
            released.append(agent.id)
    session.flush()
    return released


def _reconcile_agent_loss(
    session: Session,
    agent: Agent,
    *,
    target: AgentState,
    now: datetime,
    path: str,
) -> bool:
    """Call-intent reconciliation takes precedence over heartbeat release."""
    intent = session.scalar(
        select(CallIntent)
        .where(CallIntent.id == agent.reservation_owner_id)
        .with_for_update()
    )
    if intent is None:
        agent.reservation_owner_id = None
        agent.reservation_expires_at = None
        agent.state = target
        return True
    session.add(Incident(
        call_intent_id=intent.id,
        kind="agent_disappeared",
        detail={"path": path, "call_state": intent.state.value},
    ))
    if intent.state is CallState.RINGING:
        intent.state = CallState.CANCELLED
        intent.lease_owner = "reconcile-before-release"
        intent.lease_expires_at = now + RINGING_CANCELLATION_LEASE
        return False
    if intent.state is CallState.CONNECTED:
        intent.manual_review_reason = "human agent disappeared while connected"
        return False
    release_owned_reservations(session, intent)
    agent.state = target
    return True


def reconcile_cancelled_leases(session: Session, *, now: datetime) -> int:
    intents = session.scalars(
        select(CallIntent)
        .where(
            CallIntent.state == CallState.CANCELLED,
            CallIntent.lease_owner == "reconcile-before-release",
            CallIntent.lease_expires_at <= now,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for intent in intents:
        release_owned_reservations(session, intent)
        intent.lease_owner = None
        intent.lease_expires_at = None
    session.flush()
    return len(intents)
