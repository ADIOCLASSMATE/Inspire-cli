"""Statistical computation utilities for GPU training metrics.

Ported from holos-inspire ``metrics.ts``.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


IDLE_THRESHOLD = 0.05  # GPU usage below 5% is considered idle


@dataclass(frozen=True)
class IdleWindow:
    """A contiguous window where the metric value is below the idle threshold."""

    start_idx: int
    end_idx: int
    duration_samples: int


@dataclass(frozen=True)
class MetricSummary:
    """Statistical summary of a metric time series."""

    avg: float
    p50: float
    p90: float
    min: float
    max: float
    latest: float
    stddev: float
    samples: int
    trend: str  # "up" | "down" | "stable"
    trend_magnitude: float
    idle_windows: list[IdleWindow] = field(default_factory=list)
    idle_total_samples: int = 0


def compute_summary(values: list[float]) -> MetricSummary:
    """Compute statistical summary of a metric time series.

    Returns:
        MetricSummary with avg, p50, p90, min, max, stddev, trend, idle windows.
    """
    if not values:
        return MetricSummary(
            avg=0, p50=0, p90=0, min=0, max=0, latest=0,
            stddev=0, samples=0, trend="stable", trend_magnitude=0,
        )

    n = len(values)
    avg = sum(values) / n
    sorted_vals = sorted(values)

    def _percentile(p: float) -> float:
        idx = (n - 1) * p
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    p50 = _percentile(0.5)
    p90 = _percentile(0.9)
    min_val = sorted_vals[0]
    max_val = sorted_vals[-1]
    latest = values[-1]

    # Standard deviation
    variance = sum((x - avg) ** 2 for x in values) / n
    stddev = math.sqrt(variance)

    # Trend: linear regression slope
    if n >= 2:
        x_mean = (n - 1) / 2
        y_mean = avg
        numerator = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator > 0:
            slope = numerator / denominator
            # Normalize magnitude relative to value range
            value_range = max_val - min_val
            if value_range > 0:
                mag = abs(slope * n / value_range)
            else:
                mag = 0
        else:
            slope = 0
            mag = 0
    else:
        slope = 0
        mag = 0

    if mag > 0.15:
        trend = "up" if slope > 0 else "down"
    else:
        trend = "stable"

    # Idle windows
    idle_windows = detect_idle_windows(values, IDLE_THRESHOLD)
    idle_total = sum(w.duration_samples for w in idle_windows)

    return MetricSummary(
        avg=avg, p50=p50, p90=p90, min=min_val, max=max_val, latest=latest,
        stddev=stddev, samples=n, trend=trend, trend_magnitude=mag,
        idle_windows=idle_windows, idle_total_samples=idle_total,
    )


def detect_idle_windows(values: list[float], threshold: float) -> list[IdleWindow]:
    """Detect contiguous idle windows in a time series."""
    windows: list[IdleWindow] = []
    in_idle = False
    start = 0

    for i, v in enumerate(values):
        if v < threshold and not in_idle:
            in_idle = True
            start = i
        elif v >= threshold and in_idle:
            in_idle = False
            windows.append(IdleWindow(
                start_idx=start,
                end_idx=i - 1,
                duration_samples=i - start,
            ))

    if in_idle:
        windows.append(IdleWindow(
            start_idx=start,
            end_idx=len(values) - 1,
            duration_samples=len(values) - start,
        ))

    return windows


def compute_trend(values: list[float]) -> tuple[str, float]:
    """Compute trend direction and magnitude.

    Returns:
        (trend: "up"|"down"|"stable", magnitude: float)
    """
    summary = compute_summary(values)
    return summary.trend, summary.trend_magnitude


def assess_health(
    gpu: MetricSummary | None,
    gpu_mem: MetricSummary | None,
    cpu: MetricSummary | None,
    mem: MetricSummary | None,
    job_status: str = "",
) -> tuple[str, list[str]]:
    """Assess training health from metric summaries.

    Returns:
        (health_status_label, list of detail strings)
    """
    # No data
    if all(s is None or s.samples == 0 for s in (gpu, gpu_mem, cpu, mem)):
        return "无数据", ["未获取到任何指标数据。任务可能尚未开始或已结束。"]

    details: list[str] = []
    status = "正常"

    # Check GPU
    if gpu and gpu.samples > 0:
        details.append(
            f"GPU 利用率: avg={gpu.avg:.1%} p50={gpu.p50:.1%} p90={gpu.p90:.1%} "
            f"min={gpu.min:.1%} max={gpu.max:.1%} trend={gpu.trend}"
        )

        if gpu.idle_total_samples > gpu.samples * 0.5:
            if gpu.trend == "up" and gpu.p90 > 0.3:
                status = "预热中"
                details.append("GPU 利用率低但呈上升趋势，可能正在预热。")
            else:
                status = "疑似卡住"
                details.append(f"GPU 利用率低于 {IDLE_THRESHOLD:.0%} 超过 50% 时间，任务可能卡住。")

        if gpu.avg < 0.1 and gpu.trend != "up":
            if status == "正常":
                status = "GPU 利用率低"
            details.append("GPU 平均利用率极低，检查训练循环是否正常运行。")

    # Check GPU memory
    if gpu_mem and gpu_mem.samples > 0:
        details.append(
            f"GPU 显存: avg={gpu_mem.avg:.1%} p90={gpu_mem.p90:.1%}"
        )
        if gpu_mem.p90 > 0.9:
            if status == "正常":
                status = "显存紧张"
            details.append("GPU 显存使用率 p90 > 90%，注意 OOM 风险。")

    # Check CPU
    if cpu and cpu.samples > 0:
        details.append(
            f"CPU 利用率: avg={cpu.avg:.1%} p90={cpu.p90:.1%}"
        )
        if cpu.avg > 0.7 and (gpu is None or gpu.avg < 0.3):
            if status == "正常":
                status = "CPU 瓶颈"
            details.append("CPU 利用率高但 GPU 利用率低，可能存在 CPU 瓶颈（数据加载/预处理）。")

    # Check memory
    if mem and mem.samples > 0:
        details.append(
            f"内存: avg={mem.avg:.1%} p90={mem.p90:.1%}"
        )
        if mem.avg > 0.9:
            if status == "正常":
                status = "内存紧张"
            details.append("内存使用率 > 90%，注意 OOM 风险。")

    if status == "正常" and gpu and gpu.samples > 0 and 0.3 <= gpu.avg <= 1.0:
        details.append("GPU 利用率正常，训练运行健康。")

    return status, details


_METRIC_TYPES = [
    "gpu_usage_rate",
    "gpu_memory_usage_rate",
    "cpu_usage_rate",
    "memory_usage_rate",
]

_TIME_RANGE_SECONDS = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "3h": 10800,
    "6h": 21600,
}


def fetch_job_metrics(
    token: str,
    base_url: str,
    job_id: str,
    *,
    time_range: str = "30m",
    interval: int | None = None,
) -> tuple[list[dict], str, str]:
    """Fetch metrics for a job and return (metric_groups, compute_group_id, job_status).

    Shared helper used by both ``metrics show`` and ``metrics health`` commands.
    """
    from inspire.platform.web.v2_api.train import get_job_detail, get_task_metrics

    job_data = get_job_detail(token, base_url, job_id)
    compute_group_id = job_data.get("logic_compute_group_id", "")
    job_status = str(job_data.get("status", ""))

    now_ms = int(time.time() * 1000)
    duration_s = _TIME_RANGE_SECONDS.get(time_range, 1800)
    start_ms = now_ms - duration_s * 1000
    interval_s = interval or max(10, duration_s // 30)

    metric_groups = get_task_metrics(
        token, base_url,
        compute_group_id=compute_group_id,
        task_id=job_id,
        metric_types=list(_METRIC_TYPES),
        start_timestamp=start_ms,
        end_timestamp=now_ms,
        interval_second=interval_s,
    )

    return metric_groups, compute_group_id, job_status
