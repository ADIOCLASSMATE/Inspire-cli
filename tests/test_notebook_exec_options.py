import importlib

import pytest
from click.testing import CliRunner

from inspire.cli.context import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from inspire.cli.main import main as cli_main


def test_notebook_exec_passes_cwd_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_run_notebook_exec(ctx, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)

    notebook_commands_module = importlib.import_module(
        "inspire.cli.commands.notebook.notebook_commands"
    )
    monkeypatch.setattr(notebook_commands_module, "run_notebook_exec", fake_run_notebook_exec)

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "notebook",
            "exec",
            "dev-h200",
            "echo hi",
            "--cwd",
            "/workspace",
            "--env",
            "FOO=bar",
            "--env",
            "A_B=1 2",
        ],
    )

    assert result.exit_code == EXIT_SUCCESS
    assert captured["notebook_id"] == "dev-h200"
    assert captured["command"] == "echo hi"
    assert captured["cwd"] == "/workspace"
    assert captured["env_vars"] == ("FOO=bar", "A_B=1 2")


def test_notebook_exec_flow_builds_command_with_cwd_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Avoid network/auth by patching resolution functions
    from inspire.cli.commands.notebook import notebook_exec_flow as flow

    class FakeSession:
        storage_state = {}

    monkeypatch.setattr(flow, "require_web_session", lambda ctx, hint: FakeSession())
    monkeypatch.setattr(flow, "get_base_url", lambda: "https://example.invalid")
    monkeypatch.setattr(flow, "load_config", lambda ctx: object())

    def fake_resolve(ctx, **kwargs):  # type: ignore[no-untyped-def]
        return ("nb-1", "name")

    notebook_lookup_module = importlib.import_module("inspire.cli.commands.notebook.notebook_lookup")
    monkeypatch.setattr(notebook_lookup_module, "_resolve_notebook_id", fake_resolve)

    called = {}

    def fake_exec(ctx, **kwargs):  # type: ignore[no-untyped-def]
        called.update(kwargs)

    monkeypatch.setattr(flow, "_exec_via_session", fake_exec)

    class FakeCtx:
        json_output = False

    flow.run_notebook_exec(
        FakeCtx(),  # ctx is only passed through
        notebook_id="dev-h200",
        command="python -c 'print(123)'",
        use_session=True,
        cwd="/workspace",
        env_vars=("FOO=bar", "A_B=1 2"),
    )

    cmd = called["command"]
    assert "export FOO=bar" in cmd
    assert "export A_B='1 2'" in cmd
    assert "cd /workspace" in cmd
    assert cmd.endswith("&& python -c 'print(123)'")


def test_notebook_exec_flow_rejects_invalid_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from inspire.cli.commands.notebook import notebook_exec_flow as flow

    class FakeSession:
        storage_state = {}

    monkeypatch.setattr(flow, "require_web_session", lambda ctx, hint: FakeSession())
    monkeypatch.setattr(flow, "get_base_url", lambda: "https://example.invalid")
    monkeypatch.setattr(flow, "load_config", lambda ctx: object())

    def fake_resolve(ctx, **kwargs):  # type: ignore[no-untyped-def]
        return ("nb-1", "name")

    notebook_lookup_module = importlib.import_module("inspire.cli.commands.notebook.notebook_lookup")
    monkeypatch.setattr(notebook_lookup_module, "_resolve_notebook_id", fake_resolve)

    def fake_error(ctx, error_type, message, exit_code, **kwargs):  # type: ignore[no-untyped-def]
        raise SystemExit(exit_code)

    monkeypatch.setattr(flow, "_handle_error", fake_error)

    with pytest.raises(SystemExit) as e:
        class FakeCtx:
            json_output = False

        flow.run_notebook_exec(
            FakeCtx(),
            notebook_id="dev-h200",
            command="echo hi",
            use_session=False,
            env_vars=("NOT-VALID=x",),
        )

    assert e.value.code == EXIT_VALIDATION_ERROR
