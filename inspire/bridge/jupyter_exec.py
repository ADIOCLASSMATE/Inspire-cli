"""Non-interactive command execution on a Jupyter terminal via WebSocket.

Connects to a notebook's Jupyter terminal WebSocket (through Playwright),
sends a command, streams output, and captures the exit code via a sentinel.

Disables terminal echo (``stty -echo``) so the echoed command text does not
mix into the captured output — only actual command output is streamed.

Parallel to ``jupyter_terminal.py`` which provides interactive TTY proxy.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .jupyter_terminal import (
    _WS_CLOSED_JS,
    _WS_CLOSE_JS,
    _WS_READ_JS,
    _WS_SEND_JS,
    _WS_SETUP_JS,
)

_EXIT_SENTINEL = "__INSPIRE_EXEC_EXIT__"
_EXIT_RE = re.compile(r"__INSPIRE_EXEC_EXIT__=(-?\d+)")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

# Interval between WebSocket read polls (seconds).
_POLL_INTERVAL = 0.05

# How long to wait for an initial shell prompt before giving up (seconds).
_PROMPT_TIMEOUT = 15.0


@dataclass
class ExecResult:
    """Result of a non-interactive command execution."""

    output: str
    exit_code: int


def exec_in_jupyter_terminal(
    page: Any,
    ws_url: str,
    command: str,
    *,
    timeout_s: float = 120.0,
    on_output: Optional[Callable[[str], Any]] = None,
) -> ExecResult:
    """Execute *command* on a Jupyter terminal and return the captured output.

    Parameters
    ----------
    page:
        Playwright Page with access to the notebook's JupyterLab context.
    ws_url:
        WebSocket URL for the Jupyter terminal (from ``_build_terminal_websocket_url``).
    command:
        Shell command to execute.
    timeout_s:
        Maximum wall-clock seconds to wait for the command to finish.
    on_output:
        Optional callback invoked with each chunk of output as it arrives.
        Pass ``sys.stdout.write`` for real-time streaming.

    Returns
    -------
    ExecResult
        Accumulated output and the command's exit code (``-1`` on timeout).
    """
    # 1. Connect WebSocket
    page.evaluate(_WS_SETUP_JS, ws_url)

    try:
        # 2. Wait for shell prompt
        _wait_for_prompt(page)

        # 3. Drain prompt buffer
        page.evaluate(_WS_READ_JS)

        # 4. Disable terminal echo so the command text is not mixed
        #    into the output.  The stty command itself is echoed (echo
        #    is still on), so we drain after a short pause.
        page.evaluate(_WS_SEND_JS, "stty -echo\r")
        time.sleep(0.3)
        page.evaluate(_WS_READ_JS)  # drain stty echo

        # 5. Send the actual command with an exit-code sentinel.
        #    Since echo is off, only the command's real stdout/stderr
        #    and the sentinel printf will appear in the WS output.
        wrapped = (
            f"( {command} ); "
            f"printf '\\n{_EXIT_SENTINEL}=%d\\n' \"$?\""
            f"\r"
        )
        ok = page.evaluate(_WS_SEND_JS, wrapped)
        if not ok:
            return ExecResult(output="", exit_code=-1)

        # 6. Poll for output and watch for the sentinel
        accumulated: list[str] = []
        deadline = time.monotonic() + timeout_s
        found_exit_code: Optional[int] = None

        while time.monotonic() < deadline:
            # Check WS health
            try:
                if page.evaluate(_WS_CLOSED_JS):
                    break
            except Exception:
                break

            # Read buffered output
            try:
                chunk = page.evaluate(_WS_READ_JS)
            except Exception:
                break

            if not chunk:
                time.sleep(_POLL_INTERVAL)
                continue

            # Check for exit sentinel
            exit_match = _EXIT_RE.search(chunk)
            if exit_match:
                found_exit_code = int(exit_match.group(1))
                # Keep text before the sentinel
                pre = chunk[: exit_match.start()]
                if pre.endswith("\n"):
                    pre = pre[:-1]
                if pre:
                    clean = _strip_ansi(pre)
                    if clean:
                        accumulated.append(clean)
                        if on_output:
                            on_output(clean)
                break

            clean = _strip_ansi(chunk)
            if clean:
                accumulated.append(clean)
                if on_output:
                    on_output(clean)

            time.sleep(_POLL_INTERVAL)

        output_text = "".join(accumulated)
        # Strip leading/trailing blank lines (terminal artifacts)
        output_text = output_text.strip("\n")

        if found_exit_code is not None:
            return ExecResult(output=output_text, exit_code=found_exit_code)

        # Timeout
        return ExecResult(output=output_text, exit_code=-1)

    finally:
        try:
            page.evaluate(_WS_CLOSE_JS)
        except Exception:
            pass


def _wait_for_prompt(page: Any) -> None:
    """Poll until the shell prompt appears or timeout."""
    deadline = time.monotonic() + _PROMPT_TIMEOUT
    while time.monotonic() < deadline:
        try:
            buf = page.evaluate(_WS_READ_JS)
        except Exception:
            break
        if buf:
            stripped = buf.rstrip()
            if stripped.endswith(("$", "#", "%")):
                return
        time.sleep(_POLL_INTERVAL)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and carriage returns from text."""
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "")
    return text
