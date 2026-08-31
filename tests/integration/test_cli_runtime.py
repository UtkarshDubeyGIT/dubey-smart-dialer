import json

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

import smart_dialer.cli as cli_module
from smart_dialer.db.models import SafetyDecision


pytestmark = pytest.mark.integration


def test_seed_demo_uses_persisted_call_history_for_pacing(
    session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(
        cli_module, "build_session_factory", lambda: session_factory
    )

    result = CliRunner().invoke(cli_module.app, ["--json", "seed-demo"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["processed_calls"] > 0
    with session_factory() as session:
        decision = session.scalar(
            select(SafetyDecision).order_by(SafetyDecision.created_at.desc())
        )
    assert decision.inputs["statistics_source"] == "persisted_calls"
    assert decision.inputs["observed_attempts"] >= 30
    assert decision.inputs["average_setup_seconds"] == 4.0
    assert decision.inputs["average_talk_seconds"] == 60.0


def test_seed_demo_default_output_is_a_readable_summary(
    session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(
        cli_module, "build_session_factory", lambda: session_factory
    )

    result = CliRunner().invoke(cli_module.app, ["seed-demo"])

    assert result.exit_code == 0
    assert "[OK] Reviewer demo ready" in result.stdout
    assert "Campaign ID" in result.stdout
    assert "Approved calls" in result.stdout
    assert "Processed calls" in result.stdout
    assert not result.stdout.lstrip().startswith("{")


def test_missing_agent_error_is_clear_and_has_no_traceback(
    session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(
        cli_module, "build_session_factory", lambda: session_factory
    )

    result = CliRunner().invoke(
        cli_module.app, ["agent-heartbeat", "missing-agent"]
    )

    assert result.exit_code == 2
    assert "[ERROR] Agent 'missing-agent' was not found." in result.output
    assert "Create an agent first" in result.output
    assert "Traceback" not in result.output
