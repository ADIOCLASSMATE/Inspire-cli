"""Tests for metrics_utils statistical computation and health assessment."""

from __future__ import annotations

import pytest

from inspire.cli.utils.metrics_utils import (
    IDLE_THRESHOLD,
    IdleWindow,
    MetricSummary,
    assess_health,
    compute_summary,
    detect_idle_windows,
    compute_trend,
)
from inspire.cli.utils.status_normalizer import (
    StatusFamily,
    analyze_timeline,
    classify_job_id,
    format_duration,
    normalize_status,
)


# ---------------------------------------------------------------------------
# compute_summary tests
# ---------------------------------------------------------------------------


class TestComputeSummary:
    def test_empty_values(self):
        s = compute_summary([])
        assert s.samples == 0
        assert s.avg == 0
        assert s.trend == "stable"

    def test_single_value(self):
        s = compute_summary([0.5])
        assert s.samples == 1
        assert s.avg == 0.5
        assert s.p50 == 0.5
        assert s.p90 == 0.5
        assert s.min == 0.5
        assert s.max == 0.5
        assert s.trend == "stable"

    def test_constant_values(self):
        s = compute_summary([0.3, 0.3, 0.3, 0.3, 0.3])
        assert s.avg == pytest.approx(0.3)
        assert s.stddev == pytest.approx(0.0)
        assert s.trend == "stable"

    def test_increasing_trend(self):
        s = compute_summary([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
        assert s.trend == "up"
        assert s.trend_magnitude > 0.15

    def test_high_utilization(self):
        s = compute_summary([0.95, 0.96, 0.97, 0.98, 0.99] * 10)
        assert s.p90 == pytest.approx(0.99)
        assert s.idle_total_samples == 0

    def test_idle_detection(self):
        """Values below IDLE_THRESHOLD should create idle windows."""
        values = [0.3, 0.3, 0.01, 0.02, 0.01, 0.3, 0.3]
        s = compute_summary(values)
        assert s.idle_total_samples == 3
        assert len(s.idle_windows) == 1
        assert s.idle_windows[0].start_idx == 2
        assert s.idle_windows[0].end_idx == 4


# ---------------------------------------------------------------------------
# detect_idle_windows tests
# ---------------------------------------------------------------------------


class TestDetectIdleWindows:
    def test_no_idle(self):
        windows = detect_idle_windows([0.5, 0.6, 0.7], IDLE_THRESHOLD)
        assert len(windows) == 0

    def test_all_idle(self):
        windows = detect_idle_windows([0.01, 0.02, 0.01], IDLE_THRESHOLD)
        assert len(windows) == 1
        assert windows[0].start_idx == 0
        assert windows[0].end_idx == 2
        assert windows[0].duration_samples == 3

    def test_multiple_idle_windows(self):
        values = [0.01, 0.01, 0.5, 0.6, 0.01, 0.01, 0.01]
        windows = detect_idle_windows(values, IDLE_THRESHOLD)
        assert len(windows) == 2
        assert windows[0].duration_samples == 2  # first idle window
        assert windows[1].duration_samples == 3  # second idle window


# ---------------------------------------------------------------------------
# assess_health tests
# ---------------------------------------------------------------------------


def _make_summary(avg=0.5, p90=0.5, samples=10, trend="stable", idle_samples=0):
    return MetricSummary(
        avg=avg, p50=avg, p90=p90,
        min=0.0, max=1.0, latest=avg,
        stddev=0.1, samples=samples, trend=trend,
        trend_magnitude=0.1, idle_total_samples=idle_samples,
    )


class TestAssessHealth:
    def test_no_data(self):
        status, details = assess_health(None, None, None, None)
        assert status == "无数据"

    def test_healthy(self):
        gpu = _make_summary(avg=0.8, p90=0.9, samples=100)
        status, details = assess_health(gpu, None, None, None)
        assert status == "正常"

    def test_stuck(self):
        """GPU avg < 0.1 and idle > 50% should return '疑似卡住'."""
        gpu = _make_summary(avg=0.03, p90=0.05, samples=100, trend="stable", idle_samples=80)
        status, details = assess_health(gpu, None, None, None)
        assert status == "疑似卡住"

    def test_preheating(self):
        """GPU avg low but trending up with p90 > 0.3 should return '预热中'."""
        gpu = _make_summary(avg=0.05, p90=0.4, samples=100, trend="up", idle_samples=60)
        status, details = assess_health(gpu, None, None, None)
        assert status == "预热中"

    def test_cpu_bottleneck(self):
        cpu = _make_summary(avg=0.85, p90=0.95, samples=100)
        gpu = _make_summary(avg=0.2, p90=0.3, samples=100)
        status, details = assess_health(gpu, None, cpu, None)
        assert status == "CPU 瓶颈"

    def test_gpu_memory_pressure(self):
        gpu_mem = _make_summary(avg=0.85, p90=0.95, samples=100)
        status, details = assess_health(None, gpu_mem, None, None)
        assert status == "显存紧张"

    def test_ram_pressure(self):
        mem = _make_summary(avg=0.95, p90=0.99, samples=100)
        status, details = assess_health(None, None, None, mem)
        assert status == "内存紧张"


# ---------------------------------------------------------------------------
# normalize_status tests
# ---------------------------------------------------------------------------


class TestNormalizeStatus:
    def test_v2_numeric_codes(self):
        assert normalize_status(1).family == "running"
        assert normalize_status(1).is_terminal is False
        assert normalize_status(2).family == "waiting"
        assert normalize_status(4).family == "succeeded"
        assert normalize_status(4).is_terminal is True
        assert normalize_status(5).family == "failed"
        assert normalize_status(5).is_terminal is True
        assert normalize_status(6).family == "stopped"
        assert normalize_status(6).is_terminal is True

    def test_v1_strings(self):
        assert normalize_status("RUNNING").family == "running"
        assert normalize_status("job_running").family == "running"
        assert normalize_status("PENDING").family == "waiting"
        assert normalize_status("SUCCEEDED").family == "succeeded"
        assert normalize_status("FAILED").family == "failed"
        assert normalize_status("CANCELLED").family == "stopped"

    def test_unknown(self):
        assert normalize_status(None).family == "unknown"
        assert normalize_status("bogus").family == "unknown"

    def test_case_insensitive(self):
        assert normalize_status("running").family == "running"
        assert normalize_status("Job_Failed").family == "failed"


# ---------------------------------------------------------------------------
# classify_job_id tests
# ---------------------------------------------------------------------------


class TestClassifyJobId:
    def test_gpu_job(self):
        assert classify_job_id("job-abc123-def456") == "gpu"

    def test_hpc_job(self):
        assert classify_job_id("hpc-job-xyz789") == "hpc"

    def test_inference(self):
        assert classify_job_id("sv-12345") == "inference"

    def test_unknown(self):
        assert classify_job_id("unknown-prefix") == "unknown"

    def test_empty(self):
        assert classify_job_id("") == "unknown"


# ---------------------------------------------------------------------------
# analyze_timeline tests
# ---------------------------------------------------------------------------


class TestAnalyzeTimeline:
    def test_full_timeline(self):
        tl = analyze_timeline({
            "pending": 1700000000000,
            "running": 1700000001000,
            "succeeded": 1700000005000,
        })
        assert tl.never_started is False
        assert tl.queue_ms == 1000
        assert tl.run_ms == 4000
        assert len(tl.stages) == 3

    def test_never_started(self):
        tl = analyze_timeline({
            "pending": 1700000000000,
        })
        assert tl.never_started is True
        assert "Never started" in tl.summary

    def test_none_timeline(self):
        tl = analyze_timeline(None)
        assert tl.summary == "No timeline data"

    def test_empty_timeline(self):
        tl = analyze_timeline({})
        assert "No timeline data" in tl.summary or "Unknown" in tl.summary


# ---------------------------------------------------------------------------
# format_duration tests
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(5000) == "5s"

    def test_minutes(self):
        assert format_duration(125000) == "2m 5s"

    def test_hours(self):
        assert format_duration(3723000) == "1h 2m 3s"

    def test_days(self):
        assert format_duration(90000000) == "1d 1h 0m"

    def test_none(self):
        assert format_duration(None) == "N/A"
