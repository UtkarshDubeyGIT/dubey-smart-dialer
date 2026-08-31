import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import typer
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from smart_dialer.db.models import (
    Agent,
    Borrower,
    BorrowerState,
    CallIntent,
    Campaign,
    IntentMode,
    ProviderEvent,
    SafetyDecision,
)
from smart_dialer.db.session import build_session_factory
from smart_dialer.domain.states import AgentState, CallState
from smart_dialer.simulation import write_report
from smart_dialer.services.coordinator import run_pacing_tick
from smart_dialer.worker_loop import run_forever, run_once


app = typer.Typer(
    help="Run and inspect the CredResolve SmartDialer prototype.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

F = TypeVar("F", bound=Callable[..., Any])
PROVIDERS = {"plivo_mock", "bland_mock"}
LABELS = {
    "id": "ID",
    "campaign_id": "Campaign ID",
    "agent_id": "Agent ID",
    "borrower_id": "Borrower ID",
    "call_intent_id": "Call intent ID",
    "provider_name": "Provider",
}


@app.callback()
def configure_output(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of the human summary.",
    ),
) -> None:
    """Configure output shared by every command."""
    ctx.ensure_object(dict)
    ctx.obj["json_output"] = json_output


def _json_default(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _human_value(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=_json_default, separators=(",", ":"))
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _label(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    words = key.replace("_", " ")
    return words[:1].upper() + words[1:]


def emit(ctx: typer.Context, title: str, values: dict[str, Any]) -> None:
    if (ctx.obj or {}).get("json_output"):
        typer.echo(json.dumps(values, indent=2, default=_json_default))
        return

    typer.secho("[OK]", fg=typer.colors.GREEN, bold=True, nl=False)
    typer.echo(f" {title}")
    if not values:
        return
    labels = [_label(key) for key in values]
    width = max(len(label) for label in labels)
    for label, value in zip(labels, values.values(), strict=True):
        typer.echo(f"  {label:<{width}}  {_human_value(value)}")


def fail(message: str, hint: str, *, code: int = 2) -> NoReturn:
    typer.secho("[ERROR]", fg=typer.colors.RED, bold=True, nl=False, err=True)
    typer.echo(f" {message}", err=True)
    typer.echo(f"  Hint: {hint}", err=True)
    raise typer.Exit(code)


def database_errors(command: F) -> F:
    @wraps(command)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return command(*args, **kwargs)
        except typer.Exit:
            raise
        except SQLAlchemyError as exc:
            fail(
                "The database operation failed.",
                "Start the stack with `docker compose up --build`, then retry.",
            )
            raise AssertionError("unreachable") from exc

    return wrapped  # type: ignore[return-value]


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the local HTTP API."""
    import uvicorn

    uvicorn.run("smart_dialer.api:app", host=host, port=port)


@app.command()
@database_errors
def worker(ctx: typer.Context, once: bool = False) -> None:
    """Process durable call intents continuously or once."""
    if once:
        emit(
            ctx,
            "Worker iteration complete",
            {"processed_work_item": run_once(build_session_factory())},
        )
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    try:
        run_forever()
    except KeyboardInterrupt:
        typer.echo("[INFO] Worker stopped.")


@app.command()
def simulate(
    ctx: typer.Context,
    seed: int = 2026,
    output: Path = Path("reports/simulation.json"),
) -> None:
    """Run deterministic pacing and PostgreSQL failure scenarios."""
    report = write_report(output, seed=seed)
    emit(
        ctx,
        "Simulation complete",
        {
            "output": str(output),
            "seed": seed,
            "pacing_scenarios": len(report["pacing_scenarios"]),
            "failure_scenarios": len(report.get("failure_scenarios", {})),
        },
    )


@app.command("seed-demo")
@database_errors
def seed_demo(ctx: typer.Context) -> None:
    """Seed and execute the prepared reviewer demonstration."""
    factory = build_session_factory()
    now = datetime.now(UTC)
    with factory.begin() as session:
        campaign = Campaign(
            name="Reviewer Demo",
            mode="predictive",
            risk_tolerance=0.005,
            provider_name="bland_mock",
            language="en-IN",
        )
        session.add(campaign)
        session.flush()
        for index in range(10):
            session.add(
                Agent(
                    campaign_id=campaign.id,
                    name=f"Human Agent {index + 1}",
                    language="en-IN",
                    state=AgentState.AVAILABLE,
                    last_heartbeat_at=now,
                    available_since=now,
                )
            )
        for index in range(50):
            session.add(
                Borrower(
                    campaign_id=campaign.id,
                    external_id=f"DEMO-{index + 1}",
                    phone=f"+91980000{index:04d}",
                    language="en-IN",
                )
            )
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
            historical_intent = CallIntent(
                campaign_id=campaign.id,
                borrower_id=historical_borrower.id,
                mode=IntentMode.PREDICTIVE,
                state=CallState.COMPLETED,
                provider_name="bland_mock",
                provider_idempotency_key=f"demo-history:{campaign.id}:{index}",
                provider_call_id=f"demo-history-call:{campaign.id}:{index}",
                answer_observation="observed" if index < 12 else "not_answered",
            )
            session.add(historical_intent)
            session.flush()
            if index < 12:
                ringing_at = now - timedelta(seconds=70 + index)
                for suffix, state, occurred_at in (
                    ("ringing", CallState.RINGING, ringing_at),
                    ("answered", CallState.ANSWERED, ringing_at + timedelta(seconds=4)),
                    ("completed", CallState.COMPLETED, ringing_at + timedelta(seconds=64)),
                ):
                    session.add(
                        ProviderEvent(
                            call_intent_id=historical_intent.id,
                            provider_name="bland_mock",
                            provider_event_id=f"demo-history:{campaign.id}:{index}:{suffix}",
                            semantic_fingerprint=f"demo-fingerprint:{campaign.id}:{index}:{suffix}",
                            target_state=state,
                            occurred_at=occurred_at,
                            payload={"seeded_history": True},
                            processing_result="applied",
                        )
                    )
        session.flush()
        result = run_pacing_tick(
            session,
            campaign_id=campaign.id,
            worker_id="demo",
            now=now,
        )
        campaign_id = campaign.id
        approved = result.receipt.approved_calls

    processed = 0
    for _ in range(approved + 5):
        if not run_once(factory):
            break
        processed += 1
    emit(
        ctx,
        "Reviewer demo ready",
        {
            "campaign_id": campaign_id,
            "approved_calls": approved,
            "processed_calls": processed,
            "inspect": "/v1/safety-decisions and /v1/call-intents",
        },
    )


@app.command("campaign-create")
@database_errors
def campaign_create(
    ctx: typer.Context,
    name: str,
    mode: str = "progressive",
    risk: float = 0.005,
    provider: str = "plivo_mock",
    language: str = "en-IN",
) -> None:
    """Create a progressive or predictive campaign."""
    if mode not in {"progressive", "predictive"}:
        fail(f"Invalid mode '{mode}'.", "Choose 'progressive' or 'predictive'.")
    if not 0 <= risk <= 0.01:
        fail(
            f"Invalid risk tolerance '{risk}'.",
            "Expected a value from 0 to 0.01.",
        )
    if provider not in PROVIDERS:
        fail(
            f"Unsupported provider '{provider}'.",
            "Choose 'plivo_mock' or 'bland_mock'.",
        )

    factory = build_session_factory()
    with factory.begin() as session:
        row = Campaign(
            name=name,
            mode=mode,
            risk_tolerance=risk,
            provider_name=provider,
            language=language,
        )
        session.add(row)
        session.flush()
        values = {
            "id": row.id,
            "name": row.name,
            "mode": row.mode,
            "risk_tolerance": row.risk_tolerance,
            "provider_name": row.provider_name,
        }
    emit(ctx, "Campaign created", values)


@app.command("agent-create")
@database_errors
def agent_create(
    ctx: typer.Context,
    campaign_id: str,
    name: str,
    language: str = "en-IN",
) -> None:
    """Create an available human agent."""
    factory = build_session_factory()
    now = datetime.now(UTC)
    with factory.begin() as session:
        if session.get(Campaign, campaign_id) is None:
            fail(
                f"Campaign '{campaign_id}' was not found.",
                "Create a campaign first with `campaign-create`.",
            )
        row = Agent(
            campaign_id=campaign_id,
            name=name,
            language=language,
            state=AgentState.AVAILABLE,
            last_heartbeat_at=now,
            available_since=now,
        )
        session.add(row)
        session.flush()
        values = {"id": row.id, "name": row.name, "state": row.state}
    emit(ctx, "Human agent created", values)


@app.command("agent-heartbeat")
@database_errors
def agent_heartbeat(ctx: typer.Context, agent_id: str) -> None:
    """Refresh an agent heartbeat and restore an offline agent."""
    factory = build_session_factory()
    now = datetime.now(UTC)
    with factory.begin() as session:
        row = session.get(Agent, agent_id)
        if row is None:
            fail(
                f"Agent '{agent_id}' was not found.",
                "Create an agent first with `agent-create`.",
            )
        row.last_heartbeat_at = now
        if row.state is AgentState.OFFLINE:
            row.state = AgentState.AVAILABLE
            row.available_since = now
        values = {
            "id": row.id,
            "state": row.state,
            "last_heartbeat_at": now,
        }
    emit(ctx, "Agent heartbeat recorded", values)


@app.command("borrower-create")
@database_errors
def borrower_create(
    ctx: typer.Context,
    campaign_id: str,
    external_id: str,
    phone: str,
    language: str = "en-IN",
) -> None:
    """Queue a borrower for a campaign."""
    factory = build_session_factory()
    with factory.begin() as session:
        if session.get(Campaign, campaign_id) is None:
            fail(
                f"Campaign '{campaign_id}' was not found.",
                "Create a campaign first with `campaign-create`.",
            )
        row = Borrower(
            campaign_id=campaign_id,
            external_id=external_id,
            phone=phone,
            language=language,
        )
        session.add(row)
        session.flush()
        values = {
            "id": row.id,
            "external_id": row.external_id,
            "state": row.state,
        }
    emit(ctx, "Borrower queued", values)


@app.command("pacing-tick")
@database_errors
def pacing_tick(ctx: typer.Context, campaign_id: str) -> None:
    """Run one persisted pacing and Safety Controller decision."""
    factory = build_session_factory()
    with factory.begin() as session:
        try:
            result = run_pacing_tick(
                session,
                campaign_id=campaign_id,
                worker_id="cli",
                now=datetime.now(UTC),
            )
        except LookupError:
            fail(
                f"Campaign '{campaign_id}' was not found.",
                "Create a campaign first with `campaign-create`.",
            )
        values = {
            **result.receipt.__dict__,
            "created_intents": result.created_intents,
        }
    emit(ctx, "Pacing decision committed", values)


@app.command("list-state")
@database_errors
def list_state(ctx: typer.Context) -> None:
    """Show current persisted object counts."""
    factory = build_session_factory()
    with factory() as session:
        values = {
            "campaigns": session.scalar(select(func.count(Campaign.id))),
            "agents": session.scalar(select(func.count(Agent.id))),
            "borrowers": session.scalar(select(func.count(Borrower.id))),
            "call_intents": session.scalar(select(func.count(CallIntent.id))),
            "safety_decisions": session.scalar(select(func.count(SafetyDecision.id))),
        }
    emit(ctx, "Current system state", values)


if __name__ == "__main__":
    app()
