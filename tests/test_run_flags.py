import importlib

import pytest
from click.testing import CliRunner

from inspire.cli.context import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from inspire.cli.main import main as cli_main

run_cmd_module = importlib.import_module("inspire.cli.commands.run")


def test_run_sync_and_no_sync_mutually_exclusive() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["run", "echo hi", "--sync", "--no-sync"])
    assert result.exit_code == EXIT_VALIDATION_ERROR


def test_run_sync_if_requested_respects_no_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, int] = {"sync": 0}

    monkeypatch.setattr(run_cmd_module, "_check_uncommitted_changes", lambda: False)

    def fake_run_inspire_subcommand(args: list[str]) -> int:
        assert args == ["sync"]
        calls["sync"] += 1
        return EXIT_SUCCESS

    monkeypatch.setattr(run_cmd_module, "_run_inspire_subcommand", fake_run_inspire_subcommand)

    class FakeCtx:
        debug = False
        json_output = False

    # --watch but --no-sync should skip sync
    run_cmd_module._run_sync_if_requested(FakeCtx(), sync=False, watch=True, no_sync=True)
    assert calls["sync"] == 0

    # --watch without --no-sync should run sync
    run_cmd_module._run_sync_if_requested(FakeCtx(), sync=False, watch=True, no_sync=False)
    assert calls["sync"] == 1
