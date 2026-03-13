"""Reusable notebook discovery flow.

This module powers `inspire notebook reusable`.

A notebook is considered reusable if:
- it is RUNNING
- its resource spec strictly matches the requested resource
- and (for GPU notebooks) it appears idle based on `nvidia-smi` sampling.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GpuSample:
    max_util_percent: int
    max_mem_used_mib: int


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
) -> bool:
    """Open a notebook terminal and sample GPU util/mem via nvidia-smi.

    Returns True if all samples stay below thresholds (max-over-time heuristic).

    Notes:
    - This requires Playwright.
    - Any failure to probe is treated as NOT idle (returns False).
    """

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Playwright is required. Install with: uv pip install playwright && playwright install chromium"
        ) from e

    from inspire.bridge.jupyter_exec import exec_in_jupyter_terminal
    from inspire.platform.web.browser_api.core import _launch_browser, _new_context
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

    gpu_samples: list[GpuSample] = []

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=True)
        context = _new_context(browser, storage_state=session.storage_state)
        page = context.new_page()

        term_name = None
        lab_frame = None
        try:
            lab_frame = open_notebook_lab(page, notebook_id=notebook_id, timeout=60000)

            # Wait for Jupyter UI to settle (best-effort; UI text may vary)
            try:  # pragma: no cover
                lab_frame.locator("text=加载中").first.wait_for(state="hidden", timeout=15000)
            except Exception:
                pass

            term_name = _create_terminal_via_api(context, lab_frame.url)
            if not term_name:
                return False

            ws_url = _build_terminal_websocket_url(lab_frame.url, term_name)

            for idx in range(max(1, samples)):
                result = exec_in_jupyter_terminal(
                    page,
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
                gpu_samples.append(GpuSample(max_util_percent=max_util, max_mem_used_mib=max_mem))

                if idx != samples - 1:
                    page.wait_for_timeout(int(sample_interval_s * 1000))

        except Exception:
            return False
        finally:
            if term_name and lab_frame:
                try:
                    _delete_terminal_via_api(context, lab_url=lab_frame.url, term_name=term_name)
                except Exception:
                    pass
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    return _is_idle(
        gpu_samples,
        util_threshold=util_threshold,
        mem_threshold_mib=mem_threshold_mib,
    )


__all__ = [
    "GpuSample",
    "_is_idle",
    "_parse_nvidia_smi_util_mem",
    "check_notebook_idle_via_nvidia_smi",
]
