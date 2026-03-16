"""Reusable notebook discovery flow.

This module powers `inspire notebook reusable`.

A notebook is considered reusable if:
- it is RUNNING
- its resource spec strictly matches the requested resource
- and (for GPU notebooks) it appears idle based on `nvidia-smi` sampling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GpuSample:
    max_util_percent: int
    max_mem_used_mib: int


class NotebookIdleProbe:
    """Reusable Playwright probe for checking notebook idleness.

    Launching Playwright + browser + context is expensive. This helper lets
    `inspire notebook reusable` reuse a single browser/context/page across many
    candidate notebooks within one command invocation.

    Notes:
    - Requires Playwright.
    - Best-effort cleanup; any probe failure should be treated as NOT idle.
    """

    def __init__(self, session: Any, *, headless: bool = True):
        self._session = session
        self._headless = headless

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def page(self):
        return self._page

    @property
    def context(self):
        return self._context

    def __enter__(self) -> "NotebookIdleProbe":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "Playwright is required. Install with: uv pip install playwright && playwright install chromium"
            ) from e

        from inspire.platform.web.browser_api.core import _launch_browser, _new_context

        self._playwright = sync_playwright().start()
        self._browser = _launch_browser(self._playwright, headless=self._headless)
        self._context = _new_context(self._browser, storage_state=self._session.storage_state)
        self._page = self._context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        # Best-effort teardown.
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass

    def check_idle(
        self,
        notebook_id: str,
        *,
        samples: int,
        util_threshold: int,
        mem_threshold_mib: int,
        sample_interval_s: float,
        exec_timeout_s: int,
        lab_timeout_ms: int,
    ) -> bool:
        """Return True if sampled util/mem stays below thresholds.

        Busy short-circuit: as soon as any sample crosses a threshold, return False.
        """

        if self._page is None or self._context is None:
            return False

        from inspire.bridge.jupyter_exec import exec_in_jupyter_terminal
        from inspire.platform.web.browser_api.playwright_notebooks import open_notebook_lab
        from inspire.platform.web.browser_api.rtunnel import (
            _build_terminal_websocket_url,
            _create_terminal_via_api,
            _delete_terminal_via_api,
        )

        query_cmd = (
            "nvidia-smi --query-gpu=utilization.gpu,memory.used "
            "--format=csv,noheader,nounits"
        )

        term_name = None
        lab_frame = None
        try:
            lab_frame = open_notebook_lab(self._page, notebook_id=notebook_id, timeout=lab_timeout_ms)

            # Wait for Jupyter UI to settle (best-effort; UI text may vary)
            try:  # pragma: no cover
                lab_frame.locator("text=加载中").first.wait_for(state="hidden", timeout=15000)
            except Exception:
                pass

            term_name = _create_terminal_via_api(self._context, lab_frame.url)
            if not term_name:
                return False

            ws_url = _build_terminal_websocket_url(lab_frame.url, term_name)

            for idx in range(max(1, samples)):
                result = exec_in_jupyter_terminal(
                    self._page,
                    ws_url,
                    query_cmd,
                    timeout_s=exec_timeout_s,
                    on_output=None,
                )
                if result.exit_code != 0:
                    return False

                rows = _parse_nvidia_smi_util_mem(result.output)
                if not rows:
                    return False

                max_util = max(u for u, _ in rows)
                max_mem = max(m for _, m in rows)

                # Busy short-circuit.
                if max_util >= util_threshold or max_mem >= mem_threshold_mib:
                    return False

                if idx != samples - 1 and samples > 1:
                    self._page.wait_for_timeout(int(sample_interval_s * 1000))

            return True

        except Exception:
            return False
        finally:
            if term_name and lab_frame:
                try:
                    _delete_terminal_via_api(self._context, lab_url=lab_frame.url, term_name=term_name)
                except Exception:
                    pass


def _parse_nvidia_smi_util_mem(output: str) -> list[tuple[int, int]]:
    """Parse `nvidia-smi` CSV output into [(util_percent, mem_used_mib), ...].

    Expected format (one GPU per line):
        utilization.gpu, memory.used
    Example:
        5, 123
        0, 98
    """

    rows: list[tuple[int, int]] = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            util = int(float(parts[0]))
            mem = int(float(parts[1]))
        except ValueError:
            continue
        rows.append((util, mem))
    return rows


def _is_idle(
    samples: list[GpuSample],
    *,
    util_threshold: int = 10,
    mem_threshold_mib: int = 2048,
) -> bool:
    # Deprecated by the short-circuit logic in NotebookIdleProbe.check_idle, but
    # kept for compatibility and direct unit testing of the heuristic.
    if not samples:
        return False

    worst_util = max(s.max_util_percent for s in samples)
    worst_mem = max(s.max_mem_used_mib for s in samples)
    return worst_util < util_threshold and worst_mem < mem_threshold_mib


def check_notebook_idle_via_nvidia_smi(
    *,
    notebook_id: str,
    session,
    samples: int = 3,
    util_threshold: int = 10,
    mem_threshold_mib: int = 2048,
    sample_interval_s: float = 1.6,
    exec_timeout_s: int = 20,
    lab_timeout_ms: int = 60000,
    probe: NotebookIdleProbe | None = None,
) -> bool:
    """Open a notebook terminal and sample GPU util/mem via nvidia-smi.

    Returns True if all samples stay below thresholds.

    Notes:
    - This requires Playwright.
    - Any failure to probe is treated as NOT idle (returns False).
    - If `probe` is provided, reuses its Playwright browser/context/page.
    """

    # If a probe is provided, reuse it directly.
    if probe is not None:
        return probe.check_idle(
            notebook_id,
            samples=samples,
            util_threshold=util_threshold,
            mem_threshold_mib=mem_threshold_mib,
            sample_interval_s=sample_interval_s,
            exec_timeout_s=exec_timeout_s,
            lab_timeout_ms=lab_timeout_ms,
        )

    # Backwards-compatible slow path: create a one-off probe.
    try:
        with NotebookIdleProbe(session) as p:
            return p.check_idle(
                notebook_id,
                samples=samples,
                util_threshold=util_threshold,
                mem_threshold_mib=mem_threshold_mib,
                sample_interval_s=sample_interval_s,
                exec_timeout_s=exec_timeout_s,
                lab_timeout_ms=lab_timeout_ms,
            )
    except ImportError:
        raise
    except Exception:
        return False


__all__ = [
    "GpuSample",
    "NotebookIdleProbe",
    "_is_idle",
    "_parse_nvidia_smi_util_mem",
    "check_notebook_idle_via_nvidia_smi",
]
