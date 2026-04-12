"""Tests for the run command flags."""

from click.testing import CliRunner

from inspire.cli.main import main as cli_main


def test_run_help_shows_options() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["run", "--help"])
    assert result.exit_code == 0
    assert "--gpus" in result.output
    assert "--type" in result.output
