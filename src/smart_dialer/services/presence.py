from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smart_dialer.db.models import Agent, AgentPresenceEvent, CallIntent, Incident
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.services.recovery import release_owned_reservations


HEARTBEAT_TIMEOUT = timedelta(seconds=15)
RINGING_CANCELLATION_LEASE = timedelta(seconds=10)
RAPID_DROP_WINDOW = timedelta(seconds=30)
RAPID_DROP_MINIMUM = 5
RAPID_DROP_FRACTION = 0.20


@dataclass(frozen=True)
class RapidAgentDropSignal:
    detected: bool
    recent_losses: int
    estimated_baseline: int
    threshold: int
    window_seconds: int


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
    previous_state = agent.state
    if agent.reservation_owner_id:
        _reconcile_agent_loss(session, agent, target=target, now=now, path="graceful")
    else:
        agent.state = target
        _record_available_departure(
            session,
            agent=agent,
            previous_state=previous_state,
            target=target,
            path="graceful",
            now=now,
        )
    session.flush()
    return agent


def reap_silent_agents(
    session: Session,
    *,
    now: datetime,
    campaign_id: str | None = None,
) -> list[str]:
    campaign_filter = (() if campaign_id is None else (Agent.campaign_id == campaign_id,))
    agents = session.scalars(
        select(Agent)
        .where(
            Agent.state.not_in({AgentState.OFFLINE, AgentState.PAUSED}),
            Agent.last_heartbeat_at <= now - HEARTBEAT_TIMEOUT,
            *campaign_filter,
        )
        .order_by(Agent.id)
        .with_for_update(skip_locked=True)
    ).all()
    released: list[str] = []
    for agent in agents:
        previous_state = agent.state
        if agent.reservation_owner_id:
            released_now = _reconcile_agent_loss(
                session, agent, target=AgentState.OFFLINE, now=now, path="silent"
            )
            if released_now:
                released.append(agent.id)
        else:
            agent.state = AgentState.OFFLINE
            _record_available_departure(
                session,
                agent=agent,
                previous_state=previous_state,
                target=AgentState.OFFLINE,
                path="silent",
                now=now,
            )
            released.append(agent.id)
    session.flush()
    return released


def detect_rapid_agent_drop(
    session: Session,
    *,
    campaign_id: str,
    current_available: int,
    now: datetime,
) -> RapidAgentDropSignal:
    """Derive the live fallback signal from persisted presence transitions.

    The baseline reconstructs the pre-drop available pool as current availability
    plus recent departures. Five losses and 20% of that baseline are both required,
    preventing one ordinary departure from degrading a healthy campaign.
    """
    recent_losses = session.scalar(
        select(func.count(AgentPresenceEvent.id)).where(
            AgentPresenceEvent.campaign_id == campaign_id,
            AgentPresenceEvent.previous_state == AgentState.AVAILABLE.value,
            AgentPresenceEvent.occurred_at >= now - RAPID_DROP_WINDOW,
        )
    ) or 0
    baseline = current_available + recent_losses
    threshold = max(RAPID_DROP_MINIMUM, ceil(baseline * RAPID_DROP_FRACTION))
    return RapidAgentDropSignal(
        detected=recent_losses >= threshold,
        recent_losses=recent_losses,
        estimated_baseline=baseline,
        threshold=threshold,
        window_seconds=int(RAPID_DROP_WINDOW.total_seconds()),
    )


def _record_available_departure(
    session: Session,
    *,
    agent: Agent,
    previous_state: AgentState,
    target: AgentState,
    path: str,
    now: datetime,
) -> None:
    if previous_state is not AgentState.AVAILABLE:
        return
    session.add(AgentPresenceEvent(
        campaign_id=agent.campaign_id,
        agent_id=agent.id,
        previous_state=previous_state.value,
        target_state=target.value,
        path=path,
        occurred_at=now,
    ))


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
