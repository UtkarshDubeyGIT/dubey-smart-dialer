import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from sqlalchemy import func, select

from smart_dialer.db.models import (
    Agent,
    Borrower,
    BorrowerState,
    CallIntent,
    Campaign,
    IntentMode,
    SafetyDecision,
)
from smart_dialer.db.session import build_session_factory
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.simulation import write_report
from smart_dialer.services.coordinator import run_pacing_tick
from smart_dialer.worker_loop import run_forever, run_once


app = typer.Typer(help="CredResolve SmartDialer local operations")


def emit(value) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn
    uvicorn.run("smart_dialer.api:app", host=host, port=port)


@app.command()
def worker(once: bool = False) -> None:
    if once:
        emit({"processed": run_once(build_session_factory())})
    else:
        run_forever()


@app.command()
def simulate(seed: int = 2026, output: Path = Path("reports/simulation.json")) -> None:
    report = write_report(output, seed=seed)
    emit({"output": str(output), "seed": seed, "scenarios": len(report["pacing_scenarios"])})


@app.command("seed-demo")
def seed_demo() -> None:
    factory = build_session_factory(); now = datetime.now(UTC)
    with factory.begin() as session:
        campaign = Campaign(
            name="Reviewer Demo", mode="predictive", risk_tolerance=0.005,
            provider_name="bland_mock", language="en-IN",
        )
        session.add(campaign); session.flush()
        for index in range(10):
            session.add(Agent(
                campaign_id=campaign.id, name=f"Human Agent {index + 1}", language="en-IN",
                state=AgentState.AVAILABLE, last_heartbeat_at=now, available_since=now,
            ))
        for index in range(50):
            session.add(Borrower(
                campaign_id=campaign.id, external_id=f"DEMO-{index + 1}",
                phone=f"+91980000{index:04d}", language="en-IN",
            ))
        historical_borrower = Borrower(
            campaign_id=campaign.id,
            external_id="DEMO-HISTORY",
            phone="+919800009999",
            language="en-IN",
            state=BorrowerState.COMPLETED,
        )
        session.add(historical_borrower)
        session.flush()
        for index in range(40):
            session.add(CallIntent(
                campaign_id=campaign.id,
                borrower_id=historical_borrower.id,
                mode=IntentMode.PREDICTIVE,
                state=CallState.COMPLETED,
                provider_name="bland_mock",
                provider_idempotency_key=f"demo-history:{campaign.id}:{index}",
                provider_call_id=f"demo-history-call:{campaign.id}:{index}",
                answer_observation="observed" if index < 12 else "not_answered",
            ))
        session.flush()
        result = run_pacing_tick(
            session, campaign_id=campaign.id, worker_id="demo", now=now,
        )
        campaign_id = campaign.id
        approved = result.receipt.approved_calls
    processed = 0
    for _ in range(approved + 5):
        if not run_once(factory):
            break
        processed += 1
    emit({"campaign_id": campaign_id, "approved_calls": approved, "processed_calls": processed,
          "inspect": "GET /v1/safety-decisions and /v1/call-intents"})


@app.command("campaign-create")
def campaign_create(
    name: str, mode: str = "progressive", risk: float = 0.005,
    provider: str = "plivo_mock", language: str = "en-IN",
) -> None:
    if mode not in {"progressive", "predictive"} or not 0 <= risk <= 0.01:
        raise typer.BadParameter("mode must be progressive/predictive and risk 0..0.01")
    factory = build_session_factory()
    with factory.begin() as session:
        row = Campaign(name=name, mode=mode, risk_tolerance=risk, provider_name=provider, language=language)
        session.add(row); session.flush(); emit({"id": row.id, "name": row.name})


@app.command("agent-create")
def agent_create(campaign_id: str, name: str, language: str = "en-IN") -> None:
    factory = build_session_factory(); now = datetime.now(UTC)
    with factory.begin() as session:
        row = Agent(campaign_id=campaign_id, name=name, language=language, state=AgentState.AVAILABLE,
                    last_heartbeat_at=now, available_since=now)
        session.add(row); session.flush(); emit({"id": row.id, "state": row.state})


@app.command("agent-heartbeat")
def agent_heartbeat(agent_id: str) -> None:
    factory = build_session_factory(); now = datetime.now(UTC)
    with factory.begin() as session:
        row = session.get(Agent, agent_id)
        if row is None: raise typer.BadParameter("agent not found")
        row.last_heartbeat_at = now
        if row.state is AgentState.OFFLINE:
            row.state = AgentState.AVAILABLE; row.available_since = now
        emit({"id": row.id, "state": row.state, "last_heartbeat_at": now})


@app.command("borrower-create")
def borrower_create(campaign_id: str, external_id: str, phone: str, language: str = "en-IN") -> None:
    factory = build_session_factory()
    with factory.begin() as session:
        row = Borrower(campaign_id=campaign_id, external_id=external_id, phone=phone, language=language)
        session.add(row); session.flush(); emit({"id": row.id, "external_id": row.external_id})


@app.command("pacing-tick")
def pacing_tick(campaign_id: str) -> None:
    factory = build_session_factory()
    with factory.begin() as session:
        result = run_pacing_tick(session, campaign_id=campaign_id, worker_id="cli",
                                 now=datetime.now(UTC))
        emit({**result.receipt.__dict__, "created_intents": result.created_intents})


@app.command("list-state")
def list_state() -> None:
    factory = build_session_factory()
    with factory() as session:
        emit({
            "campaigns": session.scalar(select(func.count(Campaign.id))),
            "agents": session.scalar(select(func.count(Agent.id))),
            "borrowers": session.scalar(select(func.count(Borrower.id))),
            "call_intents": session.scalar(select(func.count(CallIntent.id))),
            "safety_decisions": session.scalar(select(func.count(SafetyDecision.id))),
        })


if __name__ == "__main__":
    app()
