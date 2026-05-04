"""Status normalization and timeline analysis for job lifecycle management.

Ported from holos-inspire ``normalize.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StatusFamily:
    """Normalized job status family."""

    family: str  # "running" | "waiting" | "succeeded" | "failed" | "stopped" | "unknown"
    is_terminal: bool
    raw: str


# v2 numeric status codes
_V2_STATUS_MAP: dict[int, tuple[str, bool]] = {
    1: ("running", False),
    2: ("waiting", False),
    3: ("waiting", False),  # queuing
    4: ("succeeded", True),
    5: ("failed", True),
    6: ("stopped", True),
    7: ("waiting", False),  # preempted / rescheduling
    8: ("failed", True),
}

# v1 string status prefixes
_V1_STATUS_PATTERNS: list[tuple[str, str, bool]] = [
    ("succeeded", "succeeded", True),
    ("job_succeeded", "succeeded", True),
    ("failed", "failed", True),
    ("job_failed", "failed", True),
    ("stopped", "stopped", True),
    ("job_stopped", "stopped", True),
    ("cancelled", "stopped", True),
    ("job_cancelled", "stopped", True),
    ("running", "running", False),
    ("job_running", "running", False),
    ("pending", "waiting", False),
    ("job_pending", "waiting", False),
    ("queuing", "waiting", False),
    ("job_queuing", "waiting", False),
    ("creating", "waiting", False),
    ("job_creating", "waiting", False),
    ("rescheduling", "waiting", False),
]


def normalize_status(raw: Any) -> StatusFamily:
    """Map diverse status strings/numbers to standard families.

    Handles v2 numeric codes (1-8) and v1 snake_case strings.
    """
    if raw is None:
        return StatusFamily(family="unknown", is_terminal=False, raw="(none)")

    raw_str = str(raw).strip()

    # Try numeric code
    try:
        code = int(raw_str)
        family, is_terminal = _V2_STATUS_MAP.get(code, ("unknown", False))
        return StatusFamily(family=family, is_terminal=is_terminal, raw=raw_str)
    except (ValueError, TypeError):
        pass

    # Try string pattern matching
    lower = raw_str.lower()
    for pattern, family, is_terminal in _V1_STATUS_PATTERNS:
        if pattern in lower:
            return StatusFamily(family=family, is_terminal=is_terminal, raw=raw_str)

    return StatusFamily(family="unknown", is_terminal=False, raw=raw_str)


def format_duration(ms: int | str | None) -> str:
    """Format milliseconds as human-readable duration string."""
    if ms is None:
        return "N/A"
    try:
        millis = int(ms)
    except (ValueError, TypeError):
        return str(ms)

    seconds = millis // 1000
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24

    if days > 0:
        return f"{days}d {hours % 24}h {minutes % 60}m"
    if hours > 0:
        return f"{hours}h {minutes % 60}m {seconds % 60}s"
    if minutes > 0:
        return f"{minutes}m {seconds % 60}s"
    return f"{seconds}s"


@dataclass(frozen=True)
class TimelineAnalysis:
    """Analysis of a job's timeline stages."""

    summary: str
    queue_ms: int | None
    run_ms: int | None
    total_duration_ms: int | None
    never_started: bool
    stages: list[dict]


def analyze_timeline(timeline: dict | None) -> TimelineAnalysis:
    """Analyze a job timeline to extract queue time, run time, etc.

    The timeline typically has entries like::

        {"pending": 1700000000000, "running": 1700000001000, ...}

    where values are Unix millisecond timestamps.
    """
    if not timeline or not isinstance(timeline, dict):
        return TimelineAnalysis(
            summary="No timeline data",
            queue_ms=None,
            run_ms=None,
            total_duration_ms=None,
            never_started=False,
            stages=[],
        )

    stages: list[dict] = []
    for stage_name, ts in sorted(timeline.items(), key=lambda x: x[1] if x[1] else 0):
        stages.append({"stage": stage_name, "timestamp_ms": ts})

    # Find key transitions
    pending_ts = timeline.get("pending")
    running_ts = timeline.get("running")
    finished_ts = timeline.get("finished") or timeline.get("succeeded") or timeline.get("failed")

    queue_ms = None
    run_ms = None
    never_started = False

    if pending_ts and running_ts:
        queue_ms = running_ts - pending_ts
    if running_ts and finished_ts:
        run_ms = finished_ts - running_ts
    if pending_ts and not running_ts:
        never_started = True

    total_ms = None
    if pending_ts and finished_ts:
        total_ms = finished_ts - pending_ts

    # Build summary
    parts: list[str] = []
    if never_started:
        parts.append("Never started running")
    if queue_ms is not None and queue_ms > 0:
        parts.append(f"Queued: {format_duration(queue_ms)}")
    if run_ms is not None and run_ms > 0:
        parts.append(f"Running: {format_duration(run_ms)}")
    if run_ms is not None and run_ms <= 0 and not never_started:
        parts.append("Run time: <1s (may have failed immediately)")

    return TimelineAnalysis(
        summary=" | ".join(parts) if parts else "Unknown timeline",
        queue_ms=queue_ms,
        run_ms=run_ms,
        total_duration_ms=total_ms,
        never_started=never_started,
        stages=stages,
    )


def classify_job_id(job_id: str) -> str:
    """Classify a job ID by prefix.

    Returns:
        "gpu" for ``job-*``, "hpc" for ``hpc-job-*``, "inference" for ``sv-*``,
        "unknown" for unrecognized formats.
    """
    if not job_id:
        return "unknown"
    if job_id.startswith("hpc-job-"):
        return "hpc"
    if job_id.startswith("job-"):
        return "gpu"
    if job_id.startswith("sv-"):
        return "inference"
    return "unknown"
