from typer.testing import CliRunner

from smart_dialer.cli import app


def test_pacing_cli_has_no_manual_answer_statistics_options() -> None:
    result = CliRunner().invoke(app, ["pacing-tick", "--help"])

    assert result.exit_code == 0
    assert "--answers" not in result.stdout
    assert "--attempts" not in result.stdout
