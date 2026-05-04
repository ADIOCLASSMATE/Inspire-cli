"""``inspire metrics show`` — display GPU/CPU/memory metrics for a training job."""

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
    compute_summary,
    fetch_job_metrics,
    MetricSummary,
)
from inspire.config import Config, ConfigError

_METRIC_LABELS = {
    "gpu_usage_rate": "GPU 利用率",
    "gpu_memory_usage_rate": "GPU 显存",
    "cpu_usage_rate": "CPU 利用率",
    "memory_usage_rate": "内存",
}
_METRIC_ORDER = ["gpu_usage_rate", "gpu_memory_usage_rate", "cpu_usage_rate", "memory_usage_rate"]


def _extract_values(series: list[dict]) -> list[float]:
    """Extract numeric values from time series data points."""
    return [float(p.get("data", 0)) for p in series if p.get("data") is not None]


@click.command("show")
@click.argument("job_id")
@click.option(
    "--time-range", "-t",
    type=click.Choice(list(_TIME_RANGE_SECONDS.keys())),
    default="30m",
    help="Time window for metrics (default: 30m)",
)
@click.option(
    "--mode", "-m",
    type=click.Choice(["summary", "raw"]),
    default="summary",
    help="Output mode: summary (statistics) or raw (time-series) (default: summary)",
)
@click.option(
    "--interval", "-i",
    type=int,
    help="Sampling interval in seconds (default: auto)",
)
@pass_context
def show(
    ctx: Context,
    job_id: str,
    time_range: str,
    mode: str,
    interval: int,
) -> None:
    """Show training resource metrics for a GPU job.

    Fetches GPU utilization, GPU memory, CPU usage, and memory usage
    metrics from the v2 API and displays statistical summaries.

    \b
    Examples:
        inspire metrics show job-abc123
        inspire metrics show job-abc123 --time-range 1h
        inspire metrics show job-abc123 --mode raw
        inspire metrics show job-abc123 --json
    """
    try:
        config, _ = Config.from_files_and_env()

        from inspire.platform.web.session import get_v2_token
        token = get_v2_token(config)
        if not token:
            _handle_error(
                ctx, "AuthenticationError",
                "v2 API authentication required for metrics. Set INSPIRE_USERNAME and INSPIRE_PASSWORD.",
                EXIT_AUTH_ERROR,
            )
            return

        metric_groups, compute_group_id, job_status = fetch_job_metrics(
            token, config.base_url, job_id,
            time_range=time_range, interval=interval,
        )

        if not compute_group_id:
            _handle_error(
                ctx, "ValidationError",
                "Cannot determine compute group for this job",
                EXIT_CONFIG_ERROR,
            )
            return

        duration_s = _TIME_RANGE_SECONDS.get(time_range, 1800)
        interval_s = interval or max(10, duration_s // 30)

        if ctx.json_output:
            click.echo(json_formatter.format_json({
                "job_id": job_id,
                "compute_group_id": compute_group_id,
                "time_range": time_range,
                "interval_s": interval_s,
                "metrics": metric_groups,
            }))
            return

        # Human output
        if not metric_groups:
            click.echo(f"No metrics data for job {job_id} in the last {time_range}.")
            click.echo("The job may not have started running yet, or metrics data has been pruned.")
            return

        summaries: dict[str, MetricSummary] = {}
        for group in metric_groups:
            metric_type = group.get("metric_type", "")
            time_series = group.get("time_series", [])
            values = _extract_values(time_series)
            if values:
                summaries[metric_type] = compute_summary(values)

        click.echo(f"Metrics for {job_id} (last {time_range}, interval={interval_s}s)")
        click.echo("")

        if mode == "raw":
            for group in metric_groups:
                metric_type = group.get("metric_type", "")
                label = _METRIC_LABELS.get(metric_type, metric_type)
                click.echo(f"  {label}:")
                for pt in group.get("time_series", []):
                    ts = pt.get("timestamp", "")
                    val = pt.get("data", 0)
                    click.echo(f"    {ts}  {val:.4f}")
                click.echo("")
        else:
            click.echo(f"{'Metric':<20} {'Avg':>8} {'P50':>8} {'P90':>8} {'Min':>8} {'Max':>8} {'Std':>8} {'Trend':>8}")
            click.echo("-" * 80)
            for metric_type in _METRIC_TYPES:
                s = summaries.get(metric_type)
                label = _METRIC_LABELS.get(metric_type, metric_type)
                if s:
                    click.echo(
                        f"{label:<20} {s.avg:>7.1%} {s.p50:>7.1%} {s.p90:>7.1%} "
                        f"{s.min:>7.1%} {s.max:>7.1%} {s.stddev:>7.3f} {s.trend:>8}"
                    )
                else:
                    click.echo(f"{label:<20} {'--':>8} {'--':>8} {'--':>8} {'--':>8} {'--':>8} {'--':>8} {'--':>8}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
