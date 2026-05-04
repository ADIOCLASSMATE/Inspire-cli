"""``inspire metrics health`` — assess training health with diagnostic recommendations."""

from __future__ import annotations

import click

from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_AUTH_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_JOB_NOT_FOUND,
    pass_context,
)
from inspire.cli.formatters import json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.metrics_utils import (
    _TIME_RANGE_SECONDS,
    assess_health,
    compute_summary,
    fetch_job_metrics,
    MetricSummary,
)
from inspire.config import Config, ConfigError


def _extract_values(series: list[dict]) -> list[float]:
    return [float(p.get("data", 0)) for p in series if p.get("data") is not None]


@click.command("health")
@click.argument("job_id")
@click.option(
    "--time-range", "-t",
    type=click.Choice(list(_TIME_RANGE_SECONDS.keys())),
    default="30m",
    help="Time window for health assessment (default: 30m)",
)
@pass_context
def health(ctx: Context, job_id: str, time_range: str) -> None:
    """Assess training health: detect stuck/idle/preheating/resource pressure.

    Analyzes GPU, GPU memory, CPU, and memory metrics and classifies
    the training state into categories with actionable recommendations.

    Health categories:
      - 正常: Training running healthily
      - 预热中: GPU warming up (low but trending up)
      - 疑似卡住: GPU idle >50% of time, likely stuck
      - GPU 利用率低: GPU utilization critically low
      - CPU 瓶颈: CPU saturated while GPU idle
      - 显存紧张: GPU memory >90% percentile
      - 内存紧张: System memory >90%

    \b
    Examples:
        inspire metrics health job-abc123
        inspire metrics health job-abc123 --time-range 1h
        inspire metrics health job-abc123 --json
    """
    try:
        config, _ = Config.from_files_and_env()

        from inspire.platform.web.session import get_v2_token
        token = get_v2_token(config)
        if not token:
            _handle_error(
                ctx, "AuthenticationError",
                "v2 API authentication required. Set INSPIRE_USERNAME and INSPIRE_PASSWORD.",
                EXIT_AUTH_ERROR,
            )
            return

        metric_groups, compute_group_id, job_status = fetch_job_metrics(
            token, config.base_url, job_id,
            time_range=time_range,
        )

        if not compute_group_id:
            _handle_error(
                ctx, "ValidationError",
                "Cannot determine compute group for this job",
                EXIT_CONFIG_ERROR,
            )
            return

        # Build summaries
        summaries: dict[str, MetricSummary] = {}
        for group in metric_groups:
            mt = group.get("metric_type", "")
            vals = _extract_values(group.get("time_series", []))
            if vals:
                summaries[mt] = compute_summary(vals)

        gpu_s = summaries.get("gpu_usage_rate")
        gpu_mem_s = summaries.get("gpu_memory_usage_rate")
        cpu_s = summaries.get("cpu_usage_rate")
        mem_s = summaries.get("memory_usage_rate")

        status_label, detail_lines = assess_health(gpu_s, gpu_mem_s, cpu_s, mem_s, job_status)

        if ctx.json_output:
            def _to_dict(s: MetricSummary | None) -> dict | None:
                if s is None:
                    return None
                return {
                    "avg": s.avg, "p50": s.p50, "p90": s.p90,
                    "min": s.min, "max": s.max, "latest": s.latest,
                    "stddev": s.stddev, "samples": s.samples,
                    "trend": s.trend, "trend_magnitude": s.trend_magnitude,
                    "idle_total_samples": s.idle_total_samples,
                }

            click.echo(json_formatter.format_json({
                "job_id": job_id,
                "job_status": job_status,
                "health": status_label,
                "details": detail_lines,
                "summaries": {
                    "gpu_usage_rate": _to_dict(gpu_s),
                    "gpu_memory_usage_rate": _to_dict(gpu_mem_s),
                    "cpu_usage_rate": _to_dict(cpu_s),
                    "memory_usage_rate": _to_dict(mem_s),
                },
            }))
            return

        # Human output
        emoji_map = {
            "正常": "OK",
            "预热中": "WARMING",
            "疑似卡住": "STUCK",
            "GPU 利用率低": "LOW_GPU",
            "CPU 瓶颈": "CPU_BOTTLENECK",
            "显存紧张": "GPU_MEM_HIGH",
            "内存紧张": "MEM_HIGH",
            "无数据": "NO_DATA",
        }
        tag = emoji_map.get(status_label, status_label)

        click.echo(f"Health Assessment for {job_id}")
        click.echo(f"  Status: {job_status}")
        click.echo(f"  Health: [{tag}] {status_label}")
        click.echo(f"  Time Range: last {time_range}")
        click.echo("")
        for line in detail_lines:
            click.echo(f"  {line}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
