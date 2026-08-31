from click import unstyle
from typer.testing import CliRunner

from smart_dialer.cli import app


def test_cli_exposes_machine_readable_json_mode() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--json" in unstyle(result.stdout)


def test_pacing_cli_has_no_manual_answer_statistics_options() -> None:
    result = CliRunner().invoke(app, ["pacing-tick", "--help"])

    assert result.exit_code == 0
    assert "--answers" not in result.stdout
    assert "--attempts" not in result.stdout


def test_invalid_campaign_mode_has_a_short_actionable_error() -> None:
    result = CliRunner().invoke(
        app, ["campaign-create", "Demo", "--mode", "batch"]
    )

    assert result.exit_code == 2
    assert "[ERROR] Invalid mode 'batch'." in result.output
    assert "Choose 'progressive' or 'predictive'." in result.output
    assert "Traceback" not in result.output
    assert "╭─ Error" not in result.output


def test_invalid_risk_has_a_separate_actionable_error() -> None:
    result = CliRunner().invoke(
        app, ["campaign-create", "Demo", "--risk", "0.02"]
    )

    assert result.exit_code == 2
    assert "[ERROR] Invalid risk tolerance '0.02'." in result.output
    assert "Expected a value from 0 to 0.01." in result.output


def test_unknown_provider_is_rejected_before_database_access() -> None:
    result = CliRunner().invoke(
        app, ["campaign-create", "Demo", "--provider", "twilio"]
    )

    assert result.exit_code == 2
    assert "[ERROR] Unsupported provider 'twilio'." in result.output
    assert "Choose 'plivo_mock' or 'bland_mock'." in result.output
