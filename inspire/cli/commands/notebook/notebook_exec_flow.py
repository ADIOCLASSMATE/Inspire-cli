"""Notebook non-interactive exec flow.

Connects to a running notebook via Playwright + Jupyter terminal WebSocket,
executes a command, streams output, and returns the exit code.

Supports ``--session`` mode which keeps the Playwright browser and terminal
connection alive in a background daemon for near-instant subsequent commands.
"""

from __future__ import annotations

import re
import shlex
import sys
from typing import Any

import click

from inspire.cli.context import Context, EXIT_API_ERROR, EXIT_CONFIG_ERROR, EXIT_VALIDATION_ERROR
from inspire.cli.formatters import json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.notebook_cli import get_base_url, load_config, require_web_session


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _build_exec_command(
    ctx: Context,
    *,
    command: str,
    cwd: str | None,
    env_vars: tuple[str, ...],
) -> str:
    """Build a single shell command applying --env/--cwd before the user command."""
    if not cwd and not env_vars:
        return command

    exports: list[str] = []
    for item in env_vars:
        if "=" not in item:
            _handle_error(
                ctx,
                "ValidationError",
                f"Invalid --env value: {item!r} (expected KEY=VAL)",
                EXIT_VALIDATION_ERROR,
            )
            raise SystemExit(EXIT_VALIDATION_ERROR)
        key, value = item.split("=", 1)
        if not _ENV_KEY_RE.match(key):
            _handle_error(
                ctx,
                "ValidationError",
                f"Invalid --env key: {key!r} (must match {_ENV_KEY_RE.pattern})",
                EXIT_VALIDATION_ERROR,
            )
            raise SystemExit(EXIT_VALIDATION_ERROR)
        exports.append(f"export {key}={shlex.quote(value)}")

    prefix_parts: list[str] = []
    if exports:
        prefix_parts.append(" && ".join(exports))
    if cwd:
        prefix_parts.append(f"cd {shlex.quote(cwd)}")

    prefix = " && ".join(prefix_parts)
    return f"{prefix} && {command}" if prefix else command


def run_notebook_exec(
    ctx: Context,
    *,
    notebook_id: str,
    command: str,
    timeout: int = 120,
    json_output: bool = False,
    use_session: bool = False,
    cwd: str | None = None,
    env_vars: tuple[str, ...] = (),
) -> None:
    """Execute *command* on a notebook and stream output to stdout."""
    from inspire.cli.commands.notebook.notebook_lookup import _resolve_notebook_id
    from inspire.cli.utils.notebook_cli import resolve_json_output

    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint="Notebook exec requires web authentication.",
    )

    base_url = get_base_url()
    config = load_config(ctx)

    resolved_id, _nb_name = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook_id,
        json_output=json_output,
    )

    command = _build_exec_command(ctx, command=command, cwd=cwd, env_vars=env_vars)

    if use_session:
        _exec_via_session(
            ctx,
            notebook_id=resolved_id,
            session=session,
            command=command,
            timeout=timeout,
            json_output=json_output,
        )
    else:
        if not json_output:
            click.echo(f"Connecting to notebook {resolved_id}...", err=True)

        _exec_via_playwright(
            ctx,
            notebook_id=resolved_id,
            session=session,
            command=command,
            timeout=timeout,
            json_output=json_output,
        )


def _exec_via_session(
    ctx: Context,
    *,
    notebook_id: str,
    session,
    command: str,
    timeout: int,
    json_output: bool,
) -> None:
    """Execute command via persistent session (fast path)."""
    from inspire.bridge.exec_session import (
        SessionClient,
        get_session_info,
        start_session_server,
        wait_for_ready,
    )

    info = get_session_info(notebook_id)
    started_new = False

    if not info:
        if not json_output:
            click.echo(f"Starting session for {notebook_id}...", err=True)
        pid = start_session_server(notebook_id, session)
        if pid <= 0:
            if not json_output:
                click.echo("Session start failed, falling back to direct exec.", err=True)
            _exec_via_playwright(
                ctx,
                notebook_id=notebook_id,
                session=session,
                command=command,
                timeout=timeout,
                json_output=json_output,
            )
            return
        started_new = True
        # Wait for daemon to finish Playwright setup (up to 30s).
        if not wait_for_ready(notebook_id, timeout=30.0):
            if not json_output:
                click.echo("Session startup timed out, falling back to direct exec.", err=True)
            _exec_via_playwright(
                ctx,
                notebook_id=notebook_id,
                session=session,
                command=command,
                timeout=timeout,
                json_output=json_output,
            )
            return

    # Connect to session.
    client = SessionClient(notebook_id)
    if not client.connect():
        client.close()
        if not json_output:
            click.echo("Session not reachable, falling back to direct exec.", err=True)
        _exec_via_playwright(
            ctx,
            notebook_id=notebook_id,
            session=session,
            command=command,
            timeout=timeout,
            json_output=json_output,
        )
        return

    try:
        if not json_output and not started_new:
            click.echo(f"Reusing session for {notebook_id}.", err=True)

        if json_output:
            output, exit_code = client.exec_command(command, timeout, on_output=None)
        else:
            output, exit_code = client.exec_command(
                command, timeout, on_output=lambda chunk: sys.stdout.write(chunk)
            )

        if json_output:
            click.echo(
                json_formatter.format_json(
                    {
                        "notebook_id": notebook_id,
                        "command": command,
                        "output": output,
                        "exit_code": exit_code,
                    }
                )
            )
        else:
            if output and not output.endswith("\n"):
                sys.stdout.write("\n")
            if exit_code == -1:
                click.echo(f"Command timed out after {timeout}s", err=True)

        sys.exit(exit_code)

    except KeyboardInterrupt:
        click.echo("\nInterrupted.", err=True)
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        _handle_error(ctx, "ExecError", f"Session exec failed: {e}", EXIT_API_ERROR)
    finally:
        client.close()


def _exec_via_playwright(
    ctx: Context,
    *,
    notebook_id: str,
    session,
    command: str,
    timeout: int,
    json_output: bool,
) -> None:
    """Launch Playwright, open notebook lab, create terminal, exec command."""
    from inspire.platform.web.browser_api.core import _in_asyncio_loop, _run_in_thread

    if _in_asyncio_loop():
        return _run_in_thread(
            _exec_via_playwright_sync,
            ctx,
            notebook_id=notebook_id,
            session=session,
            command=command,
            timeout=timeout,
            json_output=json_output,
        )

    return _exec_via_playwright_sync(
        ctx,
        notebook_id=notebook_id,
        session=session,
        command=command,
        timeout=timeout,
        json_output=json_output,
    )


def _exec_via_playwright_sync(
    ctx: Context,
    *,
    notebook_id: str,
    session,
    command: str,
    timeout: int,
    json_output: bool,
) -> None:
    """Sync Playwright implementation for notebook exec."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _handle_error(
            ctx,
            "DependencyError",
            "Playwright is required. Install with: uv pip install playwright && playwright install chromium",
            EXIT_CONFIG_ERROR,
        )
        return

    from inspire.bridge.jupyter_exec import exec_in_jupyter_terminal
    from inspire.platform.web.browser_api.core import _launch_browser, _new_context
    from inspire.platform.web.browser_api.playwright_notebooks import open_notebook_lab
    from inspire.platform.web.browser_api.rtunnel import (
        _build_terminal_websocket_url,
        _create_terminal_via_api,
        _delete_terminal_via_api,
    )

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=True)
        context = _new_context(browser, storage_state=session.storage_state)
        page = context.new_page()

        term_name = None
        lab_frame = None
        try:
            lab_frame = open_notebook_lab(page, notebook_id=notebook_id, timeout=60000)

            # Wait for Jupyter UI to settle
            try:
                lab_frame.locator("text=加载中").first.wait_for(state="hidden", timeout=15000)
            except Exception:
                pass

            # Create terminal via REST API
            term_name = _create_terminal_via_api(context, lab_frame.url)
            if not term_name:
                _handle_error(
                    ctx,
                    "APIError",
                    "Failed to create Jupyter terminal.",
                    EXIT_API_ERROR,
                    hint="Check that the notebook is running and accessible.",
                )
                return

            # Build WebSocket URL
            ws_url = _build_terminal_websocket_url(lab_frame.url, term_name)

            # Execute command
            if json_output:
                result = exec_in_jupyter_terminal(
                    page,
                    ws_url,
                    command,
                    timeout_s=timeout,
                    on_output=None,
                )
            else:
                result = exec_in_jupyter_terminal(
                    page,
                    ws_url,
                    command,
                    timeout_s=timeout,
                    on_output=lambda chunk: sys.stdout.write(chunk),
                )

            if json_output:
                click.echo(
                    json_formatter.format_json(
                        {
                            "notebook_id": notebook_id,
                            "command": command,
                            "output": result.output,
                            "exit_code": result.exit_code,
                        }
                    )
                )
            else:
                if result.output and not result.output.endswith("\n"):
                    sys.stdout.write("\n")
                if result.exit_code == -1:
                    click.echo(f"Command timed out after {timeout}s", err=True)

            sys.exit(result.exit_code)

        except KeyboardInterrupt:
            click.echo("\nInterrupted.", err=True)
            sys.exit(130)
        except SystemExit:
            raise
        except Exception as e:
            err_msg = str(e)
            if "Target page, context or browser has been closed" in err_msg:
                pass
            else:
                _handle_error(
                    ctx,
                    "ExecError",
                    f"Execution failed: {err_msg}",
                    EXIT_API_ERROR,
                )
        finally:
            if term_name and lab_frame:
                try:
                    _delete_terminal_via_api(context, lab_url=lab_frame.url, term_name=term_name)
                except Exception:
                    pass
