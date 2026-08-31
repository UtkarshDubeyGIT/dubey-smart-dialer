from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from smart_dialer.db.models import (
    Agent, Borrower, CallIntent, Campaign, Incident, ProviderEvent, ProviderHealth,
    SafetyDecision,
)
from smart_dialer.db.session import build_session_factory
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.providers.base import NormalizedProviderEvent
from smart_dialer.services.coordinator import run_pacing_tick
from smart_dialer.services.events import ingest_provider_event
from smart_dialer.services.presence import handle_graceful_departure


class CampaignCreate(BaseModel):
    name: str
    mode: Literal["progressive", "predictive"] = "progressive"
    language: str = "en-IN"
    risk_tolerance: float = Field(default=0.005, ge=0.0, le=0.01)
    provider_name: Literal["plivo_mock", "bland_mock"] = "plivo_mock"


class AgentCreate(BaseModel):
    campaign_id: str
    name: str
    language: str = "en-IN"


class AgentStateChange(BaseModel):
    state: Literal["paused", "offline", "available"]


class BorrowerCreate(BaseModel):
    campaign_id: str
    external_id: str
    phone: str
    language: str = "en-IN"


class PacingTickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_data_stale: bool = False


class ProviderEventCreate(BaseModel):
    provider_name: str
    provider_event_id: str
    call_intent_id: str
    target_state: CallState
    semantic_fingerprint: str
    occurred_at: datetime
    payload: dict = Field(default_factory=dict)


def create_app(*, session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    factory = session_factory or build_session_factory()
    app = FastAPI(title="CredResolve SmartDialer", version="0.1.0")

    def get_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    Db = Annotated[Session, Depends(get_session)]

    @app.get("/health")
    def health(db: Db) -> dict:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.post("/v1/campaigns", status_code=201)
    def create_campaign(body: CampaignCreate, db: Db) -> dict:
        with db.begin():
            row = Campaign(**body.model_dump())
            db.add(row)
            db.flush()
            return _campaign(row)

    @app.get("/v1/campaigns")
    def list_campaigns(db: Db) -> list[dict]:
        return [_campaign(row) for row in db.scalars(select(Campaign).order_by(Campaign.created_at))]

    @app.post("/v1/agents", status_code=201)
    def create_agent(body: AgentCreate, db: Db) -> dict:
        with db.begin():
            if db.get(Campaign, body.campaign_id) is None:
                raise HTTPException(404, "campaign not found")
            row = Agent(**body.model_dump(), state=AgentState.OFFLINE)
            db.add(row); db.flush()
            return _agent(row)

    @app.post("/v1/agents/{agent_id}/heartbeat")
    def heartbeat(agent_id: str, db: Db) -> dict:
        with db.begin():
            row = db.get(Agent, agent_id)
            if row is None: raise HTTPException(404, "agent not found")
            now = datetime.now(UTC)
            row.last_heartbeat_at = now
            if row.state is AgentState.OFFLINE:
                row.state = AgentState.AVAILABLE
                row.available_since = now
            return _agent(row)

    @app.post("/v1/agents/{agent_id}/state")
    def set_agent_state(agent_id: str, body: AgentStateChange, db: Db) -> dict:
        with db.begin():
            now = datetime.now(UTC)
            if body.state == "available":
                row = db.get(Agent, agent_id)
                if row is None: raise HTTPException(404, "agent not found")
                row.state = AgentState.AVAILABLE; row.last_heartbeat_at = now; row.available_since = now
            else:
                try:
                    row = handle_graceful_departure(db, agent_id=agent_id, target=AgentState(body.state), now=now)
                except LookupError:
                    raise HTTPException(404, "agent not found") from None
            return _agent(row)

    @app.post("/v1/borrowers", status_code=201)
    def create_borrower(body: BorrowerCreate, db: Db) -> dict:
        with db.begin():
            if db.get(Campaign, body.campaign_id) is None:
                raise HTTPException(404, "campaign not found")
            row = Borrower(**body.model_dump())
            db.add(row); db.flush()
            return _borrower(row)

    @app.post("/v1/campaigns/{campaign_id}/pacing-tick")
    def pacing_tick(campaign_id: str, body: PacingTickRequest, db: Db) -> dict:
        with db.begin():
            try:
                result = run_pacing_tick(
                    db, campaign_id=campaign_id, worker_id="api", now=datetime.now(UTC),
                    **body.model_dump(),
                )
            except LookupError:
                raise HTTPException(404, "campaign not found") from None
            return {**result.receipt.__dict__, "created_intents": result.created_intents}

    @app.post("/v1/provider-events")
    def provider_event(body: ProviderEventCreate, db: Db) -> dict:
        with db.begin():
            result = ingest_provider_event(db, NormalizedProviderEvent(**body.model_dump()))
            return {"result": result}

    @app.get("/v1/call-intents")
    def list_intents(db: Db) -> list[dict]:
        return [_intent(row) for row in db.scalars(select(CallIntent).order_by(CallIntent.created_at))]

    @app.get("/v1/provider-events")
    def list_events(db: Db) -> list[dict]:
        return [{"id": r.id, "call_intent_id": r.call_intent_id, "provider_event_id": r.provider_event_id,
                 "target_state": r.target_state, "processing_result": r.processing_result}
                for r in db.scalars(select(ProviderEvent).order_by(ProviderEvent.received_at))]

    @app.get("/v1/provider-health")
    def list_provider_health(db: Db) -> list[dict]:
        return [
            _provider_health(row)
            for row in db.scalars(select(ProviderHealth).order_by(ProviderHealth.provider_name))
        ]

    @app.get("/v1/safety-decisions")
    def list_safety(db: Db) -> list[dict]:
        return [{"id": r.id, "campaign_id": r.campaign_id, "requested_calls": r.requested_calls,
                 "approved_calls": r.approved_calls, "decision": r.decision,
                 "effective_mode": r.effective_mode, "effective_risk": r.effective_risk,
                 "overload_probability": r.overload_probability, "inputs": r.inputs, "reasons": r.reasons}
                for r in db.scalars(select(SafetyDecision).order_by(SafetyDecision.created_at.desc()))]

    @app.get("/v1/incidents")
    def list_incidents(db: Db) -> list[dict]:
        return [_incident(r) for r in db.scalars(select(Incident).order_by(Incident.created_at.desc()))]

    @app.get("/v1/manual-review")
    def manual_review(db: Db) -> list[dict]:
        return [_intent(r) for r in db.scalars(select(CallIntent).where(CallIntent.manual_review_reason.is_not(None)))]

    return app


def _campaign(r: Campaign) -> dict:
    return {"id": r.id, "name": r.name, "mode": r.mode, "language": r.language,
            "risk_tolerance": r.risk_tolerance, "provider_name": r.provider_name}


def _agent(r: Agent) -> dict:
    return {"id": r.id, "campaign_id": r.campaign_id, "name": r.name,
            "language": r.language, "state": r.state, "last_heartbeat_at": r.last_heartbeat_at}


def _borrower(r: Borrower) -> dict:
    return {"id": r.id, "campaign_id": r.campaign_id, "external_id": r.external_id,
            "phone": r.phone, "language": r.language, "state": r.state}


def _intent(r: CallIntent) -> dict:
    return {"id": r.id, "campaign_id": r.campaign_id, "borrower_id": r.borrower_id,
            "agent_id": r.agent_id, "safety_decision_id": r.safety_decision_id,
            "mode": r.mode, "state": r.state,
            "provider_name": r.provider_name, "processing_attempts": r.processing_attempts,
            "answer_observation": r.answer_observation, "manual_review_reason": r.manual_review_reason}


def _incident(r: Incident) -> dict:
    return {"id": r.id, "call_intent_id": r.call_intent_id, "kind": r.kind,
            "detail": r.detail, "status": r.status, "created_at": r.created_at}


def _provider_health(r: ProviderHealth) -> dict:
    attempts = len(r.recent_outcomes)
    failures = attempts - sum(r.recent_outcomes)
    return {
        "provider_name": r.provider_name,
        "state": r.state,
        "recent_attempts": attempts,
        "recent_failure_rate": failures / attempts if attempts else 0.0,
        "consecutive_timeouts": r.consecutive_timeouts,
        "opened_at": r.opened_at,
        "last_probe_at": r.last_probe_at,
        "updated_at": r.updated_at,
    }


app = create_app()
