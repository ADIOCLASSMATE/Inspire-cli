"""Interactive Jupyter terminal proxy over WebSocket.

Provides an SSH-like interactive terminal experience by connecting to
a notebook's Jupyter terminal via WebSocket through Playwright's browser
context (which handles platform authentication transparently).

The proxy sets the local terminal to raw mode and bidirectionally relays
data between stdin/stdout and the remote Jupyter terminal WebSocket.
"""

from __future__ import annotations

import json
import os
import select
import signal
import sys
import time
from typing import Any, Optional

try:
    import termios
    import tty

    _HAS_TTY = True
except ImportError:
    _HAS_TTY = False


# JS snippet that creates a WebSocket, buffers stdout, and exposes helpers.
_WS_SETUP_JS = """
(wsUrl) => {
    return new Promise((resolve, reject) => {
        const ws = new WebSocket(wsUrl);
        window.__inspireTermWs = ws;
        window.__inspireTermBuf = '';
        window.__inspireTermClosed = false;
        ws.onopen = () => resolve(true);
        ws.onerror = (e) => reject(new Error('WebSocket error: ' + e.type));
        ws.onclose = () => { window.__inspireTermClosed = true; };
        ws.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                if (msg[0] === 'stdout') {
                    window.__inspireTermBuf += msg[1];
                }
            } catch (_) {}
        };
        setTimeout(() => reject(new Error('WebSocket open timeout')), 10000);
    });
}
"""

_WS_READ_JS = """
() => {
    const buf = window.__inspireTermBuf || '';
    window.__inspireTermBuf = '';
    return buf;
}
"""

_WS_SEND_JS = """
(data) => {
    if (window.__inspireTermWs && window.__inspireTermWs.readyState === 1) {
        window.__inspireTermWs.send(JSON.stringify(["stdin", data]));
        return true;
    }
    return false;
}
"""

_WS_RESIZE_JS = """
(dims) => {
    if (window.__inspireTermWs && window.__inspireTermWs.readyState === 1) {
        window.__inspireTermWs.send(JSON.stringify(["set_size", dims[0], dims[1]]));
        return true;
    }
    return false;
}
"""

_WS_CLOSED_JS = "() => window.__inspireTermClosed === true"

_WS_CLOSE_JS = """
() => {
    if (window.__inspireTermWs) {
        try {
            window.__inspireTermWs.close();
        } catch (e) {}
        window.__inspireTermWs = null;
    }
    window.__inspireTermBuf = '';
    window.__inspireTermClosed = true;
}
"""


class JupyterTerminalProxy:
    """Bidirectional proxy between local terminal and Jupyter terminal WebSocket.

    Uses Playwright's page.evaluate() to manage a browser-side WebSocket,
    which inherits the platform's session authentication automatically.
    """

    def __init__(
        self,
        page: Any,
        ws_url: str,
        *,
        poll_interval: float = 0.02,
    ):
        self.page = page
        self.ws_url = ws_url
        self.poll_interval = poll_interval
        self.running = False
        self._old_tty: Optional[list] = None

    def connect(self) -> None:
        """Open the browser-side WebSocket connection."""
        self.page.evaluate(_WS_SETUP_JS, self.ws_url)

    def run(self) -> None:
        """Run the interactive terminal proxy until disconnected or Ctrl+]."""
        if not sys.stdin.isatty():
            raise RuntimeError("stdin is not a terminal")

        self.running = True

        # Save tty and switch to raw mode
        if _HAS_TTY:
            self._old_tty = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin.fileno())

        # Handle window resize
        old_sigwinch = None
        try:
            old_sigwinch = signal.signal(signal.SIGWINCH, self._on_resize)
        except (OSError, ValueError):
            pass

        # Send initial terminal size
        self._send_resize()

        try:
            self._proxy_loop()
        finally:
            self.running = False
            if _HAS_TTY and self._old_tty is not None:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_tty)
            if old_sigwinch is not None:
                try:
                    signal.signal(signal.SIGWINCH, old_sigwinch)
                except (OSError, ValueError):
                    pass
            try:
                self.page.evaluate(_WS_CLOSE_JS)
            except Exception:
                pass

    def _proxy_loop(self) -> None:
        """Main loop: relay stdin <-> WebSocket stdout."""
        stdin_fd = sys.stdin.fileno()
        stdout_fd = sys.stdout.fileno()

        while self.running:
            # --- Read from WebSocket (buffered in browser JS) ---
            try:
                output = self.page.evaluate(_WS_READ_JS)
            except Exception:
                break

            if output:
                os.write(stdout_fd, output.encode("utf-8", errors="replace"))

            # --- Check if WebSocket closed ---
            try:
                if self.page.evaluate(_WS_CLOSED_JS):
                    break
            except Exception:
                break

            # --- Read from local stdin (non-blocking) ---
            try:
                readable, _, _ = select.select([stdin_fd], [], [], self.poll_interval)
            except (ValueError, OSError):
                break

            if readable:
                try:
                    data = os.read(stdin_fd, 4096)
                except OSError:
                    break

                if not data:
                    break

                # Ctrl+] (0x1d) = disconnect
                if b"\x1d" in data:
                    break

                text = data.decode("utf-8", errors="replace")
                try:
                    ok = self.page.evaluate(_WS_SEND_JS, text)
                    if not ok:
                        break
                except Exception:
                    break

    def _on_resize(self, signum: int, frame: Any) -> None:
        self._send_resize()

    def _send_resize(self) -> None:
        try:
            cols, rows = os.get_terminal_size()
            self.page.evaluate(_WS_RESIZE_JS, [rows, cols])
        except Exception:
            pass
