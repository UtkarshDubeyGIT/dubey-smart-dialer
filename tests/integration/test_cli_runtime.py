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

    result = CliRunner().invoke(cli_module.app, ["seed-demo"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["processed_calls"] > 0
    with session_factory() as session:
        decision = session.scalar(
            select(SafetyDecision).order_by(SafetyDecision.created_at.desc())
        )
    assert decision.inputs["statistics_source"] == "persisted_calls"
    assert decision.inputs["observed_attempts"] >= 30
