"""Notebook exec-session subcommands.

These commands manage the local persistent exec session daemon implemented in
`inspire.bridge.exec_session`.

They are intentionally separate from `inspire notebook exec --session` so users
can start/stop/list sessions explicitly.
"""

from __future__ import annotations

import glob
import os
import re
import shlex
from pathlib import Path

import click

from inspire.bridge.exec_session import (
    SessionClient,
    get_session_info,
    start_session_server,
    stop_session,
    wait_for_ready,
)
from inspire.cli.context import Context, EXIT_API_ERROR, EXIT_SUCCESS, EXIT_VALIDATION_ERROR, pass_context
from inspire.cli.formatters import json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.notebook_cli import (
    get_base_url,
    load_config,
    require_web_session,
    resolve_json_output,
)


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _build_init_command(*, cwd: str | None, env_vars: tuple[str, ...]) -> str:
    parts: list[str] = []

    for item in env_vars:
        if "=" not in item:
            raise ValueError(f"Invalid --env value: {item!r} (expected KEY=VAL)")
        key, value = item.split("=", 1)
        if not _ENV_KEY_RE.match(key):
            raise ValueError(f"Invalid --env key: {key!r} (must match {_ENV_KEY_RE.pattern})")
        parts.append(f"export {key}={shlex.quote(value)}")

    if cwd:
        parts.append(f"cd {shlex.quote(cwd)}")

    parts.append("pwd")
    return " && ".join(parts)


@click.group("exec-session")
def exec_session_group() -> None:
    """Manage persistent notebook exec sessions."""


@exec_session_group.command("start")
@click.argument("notebook")
@click.option(
    "--cwd",
    default=None,
    help="Working directory to cd into after session starts (default: current local directory)",
)
@click.option(
    "--env",
    "env_vars",
    multiple=True,
    help="Environment variables (repeatable, KEY=VAL) to export for the session",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def start_exec_session(
    ctx: Context, notebook: str, cwd: str | None, env_vars: tuple[str, ...], json_output: bool
) -> None:
    """Start (or reuse) a persistent exec session for a notebook.

    After the daemon is ready, this runs an initialization command to set env
    vars and `cd` into the requested directory.
    """

    from inspire.cli.commands.notebook.notebook_lookup import _resolve_notebook_id

    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(ctx, hint="Notebook exec-session requires web authentication.")
    base_url = get_base_url()
    config = load_config(ctx)

    resolved_id, _nb_name = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook,
        json_output=json_output,
    )

    info = get_session_info(resolved_id)
    started_new = False

    if not info:
        pid = start_session_server(resolved_id, session)
        if pid <= 0:
            _handle_error(ctx, "ExecSessionError", "Failed to start exec session", EXIT_API_ERROR)
        started_new = True

        if not wait_for_ready(resolved_id, timeout=30.0):
            _handle_error(ctx, "ExecSessionError", "Exec session startup timed out", EXIT_API_ERROR)

    init_cwd = cwd or os.getcwd()

    try:
        init_cmd = _build_init_command(cwd=init_cwd, env_vars=env_vars)
    except ValueError as e:
        _handle_error(ctx, "ValidationError", str(e), EXIT_VALIDATION_ERROR)

    client = SessionClient(resolved_id)
    if not client.connect():
        client.close()
        _handle_error(ctx, "ExecSessionError", "Exec session not reachable", EXIT_API_ERROR)

    try:
        output, exit_code = client.exec_command(init_cmd, timeout=30, on_output=None)
    finally:
        client.close()

    if exit_code != 0:
        _handle_error(
            ctx,
            "ExecSessionError",
            f"Session initialization failed (exit_code={exit_code})",
            EXIT_API_ERROR,
            hint=output,
        )

    payload = {
        "notebook_id": resolved_id,
        "started_new": started_new,
        "cwd": init_cwd,
        "env": list(env_vars),
        "init_command": init_cmd,
        "init_output": output,
    }

    if json_output:
        click.echo(json_formatter.format_json(payload))
    else:
        click.echo(
            f"Exec session {'started' if started_new else 'reused'}: {resolved_id}\n"
            f"CWD: {init_cwd}"
        )

    raise SystemExit(EXIT_SUCCESS)


@exec_session_group.command("stop")
@click.argument("notebook")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def stop_exec_session(ctx: Context, notebook: str, json_output: bool) -> None:
    """Stop a persistent exec session for a notebook."""

    from inspire.cli.commands.notebook.notebook_lookup import _resolve_notebook_id

    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(ctx, hint="Notebook exec-session requires web authentication.")
    base_url = get_base_url()
    config = load_config(ctx)

    resolved_id, _nb_name = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook,
        json_output=json_output,
    )

    stop_session(resolved_id)

    payload = {"notebook_id": resolved_id, "stopped": True}
    if json_output:
        click.echo(json_formatter.format_json(payload))
    else:
        click.echo(f"Exec session stopped: {resolved_id}")


@exec_session_group.command("list")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def list_exec_sessions(ctx: Context, json_output: bool) -> None:
    """List local exec sessions on this machine."""

    json_output = resolve_json_output(ctx, json_output)

    sock_paths = sorted(glob.glob("/tmp/inspire-exec-sessions/*.sock"))

    sessions: list[dict] = []
    for sp in sock_paths:
        notebook_id = Path(sp).stem
        info = get_session_info(notebook_id)
        if not info:
            continue
        sessions.append(
            {
                "notebook_id": info.notebook_id,
                "pid": info.pid,
                "socket": str(info.socket_path),
            }
        )

    if json_output:
        click.echo(json_formatter.format_json({"sessions": sessions}))
        return

    if not sessions:
        click.echo("No exec sessions running.")
        return

    for s in sessions:
        click.echo(f"{s['notebook_id']}\tpid={s['pid']}\tsock={s['socket']}")


__all__ = ["exec_session_group"]
