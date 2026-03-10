"""Notebook interactive terminal flow.

Opens an interactive terminal session to a running notebook via
Jupyter terminal WebSocket — no SSH/rtunnel required.
"""

from __future__ import annotations

import sys
import time
from typing import Optional

import click

from inspire.cli.context import Context, EXIT_API_ERROR, EXIT_CONFIG_ERROR
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.notebook_cli import get_base_url, load_config, require_web_session


def run_notebook_terminal(
    ctx: Context,
    *,
    notebook_id: str,
    tmux_session: Optional[str] = None,
) -> None:
    """Open an interactive terminal to a notebook via Jupyter WebSocket."""
    from inspire.cli.commands.notebook.notebook_lookup import _resolve_notebook_id
    from inspire.platform.web import browser_api as browser_api_module

    session = require_web_session(
        ctx,
        hint="Interactive terminal requires web authentication.",
    )

    base_url = get_base_url()
    config = load_config(ctx)

    resolved_id, _nb_name = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook_id,
        json_output=False,
    )

    click.echo(f"Connecting to notebook {resolved_id}...")
    click.echo("(Press Ctrl+] to disconnect)\n")

    _open_terminal_via_playwright(
        ctx,
        notebook_id=resolved_id,
        session=session,
        tmux_session=tmux_session,
    )


def _open_terminal_via_playwright(
    ctx: Context,
    *,
    notebook_id: str,
    session,
    tmux_session: Optional[str] = None,
) -> None:
    """Launch Playwright, open notebook lab, create terminal, and proxy."""
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

    from inspire.platform.web.browser_api.core import _launch_browser, _new_context
    from inspire.platform.web.browser_api.playwright_notebooks import open_notebook_lab
    from inspire.platform.web.browser_api.rtunnel import (
        _build_jupyter_xsrf_headers,
        _build_terminal_websocket_url,
        _create_terminal_via_api,
        _delete_terminal_via_api,
    )
    from inspire.bridge.jupyter_terminal import JupyterTerminalProxy

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=True)
        context = _new_context(browser, storage_state=session.storage_state)
        page = context.new_page()

        term_name = None
        try:
            lab_frame = open_notebook_lab(page, notebook_id=notebook_id, timeout=60000)

            # Wait for Jupyter UI to settle
            try:
                lab_frame.locator("text=加载中").first.wait_for(
                    state="hidden", timeout=15000
                )
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

            # If tmux requested, send tmux command after connecting
            proxy = JupyterTerminalProxy(page, ws_url)
            proxy.connect()

            if tmux_session:
                # Small delay for shell prompt
                time.sleep(0.3)
                # Try attach first, fall back to new session
                tmux_cmd = (
                    f"tmux attach-session -t {tmux_session} 2>/dev/null "
                    f"|| tmux new-session -s {tmux_session}\r"
                )
                page.evaluate(
                    "(data) => window.__inspireTermWs.send(JSON.stringify(['stdin', data]))",
                    tmux_cmd,
                )
                time.sleep(0.3)

            proxy.run()

        except KeyboardInterrupt:
            pass
        except Exception as e:
            err_msg = str(e)
            if "Target page, context or browser has been closed" in err_msg:
                pass  # Normal exit
            else:
                click.echo(f"\nConnection error: {err_msg}", err=True)
        finally:
            click.echo("\nDisconnected.")
            if term_name:
                try:
                    _delete_terminal_via_api(
                        context, lab_url=lab_frame.url, term_name=term_name
                    )
                except Exception:
                    pass
