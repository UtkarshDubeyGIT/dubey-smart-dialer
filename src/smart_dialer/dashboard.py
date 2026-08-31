from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smart_dialer.db.models import (
    Agent,
    Borrower,
    BorrowerState,
    CallIntent,
    Campaign,
    Incident,
    ProviderHealth,
    SafetyDecision,
)
from smart_dialer.domain.states import AgentState, TERMINAL_CALL_STATES


AGENT_STATE_ORDER = (
    AgentState.AVAILABLE,
    AgentState.CONNECTED,
    AgentState.RESERVED,
    AgentState.DIALING,
    AgentState.WRAP_UP,
    AgentState.PAUSED,
    AgentState.OFFLINE,
)


def _label(value: str) -> str:
    return value.replace("_", " ").title()


def _provider_label(value: str) -> str:
    return value.replace("_mock", " Mock").replace("_", " ").title()


def load_dashboard(session: Session, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    campaigns = session.scalars(
        select(Campaign).order_by(Campaign.created_at.desc())
    ).all()
    campaign_names = {campaign.id: campaign.name for campaign in campaigns}

    agents = session.scalars(
        select(Agent).order_by(Agent.name, Agent.id)
    ).all()
    agent_rank = {state: index for index, state in enumerate(AGENT_STATE_ORDER)}
    agents.sort(key=lambda agent: (agent_rank[agent.state], agent.name, agent.id))
    agent_counts = Counter(agent.state.value for agent in agents)
    agent_states = [
        {
            "key": state.value,
            "label": _label(state.value),
            "count": agent_counts[state.value],
            "percent": round(100 * agent_counts[state.value] / len(agents), 1)
            if agents
            else 0,
        }
        for state in AGENT_STATE_ORDER
        if agent_counts[state.value]
    ]
    visible_agents = [
        {
            "name": agent.name,
            "state": agent.state.value,
            "state_label": _label(agent.state.value),
            "campaign": campaign_names.get(agent.campaign_id, "Unknown campaign"),
            "heartbeat": (
                max(0, int((now - agent.last_heartbeat_at).total_seconds()))
                if agent.last_heartbeat_at
                else None
            ),
        }
        for agent in agents[:8]
    ]

    recent_intents = session.scalars(
        select(CallIntent).order_by(CallIntent.created_at.desc()).limit(8)
    ).all()
    borrower_ids = {intent.borrower_id for intent in recent_intents}
    agent_ids = {intent.agent_id for intent in recent_intents if intent.agent_id}
    borrower_names = {
        borrower.id: borrower.external_id
        for borrower in session.scalars(
            select(Borrower).where(Borrower.id.in_(borrower_ids))
        )
    } if borrower_ids else {}
    intent_agent_names = {
        agent.id: agent.name
        for agent in session.scalars(select(Agent).where(Agent.id.in_(agent_ids)))
    } if agent_ids else {}
    calls = [
        {
            "id": intent.id[:8],
            "campaign": campaign_names.get(intent.campaign_id, "Unknown campaign"),
            "borrower": borrower_names.get(intent.borrower_id, "Unknown borrower"),
            "agent": intent_agent_names.get(intent.agent_id, "Awaiting human"),
            "mode": _label(intent.mode.value),
            "state": intent.state.value,
            "state_label": _label(intent.state.value),
            "provider": _provider_label(intent.provider_name),
            "safety_id": intent.safety_decision_id[:8],
        }
        for intent in recent_intents
    ]

    latest_safety = session.scalar(
        select(SafetyDecision).order_by(SafetyDecision.created_at.desc()).limit(1)
    )
    safety = None
    if latest_safety:
        safety = {
            "id": latest_safety.id[:8],
            "campaign": campaign_names.get(
                latest_safety.campaign_id, "Unknown campaign"
            ),
            "decision": latest_safety.decision,
            "decision_label": _label(latest_safety.decision),
            "mode": _label(latest_safety.effective_mode),
            "approved": latest_safety.approved_calls,
            "requested": latest_safety.requested_calls,
            "risk_percent": latest_safety.effective_risk * 100,
            "overload_percent": latest_safety.overload_probability * 100,
            "reasons": [_label(reason) for reason in latest_safety.reasons],
            "created_at": latest_safety.created_at,
        }

    provider_rows = {
        provider.provider_name: provider
        for provider in session.scalars(select(ProviderHealth))
    }
    provider_names = sorted(
        set(provider_rows) | {campaign.provider_name for campaign in campaigns}
    )
    providers = []
    for provider_name in provider_names:
        row = provider_rows.get(provider_name)
        outcomes = row.recent_outcomes if row else []
        failures = len(outcomes) - sum(outcomes)
        providers.append({
            "name": _provider_label(provider_name),
            "state": row.state if row else "closed",
            "state_label": "Healthy" if row is None or row.state == "closed" else _label(row.state),
            "attempts": len(outcomes),
            "failure_rate": round(100 * failures / len(outcomes), 1) if outcomes else 0,
        })

    incident_rows = session.scalars(
        select(Incident).order_by(Incident.created_at.desc()).limit(5)
    ).all()
    incidents = [
        {
            "kind": _label(incident.kind),
            "status": _label(incident.status),
            "time": incident.created_at,
            "call_id": incident.call_intent_id[:8] if incident.call_intent_id else "System",
        }
        for incident in incident_rows
    ]

    active_calls = session.scalar(
        select(func.count(CallIntent.id)).where(
            CallIntent.state.not_in(TERMINAL_CALL_STATES)
        )
    ) or 0
    manual_review = session.scalar(
        select(func.count(CallIntent.id)).where(
            CallIntent.manual_review_reason.is_not(None)
        )
    ) or 0
    queued_borrowers = session.scalar(
        select(func.count(Borrower.id)).where(
            Borrower.state == BorrowerState.QUEUED
        )
    ) or 0

    return {
        "generated_at": now,
        "campaigns": [
            {
                "name": campaign.name,
                "mode": _label(campaign.mode),
                "provider": _provider_label(campaign.provider_name),
                "risk_percent": campaign.risk_tolerance * 100,
                "language": campaign.language,
            }
            for campaign in campaigns
        ],
        "agent_states": agent_states,
        "agents": visible_agents,
        "calls": calls,
        "safety": safety,
        "providers": providers,
        "incidents": incidents,
        "metrics": {
            "campaigns": len(campaigns),
            "available_agents": agent_counts[AgentState.AVAILABLE.value],
            "total_agents": len(agents),
            "active_calls": active_calls,
            "queued_borrowers": queued_borrowers,
            "manual_review": manual_review,
        },
    }
