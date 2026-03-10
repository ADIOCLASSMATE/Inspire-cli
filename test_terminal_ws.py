"""Quick test: connect to notebook Jupyter terminal WS, send a command, read output."""
import sys
import time

from inspire.platform.web.session import get_web_session
from inspire.platform.web.browser_api.core import _launch_browser, _new_context
from inspire.platform.web.browser_api.playwright_notebooks import open_notebook_lab
from inspire.platform.web.browser_api.rtunnel import (
    _build_terminal_websocket_url,
    _create_terminal_via_api,
    _delete_terminal_via_api,
)

NOTEBOOK_ID = sys.argv[1] if len(sys.argv) > 1 else "test-4090"
CMD = sys.argv[2] if len(sys.argv) > 2 else "echo HELLO_FROM_GPU && nvidia-smi --query-gpu=name --format=csv,noheader"

print(f"[test] Notebook: {NOTEBOOK_ID}")
print(f"[test] Command: {CMD}")

from playwright.sync_api import sync_playwright

session = get_web_session()
print(f"[test] Session OK (user: {session.login_username})")

with sync_playwright() as p:
    browser = _launch_browser(p, headless=True)
    context = _new_context(browser, storage_state=session.storage_state)
    page = context.new_page()

    # The notebook ID should already be a UUID if passed directly
    lab_frame = open_notebook_lab(page, notebook_id=NOTEBOOK_ID, timeout=60000)
    print(f"[test] Lab frame URL obtained")

    # Wait for UI
    try:
        lab_frame.locator("text=加载中").first.wait_for(state="hidden", timeout=10000)
    except Exception:
        pass

    term_name = _create_terminal_via_api(context, lab_frame.url)
    print(f"[test] Terminal created: {term_name}")

    ws_url = _build_terminal_websocket_url(lab_frame.url, term_name)
    print(f"[test] WS URL built (redacted)")

    # Connect WebSocket via browser JS
    JS_SETUP = """
    (wsUrl) => {
        return new Promise((resolve, reject) => {
            const ws = new WebSocket(wsUrl);
            window.__testWs = ws;
            window.__testBuf = '';
            ws.onopen = () => resolve('connected');
            ws.onerror = (e) => reject(new Error('WS error'));
            ws.onclose = () => {};
            ws.onmessage = (e) => {
                try {
                    const msg = JSON.parse(e.data);
                    if (msg[0] === 'stdout') window.__testBuf += msg[1];
                } catch(_) {}
            };
            setTimeout(() => reject(new Error('timeout')), 10000);
        });
    }
    """
    result = page.evaluate(JS_SETUP, ws_url)
    print(f"[test] WebSocket: {result}")

    # Wait for shell prompt
    time.sleep(1.0)

    # Drain initial output
    initial = page.evaluate("() => { const b = window.__testBuf; window.__testBuf = ''; return b; }")
    print(f"[test] Initial output ({len(initial)} chars): {repr(initial[:200])}")

    # Send command
    send_cmd = CMD + "\r"
    page.evaluate(
        "(data) => window.__testWs.send(JSON.stringify(['stdin', data]))",
        send_cmd,
    )
    print(f"[test] Sent command")

    # Read output
    time.sleep(2.0)
    output = page.evaluate("() => { const b = window.__testBuf; window.__testBuf = ''; return b; }")
    print(f"[test] Output ({len(output)} chars):")
    print("---")
    print(output)
    print("---")

    # Cleanup
    page.evaluate("() => { if (window.__testWs) window.__testWs.close(); }")
    _delete_terminal_via_api(context, lab_url=lab_frame.url, term_name=term_name)
    print("[test] Done")
