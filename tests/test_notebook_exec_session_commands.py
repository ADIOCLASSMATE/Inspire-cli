import importlib

import pytest
from click.testing import CliRunner

from inspire.cli.context import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from inspire.cli.main import main as cli_main


def test_notebook_exec_session_start_stop_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch auth + notebook id resolution to avoid network
    from inspire.cli.commands.notebook import notebook_exec_session_commands as mod

    class FakeSession:
        storage_state = {}

    monkeypatch.setattr(mod, "require_web_session", lambda ctx, hint: FakeSession())
    monkeypatch.setattr(mod, "get_base_url", lambda: "https://example.invalid")
    monkeypatch.setattr(mod, "load_config", lambda ctx: object())

    def fake_resolve(ctx, **kwargs):  # type: ignore[no-untyped-def]
        return ("nb-1", "name")

    notebook_lookup_module = importlib.import_module("inspire.cli.commands.notebook.notebook_lookup")
    monkeypatch.setattr(notebook_lookup_module, "_resolve_notebook_id", fake_resolve)

    monkeypatch.setattr(mod, "get_session_info", lambda notebook_id: None)

    started = {}

    def fake_start_session_server(notebook_id, session):  # type: ignore[no-untyped-def]
        started["notebook_id"] = notebook_id
        return 12345

    monkeypatch.setattr(mod, "start_session_server", fake_start_session_server)
    monkeypatch.setattr(mod, "wait_for_ready", lambda notebook_id, timeout=30.0: True)

    class FakeClient:
        def __init__(self, notebook_id):  # type: ignore[no-untyped-def]
            self.notebook_id = notebook_id

        def connect(self):  # type: ignore[no-untyped-def]
            return True

        def close(self):  # type: ignore[no-untyped-def]
            return None

        def exec_command(self, command, timeout=30, on_output=None):  # type: ignore[no-untyped-def]
            assert "cd" in command
            assert "pwd" in command
            return ("/workspace\n", 0)

    monkeypatch.setattr(mod, "SessionClient", FakeClient)

    runner = CliRunner()

    # start
    result = runner.invoke(
        cli_main, ["notebook", "exec-session", "start", "dev-h200", "--cwd", "/workspace"]
    )
    assert result.exit_code == EXIT_SUCCESS
    assert started["notebook_id"] == "nb-1"

    # stop
    stopped = {}

    def fake_stop_session(notebook_id):  # type: ignore[no-untyped-def]
        stopped["notebook_id"] = notebook_id

    monkeypatch.setattr(mod, "stop_session", fake_stop_session)

    result2 = runner.invoke(cli_main, ["notebook", "exec-session", "stop", "dev-h200"])
    assert result2.exit_code == EXIT_SUCCESS
    assert stopped["notebook_id"] == "nb-1"


def test_notebook_exec_session_start_rejects_bad_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from inspire.cli.commands.notebook import notebook_exec_session_commands as mod

    class FakeSession:
        storage_state = {}

    monkeypatch.setattr(mod, "require_web_session", lambda ctx, hint: FakeSession())
    monkeypatch.setattr(mod, "get_base_url", lambda: "https://example.invalid")
    monkeypatch.setattr(mod, "load_config", lambda ctx: object())

    def fake_resolve(ctx, **kwargs):  # type: ignore[no-untyped-def]
        return ("nb-1", "name")

    notebook_lookup_module = importlib.import_module("inspire.cli.commands.notebook.notebook_lookup")
    monkeypatch.setattr(notebook_lookup_module, "_resolve_notebook_id", fake_resolve)

    # session already running, skip start_session_server
    monkeypatch.setattr(mod, "get_session_info", lambda notebook_id: object())

    def fake_error(ctx, error_type, message, exit_code, **kwargs):  # type: ignore[no-untyped-def]
        raise SystemExit(exit_code)

    monkeypatch.setattr(mod, "_handle_error", fake_error)

    runner = CliRunner()
    res = runner.invoke(
        cli_main,
        [
            "notebook",
            "exec-session",
            "start",
            "dev-h200",
            "--env",
            "NOT-VALID=x",
        ],
    )
    assert res.exit_code == EXIT_VALIDATION_ERROR
