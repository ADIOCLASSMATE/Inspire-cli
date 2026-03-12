"""Persistent notebook exec session server.

Maintains a Playwright browser and Jupyter terminal for fast repeated
command execution.  Communicates via Unix domain socket.

Architecture
~~~~~~~~~~~~
- **Server** runs as a background subprocess holding Playwright open.
  For each command it creates a fresh WebSocket to the persistent
  Jupyter terminal, executes, and tears down the WebSocket — only the
  expensive browser + lab navigation is amortised.
- **Client** connects via Unix socket, sends a JSON request, and reads
  a stream of JSON-line responses (chunks + final result).
- One session per notebook (socket path includes notebook ID).
- Auto-shutdown after idle timeout (default 15 min).
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

_log = logging.getLogger("inspire.bridge.exec_session")

# Session idle timeout in seconds (15 minutes).
_IDLE_TIMEOUT = 900

# Where sockets live.
_SOCKET_DIR = Path(tempfile.gettempdir()) / "inspire-exec-sessions"

# ------------------------------------------------------------------
# Wire protocol — JSON lines over a Unix stream socket.
# Each message is a single JSON object terminated by ``\n``.
# ------------------------------------------------------------------
_MSG_EXEC = "exec"
_MSG_PING = "ping"
_MSG_SHUTDOWN = "shutdown"
_MSG_CHUNK = "chunk"
_MSG_RESULT = "result"
_MSG_ERROR = "error"
_MSG_PONG = "pong"

# Marker written to a temp file once the server is ready.
_READY_SENTINEL = "READY"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _socket_path(notebook_id: str) -> Path:
    safe = notebook_id.replace("/", "_").replace("\\", "_")
    return _SOCKET_DIR / f"{safe}.sock"


def _pid_path(notebook_id: str) -> Path:
    safe = notebook_id.replace("/", "_").replace("\\", "_")
    return _SOCKET_DIR / f"{safe}.pid"


def _ready_path(notebook_id: str) -> Path:
    safe = notebook_id.replace("/", "_").replace("\\", "_")
    return _SOCKET_DIR / f"{safe}.ready"


def _cleanup_stale(notebook_id: str) -> None:
    for p in (_socket_path(notebook_id), _pid_path(notebook_id), _ready_path(notebook_id)):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


# ------------------------------------------------------------------
# Public: check / start / stop sessions
# ------------------------------------------------------------------
@dataclass
class SessionInfo:
    notebook_id: str
    socket_path: Path
    pid: int


def get_session_info(notebook_id: str) -> Optional[SessionInfo]:
    """Return info about a live session, or ``None``."""
    sp = _socket_path(notebook_id)
    pp = _pid_path(notebook_id)
    if not sp.exists() or not pp.exists():
        return None
    try:
        pid = int(pp.read_text().strip())
        os.kill(pid, 0)  # alive?
        return SessionInfo(notebook_id=notebook_id, socket_path=sp, pid=pid)
    except (ValueError, OSError, ProcessLookupError):
        _cleanup_stale(notebook_id)
        return None


def stop_session(notebook_id: str) -> None:
    """Gracefully stop a running session."""
    info = get_session_info(notebook_id)
    if not info:
        return
    try:
        os.kill(info.pid, signal.SIGTERM)
    except OSError:
        pass
    _cleanup_stale(notebook_id)


# ------------------------------------------------------------------
# Client
# ------------------------------------------------------------------
class SessionClient:
    """Thin client that talks to a running session server."""

    def __init__(self, notebook_id: str):
        self.notebook_id = notebook_id
        self._sock: Optional[socket.socket] = None
        self._buf = b""

    # -- lifecycle --
    def connect(self) -> bool:
        sp = _socket_path(self.notebook_id)
        if not sp.exists():
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(str(sp))
            s.settimeout(5.0)
            self._sock = s
            self._buf = b""
            return True
        except (OSError, ConnectionRefusedError):
            return False

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # -- operations --
    def ping(self) -> bool:
        try:
            self._send({_MSG_PING: True})
            r = self._recv()
            return bool(r and r.get(_MSG_PONG))
        except Exception:
            return False

    def exec_command(
        self,
        command: str,
        timeout: int = 120,
        on_output: Optional[Callable[[str], Any]] = None,
    ) -> tuple[str, int]:
        if not self._sock:
            raise RuntimeError("Not connected")
        self._sock.settimeout(timeout + 30)
        self._send({_MSG_EXEC: command, "timeout": timeout})

        parts: list[str] = []
        while True:
            msg = self._recv()
            if msg is None:
                return "".join(parts), -1
            if _MSG_CHUNK in msg:
                chunk = msg[_MSG_CHUNK]
                parts.append(chunk)
                if on_output:
                    on_output(chunk)
            elif _MSG_RESULT in msg:
                return msg.get("output", "".join(parts)), msg.get("exit_code", -1)
            elif _MSG_ERROR in msg:
                return msg[_MSG_ERROR], -1

    def shutdown(self) -> None:
        try:
            self._send({_MSG_SHUTDOWN: True})
        except Exception:
            pass

    # -- wire helpers --
    def _send(self, obj: dict) -> None:
        assert self._sock
        self._sock.sendall(json.dumps(obj, ensure_ascii=False).encode() + b"\n")

    def _recv(self) -> Optional[dict]:
        """Read one JSON-line from the socket (handles partial reads)."""
        assert self._sock
        while b"\n" not in self._buf:
            try:
                chunk = self._sock.recv(65536)
            except Exception:
                return None
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)


# ------------------------------------------------------------------
# Server
# ------------------------------------------------------------------
class SessionServer:
    """Background daemon that holds a Playwright browser + Jupyter terminal."""

    def __init__(self, notebook_id: str, storage_state, idle_timeout: int = _IDLE_TIMEOUT):
        self.notebook_id = notebook_id
        # storage_state can be a dict or a file path.
        self.storage_state = storage_state
        self.idle_timeout = idle_timeout

        self._running = False
        self._last_activity = time.monotonic()

        # Playwright resources (set in _setup).
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lab_url: Optional[str] = None
        self._term_name: Optional[str] = None
        self._ws_url: Optional[str] = None

    # -- main entry point (called in subprocess) --
    def run(self) -> None:
        _SOCKET_DIR.mkdir(parents=True, exist_ok=True)

        sp = _socket_path(self.notebook_id)
        pp = _pid_path(self.notebook_id)
        rp = _ready_path(self.notebook_id)
        sp.unlink(missing_ok=True)
        rp.unlink(missing_ok=True)
        pp.write_text(str(os.getpid()))

        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "_running", False))

        if not self._setup():
            _cleanup_stale(self.notebook_id)
            sys.exit(1)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(sp))
        sock.listen(5)
        sock.settimeout(1.0)

        # Signal readiness.
        rp.write_text(_READY_SENTINEL)

        self._running = True
        self._last_activity = time.monotonic()
        _log.debug("main loop: starting")
        try:
            while self._running:
                if time.monotonic() - self._last_activity > self.idle_timeout:
                    _log.debug("main loop: idle timeout")
                    break
                try:
                    conn, _ = sock.accept()
                    self._last_activity = time.monotonic()
                    _log.debug("main loop: accepted connection")
                    # Handle inline — Playwright requires same-thread access.
                    self._serve(conn)
                    _log.debug("main loop: serve returned, continuing")
                except socket.timeout:
                    continue
                except Exception:
                    _log.exception("main loop: accept error")
                    break
        except Exception:
            _log.exception("main loop: outer error")
        finally:
            _log.debug("main loop: exiting")
            sock.close()
            self._teardown()

    # -- playwright setup --
    def _setup(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright

            from inspire.platform.web.browser_api.core import _launch_browser, _new_context
            from inspire.platform.web.browser_api.playwright_notebooks import open_notebook_lab
            from inspire.platform.web.browser_api.rtunnel import (
                _build_terminal_websocket_url,
                _create_terminal_via_api,
            )

            _log.debug("_setup: starting playwright")
            self._playwright = sync_playwright().start()
            self._browser = _launch_browser(self._playwright, headless=True)
            self._context = _new_context(self._browser, storage_state=self.storage_state)
            self._page = self._context.new_page()

            _log.debug("_setup: opening notebook lab")
            lab_frame = open_notebook_lab(self._page, notebook_id=self.notebook_id, timeout=60000)
            try:
                lab_frame.locator("text=加载中").first.wait_for(state="hidden", timeout=15000)
            except Exception:
                pass

            self._lab_url = lab_frame.url
            _log.debug("_setup: lab_url=%s", self._lab_url)

            _log.debug("_setup: creating terminal")
            self._term_name = _create_terminal_via_api(self._context, self._lab_url)
            if not self._term_name:
                _log.error("_setup: terminal creation failed")
                return False
            self._ws_url = _build_terminal_websocket_url(self._lab_url, self._term_name)
            _log.debug("_setup: term=%s ws_url=%s", self._term_name, self._ws_url)
            return True
        except Exception:
            _log.exception("_setup: failed")
            return False

    # -- per-client handler --
    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(300)
        buf = b""
        try:
            # Read one request line.
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    _log.debug("_serve: client disconnected before sending message")
                    return
                buf += chunk
            line, _ = buf.split(b"\n", 1)
            _log.debug("_serve: received %s", line[:200])
            msg = json.loads(line)

            if _MSG_PING in msg:
                _log.debug("_serve: handling ping")
                self._reply(conn, {_MSG_PONG: True})
            elif _MSG_SHUTDOWN in msg:
                _log.debug("_serve: handling shutdown")
                self._running = False
            elif _MSG_EXEC in msg:
                _log.debug("_serve: handling exec: %s", msg[_MSG_EXEC][:100])
                self._do_exec(conn, msg[_MSG_EXEC], msg.get("timeout", 120))
        except Exception:
            _log.exception("_serve: error")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # -- command execution (fresh WS each time) --
    def _do_exec(self, conn: socket.socket, command: str, timeout: int) -> None:
        """Execute a command using a fresh WebSocket to the persistent terminal.

        Optimised for the session use case: sends ``\\r`` to trigger a prompt
        instead of relying on ``_wait_for_prompt``'s 15-second timeout (the
        terminal already has a shell running).
        """
        import re

        from inspire.bridge.jupyter_exec import _ANSI_RE, _EXIT_RE, _EXIT_SENTINEL, _POLL_INTERVAL
        from inspire.bridge.jupyter_terminal import (
            _WS_CLOSE_JS,
            _WS_CLOSED_JS,
            _WS_READ_JS,
            _WS_SEND_JS,
            _WS_SETUP_JS,
        )

        _log.debug("_do_exec: command=%r timeout=%d", command, timeout)

        try:
            # 1. Ensure previous WebSocket is fully closed.
            try:
                self._page.evaluate(_WS_CLOSE_JS)
                time.sleep(0.5)
            except Exception:
                pass

            # 2. Open a fresh WebSocket with retry.
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self._page.evaluate(_WS_SETUP_JS, self._ws_url)
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        _log.warning("_do_exec: WebSocket connection attempt %d failed, retrying...", attempt + 1)
                        time.sleep(1.0)
                    else:
                        _log.error("_do_exec: WebSocket connection failed after %d attempts", max_retries)
                        self._reply(conn, {_MSG_ERROR: f"Failed to connect: {e}"})
                        return

            # 2. Trigger a prompt by sending a blank line, then drain.
            self._page.evaluate(_WS_SEND_JS, "\r")
            time.sleep(0.3)
            self._page.evaluate(_WS_READ_JS)  # drain prompt

            # 3. Disable echo.
            self._page.evaluate(_WS_SEND_JS, "stty -echo\r")
            time.sleep(0.3)
            self._page.evaluate(_WS_READ_JS)  # drain stty echo

            # 4. Send command with exit-code sentinel.
            wrapped = (
                f"{command}; __ec=$?; "
                f"printf '\\n{_EXIT_SENTINEL}=%d\\n' \"$__ec\""
                f"\r"
            )
            ok = self._page.evaluate(_WS_SEND_JS, wrapped)
            if not ok:
                self._reply(conn, {_MSG_ERROR: "Failed to send command"})
                return

            # 5. Poll for output.
            accumulated: list[str] = []
            deadline = time.monotonic() + timeout
            found_exit_code = None

            while time.monotonic() < deadline:
                try:
                    if self._page.evaluate(_WS_CLOSED_JS):
                        break
                except Exception:
                    break

                try:
                    chunk = self._page.evaluate(_WS_READ_JS)
                except Exception:
                    break

                if not chunk:
                    time.sleep(_POLL_INTERVAL)
                    continue

                exit_match = _EXIT_RE.search(chunk)
                if exit_match:
                    found_exit_code = int(exit_match.group(1))
                    pre = chunk[: exit_match.start()]
                    if pre.endswith("\n"):
                        pre = pre[:-1]
                    if pre:
                        clean = self._strip_ansi(pre)
                        if clean:
                            accumulated.append(clean)
                            self._reply(conn, {_MSG_CHUNK: clean})
                    break

                clean = self._strip_ansi(chunk)
                if clean:
                    accumulated.append(clean)
                    self._reply(conn, {_MSG_CHUNK: clean})

                time.sleep(_POLL_INTERVAL)

            output = "".join(accumulated).strip("\n")
            exit_code = found_exit_code if found_exit_code is not None else -1
            _log.debug("_do_exec: exit_code=%d output_len=%d", exit_code, len(output))
            self._reply(conn, {
                _MSG_RESULT: True,
                "output": output,
                "exit_code": exit_code,
            })

        except Exception as exc:
            _log.exception("_do_exec: error")
            self._reply(conn, {_MSG_ERROR: str(exc)})
        finally:
            try:
                self._page.evaluate(_WS_CLOSE_JS)
            except Exception:
                pass

    @staticmethod
    def _strip_ansi(text: str) -> str:
        text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
        text = text.replace("\r\n", "\n").replace("\r", "")
        return text

    # -- teardown --
    def _teardown(self) -> None:
        if self._term_name and self._lab_url and self._context:
            try:
                from inspire.platform.web.browser_api.rtunnel import _delete_terminal_via_api
                _delete_terminal_via_api(self._context, lab_url=self._lab_url, term_name=self._term_name)
            except Exception:
                pass
        for resource in (self._browser, self._playwright):
            if resource:
                try:
                    if hasattr(resource, "close"):
                        resource.close()
                    elif hasattr(resource, "stop"):
                        resource.stop()
                except Exception:
                    pass
        _cleanup_stale(self.notebook_id)

    @staticmethod
    def _reply(conn: socket.socket, obj: dict) -> None:
        try:
            conn.sendall(json.dumps(obj, ensure_ascii=False).encode() + b"\n")
        except Exception:
            pass


# ------------------------------------------------------------------
# Launcher: start server as a detached subprocess
# ------------------------------------------------------------------
def start_session_server(notebook_id: str, session) -> int:
    """Launch a session server in a background process.

    *session* must expose ``.storage_state`` (a dict or a path to
    Playwright storage-state JSON).  Returns the child PID, or -1 on failure.
    """
    _SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_stale(notebook_id)

    # Persist storage state to a temp file so the subprocess can load it.
    import tempfile as _tmpfile

    state = session.storage_state
    if isinstance(state, dict):
        fd, state_path = _tmpfile.mkstemp(suffix=".json", prefix="inspire_ss_")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
    else:
        state_path = str(state)

    # Spawn subprocess that runs the server.
    log_path = _SOCKET_DIR / f"{notebook_id.replace('/', '_').replace(chr(92), '_')}.log"
    log_fh = open(log_path, "a")
    code = (
        "import json, logging, sys; "
        "logging.basicConfig(level=logging.DEBUG, stream=sys.stderr); "
        "from inspire.bridge.exec_session import SessionServer; "
        f"ss = json.load(open({state_path!r})); "
        f"s = SessionServer({notebook_id!r}, ss); "
        "s.run()"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=log_fh,
        start_new_session=True,
    )
    return proc.pid


def wait_for_ready(notebook_id: str, timeout: float = 30.0) -> bool:
    """Block until the session server signals readiness or *timeout* elapses."""
    rp = _ready_path(notebook_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if rp.exists():
            try:
                if rp.read_text().strip() == _READY_SENTINEL:
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


# ------------------------------------------------------------------
# Convenience: exec via session (start if needed)
# ------------------------------------------------------------------
def exec_via_session(
    notebook_id: str,
    command: str,
    timeout: int = 120,
    on_output: Optional[Callable[[str], Any]] = None,
    session=None,
) -> tuple[str, int]:
    """Execute a command through a persistent session.

    Starts the daemon automatically if *session* credentials are provided.
    Returns ``(output, exit_code)``.
    """
    client = SessionClient(notebook_id)

    # Try existing session first.
    if client.connect():
        try:
            return client.exec_command(command, timeout, on_output)
        except Exception:
            pass
        finally:
            client.close()
    client.close()

    if session is None:
        return "", -1

    # Start new daemon and wait for readiness.
    start_session_server(notebook_id, session)
    if not wait_for_ready(notebook_id, timeout=30.0):
        return "", -1

    client = SessionClient(notebook_id)
    if client.connect():
        try:
            return client.exec_command(command, timeout, on_output)
        except Exception:
            pass
        finally:
            client.close()
    client.close()
    return "", -1
