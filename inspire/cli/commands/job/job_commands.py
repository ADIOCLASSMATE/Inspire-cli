"""Job subcommands (excluding create/logs)."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from typing import Optional

import click

from . import job_deps
from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_AUTH_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_GENERAL_ERROR,
    EXIT_JOB_NOT_FOUND,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
    pass_context,
)
from inspire.cli.formatters import human_formatter, json_formatter
from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web.session import SessionExpiredError
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.job_cli import resolve_job_id
from inspire.cli.utils.status_normalizer import (
    analyze_timeline,
    classify_job_id,
    format_duration,
    normalize_status,
)
from inspire.config import Config, ConfigError

logger = logging.getLogger(__name__)

_ACTIVE_EXCLUDE_STATUSES = {
    "FAILED",
    "job_failed",
    "CANCELLED",
    "job_cancelled",
    "job_stopped",
}


def _watch_jobs(
    ctx: Context,
    config: Config,
    limit: int,
    status: Optional[str],
    active: bool,
    interval: int,
) -> None:
    """Continuously poll and display job status with incremental updates."""
    api_logger = logging.getLogger("inspire.inspire_api_control")
    original_level = api_logger.level
    api_logger.setLevel(logging.CRITICAL)

    cache = job_deps.JobCache(config.get_expanded_cache_path())

    exclude_statuses = None
    if active:
        exclude_statuses = _ACTIVE_EXCLUDE_STATUSES

    terminal_statuses = {
        "SUCCEEDED",
        "job_succeeded",
        "FAILED",
        "job_failed",
        "CANCELLED",
        "job_cancelled",
        "job_stopped",
    }

    completed_this_session: list = []
    completed_job_ids: set = set()

    def _progress_bar(current: int, total: int, width: int = 20) -> str:
        if total == 0:
            return "░" * width
        filled = int(width * current / total)
        return "█" * filled + "░" * (width - filled)

    def _render_display(
        jobs_list: list,
        updated_count: int,
        total_count: int,
        completed_list: list,
    ) -> None:
        if not ctx.json_output:
            os.system("clear")
        if ctx.json_output:
            timestamp = datetime.now().strftime("%H:%M:%S")
            click.echo(
                json_formatter.format_json(
                    {
                        "event": "refresh",
                        "timestamp": timestamp,
                        "updated": updated_count,
                        "total": total_count,
                        "jobs": jobs_list,
                        "completed_this_session": completed_list,
                    }
                )
            )
        else:
            bar = _progress_bar(updated_count, total_count)
            if updated_count < total_count:
                click.echo(f"🔄 [{bar}] {updated_count}/{total_count} updating...\n")
            else:
                click.echo(f"✅ [{bar}] {total_count}/{total_count} done (interval: {interval}s)\n")

            click.echo(human_formatter.format_job_list(jobs_list))

            if completed_list:
                click.echo(f"\n✅ Completed This Session ({len(completed_list)})")
                click.echo("─" * 60)
                for job_item in completed_list:
                    status_emoji = (
                        "✅" if "succeeded" in job_item.get("status", "").lower() else "❌"
                    )
                    click.echo(
                        f"{job_item.get('job_id', 'N/A')[:36]:36}  "
                        f"{job_item.get('name', 'N/A')[:20]:20}  "
                        f"{status_emoji} {job_item.get('status', 'N/A')}"
                    )

    try:
        while True:
            jobs = cache.list_jobs(limit=limit, status=status, exclude_statuses=exclude_statuses)
            total = len(jobs)

            _render_display(jobs, 0, total, completed_this_session)

            for i, job_item in enumerate(jobs):
                job_id = job_item.get("job_id")
                if job_id:
                    original_status = job_item.get("status", "")
                    try:
                        result = browser_api_module.get_job_detail(job_id)
                        data = result.get("data", {})
                        new_status = data.get("status")
                        if new_status:
                            job_item["status"] = new_status
                            cache.update_status(job_id, new_status)

                            if (
                                new_status in terminal_statuses
                                and original_status not in terminal_statuses
                                and job_id not in completed_job_ids
                            ):
                                completed_this_session.append(dict(job_item))
                                completed_job_ids.add(job_id)
                    except Exception:
                        logger.debug("Failed to refresh job %s", job_id, exc_info=True)

                _render_display(jobs, i + 1, total, completed_this_session)

                if i < total - 1:
                    job_deps.time.sleep(1.0)

            if active and exclude_statuses:
                filtered = [j for j in jobs if j.get("status") not in exclude_statuses]
                if len(filtered) != len(jobs):
                    _render_display(filtered, total, total, completed_this_session)

            job_deps.time.sleep(interval)

    except KeyboardInterrupt:
        if not ctx.json_output:
            click.echo("\nStopped watching.")
        sys.exit(EXIT_SUCCESS)
    finally:
        api_logger.setLevel(original_level)


@click.command("list")
@click.option("--limit", "-n", type=int, default=10, help="Max jobs to show (default: 10)")
@click.option("--status", "-s", help="Filter by status (PENDING, RUNNING, SUCCEEDED, FAILED)")
@click.option(
    "--active",
    "-a",
    is_flag=True,
    help="Show only active jobs (exclude failed, cancelled, stopped)",
)
@click.option("--watch", "-w", is_flag=True, help="Continuously refresh job list")
@click.option(
    "--interval",
    type=int,
    default=10,
    help="Refresh interval in seconds for --watch (default: 10)",
)
@click.option(
    "--v2/--no-v2",
    is_flag=True,
    default=True,
    help="Use v2 API for real-time listing (default: True, falls back to cache)",
)
@click.option(
    "--workspace",
    "-ws",
    help="Workspace ID for v2 API listing",
)
@pass_context
def list_jobs(
    ctx: Context,
    limit: int,
    status: Optional[str],
    active: bool,
    watch: bool,
    interval: int,
    v2: bool,
    workspace: Optional[str],
) -> None:
    """List recent jobs from local cache or v2 API.

    With --v2 (default), fetches live job data from the v2 ListJobs API.
    Falls back to local cache if v2 is unavailable.

    \b
    Example:
        inspire job list
        inspire job list --limit 20 --status RUNNING
        inspire job list --active
        inspire job list --watch --active -n 20
        inspire job list --watch --interval 5
        inspire job list --v2 --workspace ws-xxx
    """
    try:
        config, _ = Config.from_files_and_env()

        if watch:
            _watch_jobs(
                ctx=ctx,
                config=config,
                limit=limit,
                status=status,
                active=active,
                interval=interval,
            )
            return

        # Try v2 API first
        if v2:
            try:
                from inspire.platform.web.v2_api.train import list_jobs as v2_list_jobs
                from inspire.platform.web.session import get_v2_token

                token = get_v2_token(config)
                if token:
                    ws_id = workspace or config.job_workspace_id
                    if ws_id:
                        v2_jobs, _ = v2_list_jobs(
                            token, config.base_url, ws_id,
                            page_size=limit, status=status,
                        )
                        job_dicts = [
                            {
                                "job_id": j.job_id,
                                "name": j.name,
                                "status": normalize_status(j.status).raw,
                                "created_at": j.created_at,
                            }
                            for j in v2_jobs
                        ]
                        if ctx.json_output:
                            click.echo(json_formatter.format_json(job_dicts))
                        else:
                            click.echo(human_formatter.format_job_list(job_dicts))
                        return
            except Exception:
                # Fall through to cache
                pass

        cache = job_deps.JobCache(config.get_expanded_cache_path())

        exclude_statuses = None
        if active:
            exclude_statuses = _ACTIVE_EXCLUDE_STATUSES

        jobs = cache.list_jobs(limit=limit, status=status, exclude_statuses=exclude_statuses)

        if ctx.json_output:
            click.echo(json_formatter.format_json(jobs))
        else:
            click.echo(human_formatter.format_job_list(jobs))

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "Error", str(e), EXIT_GENERAL_ERROR)


@click.command("status")
@click.argument("job_id")
@pass_context
def status(ctx: Context, job_id: str) -> None:
    """Check the status of a training job.

    \b
    Example:
        inspire job status job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf
    """
    job_id = resolve_job_id(ctx, job_id)

    try:
        config, _ = Config.from_files_and_env()


        result = browser_api_module.get_job_detail(job_id)
        job_data = result.get("data", {})

        if job_data.get("status"):
            cache = job_deps.JobCache(config.get_expanded_cache_path())
            cache.update_status(job_id, job_data["status"])

        if ctx.json_output:
            click.echo(json_formatter.format_json(job_data))
        else:
            click.echo(human_formatter.format_job_status(job_data))

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except SessionExpiredError as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        msg = str(e).lower()
        if "not found" in msg or "invalid job id" in msg:
            _handle_error(ctx, "JobNotFound", str(e), EXIT_JOB_NOT_FOUND)
        else:
            _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


@click.command("stop")
@click.argument("job_id", required=False)
@click.option("--workspace", "-w", help="Workspace ID for batch stop")
@click.option(
    "--status", "-s",
    default="running",
    help="Status filter for batch stop (default: running)",
)
@click.option("--project", "-p", help="Project ID filter for batch stop")
@click.option("--dry-run", is_flag=True, help="Preview what would be stopped")
@pass_context
def stop(
    ctx: Context,
    job_id: str | None,
    workspace: str | None,
    status: str,
    project: str | None,
    dry_run: bool,
) -> None:
    """Stop a running training job by ID, or batch-stop by status filter.

    \b
    Single stop:
        inspire job stop job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf

    Batch stop:
        inspire job stop --workspace ws-xxx --status running
        inspire job stop --workspace ws-xxx --status all --dry-run
    """
    try:
        config, _ = Config.from_files_and_env()

        # Single job stop
        if job_id:
            job_id = resolve_job_id(ctx, job_id)
            browser_api_module.stop_job(job_id)
            cache = job_deps.JobCache(config.get_expanded_cache_path())
            cache.update_status(job_id, "CANCELLED")
            if ctx.json_output:
                click.echo(json_formatter.format_json({"job_id": job_id, "status": "stopped"}))
            else:
                click.echo(human_formatter.format_success(f"Job stopped: {job_id}"))
            return

        # Batch stop
        if not workspace:
            _handle_error(
                ctx, "ValidationError",
                "Either JOB_ID or --workspace is required for batch stop.",
                EXIT_CONFIG_ERROR,
            )
            return

        # Try v2 API for listing and stopping
        from inspire.platform.web.v2_api.train import list_jobs as v2_list_jobs, stop_job as v2_stop_train
        from inspire.platform.web.v2_api.inference import stop_inference as v2_stop_inference
        from inspire.platform.web.session import get_v2_token

        token = get_v2_token(config)
        if not token:
            _handle_error(
                ctx, "AuthenticationError",
                "v2 API authentication required for batch stop. Set INSPIRE_USERNAME and INSPIRE_PASSWORD.",
                EXIT_AUTH_ERROR,
            )
            return

        api_status = None if status == "all" else status
        jobs, total = v2_list_jobs(token, config.base_url, workspace, page_size=200, status=api_status)
        if project:
            jobs = [j for j in jobs if j.project_id == project]

        if not jobs:
            click.echo("No matching jobs found.")
            return

        if dry_run:
            click.echo(f"Would stop {len(jobs)} job(s):")
            for j in jobs:
                job_type = classify_job_id(j.job_id)
                click.echo(f"  {j.job_id}  {j.name[:30]}  {j.status}  [{job_type}]")
            return

        stopped = 0
        errors: list[dict] = []
        skipped = 0
        for j in jobs:
            job_type = classify_job_id(j.job_id)
            try:
                if job_type == "gpu":
                    v2_stop_train(token, config.base_url, j.job_id)
                elif job_type == "inference":
                    v2_stop_inference(token, config.base_url, j.job_id)
                elif job_type == "hpc":
                    skipped += 1
                    errors.append({
                        "job_id": j.job_id,
                        "error": f"HPC job stop not yet supported via v2 API (type={job_type}). Use the web UI.",
                    })
                    continue
                else:
                    skipped += 1
                    errors.append({
                        "job_id": j.job_id,
                        "error": f"Unknown job type '{job_type}' — cannot determine stop API.",
                    })
                    continue
                stopped += 1
            except Exception as e:
                errors.append({"job_id": j.job_id, "error": str(e)})

        if ctx.json_output:
            click.echo(json_formatter.format_json({
                "total": len(jobs),
                "stopped": stopped,
                "errors": errors,
            }))
        else:
            click.echo(f"Stopped {stopped}/{len(jobs)} job(s)")
            if errors:
                click.echo("\nErrors:")
                for err in errors:
                    click.echo(f"  {err['job_id']}: {err['error']}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except SessionExpiredError as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        msg = str(e).lower()
        if "not found" in msg or "invalid job id" in msg:
            _handle_error(ctx, "JobNotFound", str(e), EXIT_JOB_NOT_FOUND)
        else:
            _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


@click.command("wait")
@click.argument("job_id")
@click.option("--timeout", type=int, default=14400, help="Timeout in seconds (default: 4 hours)")
@click.option("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
@pass_context
def wait(ctx: Context, job_id: str, timeout: int, interval: int) -> None:
    """Wait for a job to complete.

    Polls the job status until it reaches a terminal state
    (SUCCEEDED, FAILED, or CANCELLED).

    \b
    Example:
        inspire job wait job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --timeout 7200
    """
    job_id = resolve_job_id(ctx, job_id)

    try:
        config, _ = Config.from_files_and_env()

        cache = job_deps.JobCache(config.get_expanded_cache_path())

        terminal_statuses = {
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "job_succeeded",
            "job_failed",
            "job_cancelled",
        }
        start_time = job_deps.time.time()
        last_status = None

        if not ctx.json_output:
            click.echo(f"Waiting for job {job_id} (timeout: {timeout}s, interval: {interval}s)")

        while True:
            elapsed = job_deps.time.time() - start_time

            if elapsed > timeout:
                _handle_error(ctx, "Timeout", f"Timeout after {timeout}s", EXIT_TIMEOUT)
                return

            try:
                result = browser_api_module.get_job_detail(job_id)
                job_data = result.get("data", {})
                current_status = job_data.get("status", "UNKNOWN")

                cache.update_status(job_id, current_status)

                if current_status != last_status:
                    if ctx.json_output:
                        click.echo(
                            json_formatter.format_json(
                                {
                                    "event": "status_change",
                                    "status": current_status,
                                    "elapsed_seconds": int(elapsed),
                                }
                            )
                        )
                    else:
                        click.echo(f"\nStatus: {current_status}")
                    last_status = current_status
                else:
                    if not ctx.json_output:
                        mins = int(elapsed // 60)
                        secs = int(elapsed % 60)
                        click.echo(
                            f"\r[{mins:02d}:{secs:02d}] Waiting... Status: {current_status}",
                            nl=False,
                        )

                if current_status in terminal_statuses:
                    if ctx.json_output:
                        click.echo(json_formatter.format_json(job_data))
                    else:
                        click.echo("")
                        click.echo(human_formatter.format_job_status(job_data))

                    if current_status in {"SUCCEEDED", "job_succeeded"}:
                        sys.exit(EXIT_SUCCESS)
                    sys.exit(EXIT_GENERAL_ERROR)

            except Exception as e:
                if not ctx.json_output:
                    click.echo(f"\nWarning: Failed to get status: {e}")

            job_deps.time.sleep(interval)

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except SessionExpiredError as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except KeyboardInterrupt:
        if not ctx.json_output:
            click.echo("\nInterrupted")
        sys.exit(EXIT_GENERAL_ERROR)


@click.command("update")
@click.option(
    "--status",
    "-s",
    multiple=True,
    help="Status filter (default: PENDING,RUNNING + API aliases). Repeatable.",
)
@click.option(
    "--limit",
    "-n",
    type=int,
    default=10,
    help="Max jobs to refresh from cache (default: 10)",
)
@click.option(
    "--delay",
    "-d",
    type=float,
    default=0.6,
    help="Delay between API requests in seconds to avoid rate limits (default: 0.6)",
)
@pass_context
def update_jobs(ctx: Context, status: tuple, limit: int, delay: float) -> None:
    """Update cached jobs by polling the API.

    Refreshes statuses for cached jobs matching the status filter
    (defaults to PENDING/RUNNING/QUEUING and API snake_case aliases) and
    updates the local cache. Skips jobs that fail to refresh and
    reports them.
    """
    default_statuses = ("PENDING", "RUNNING", "QUEUING") if not status else tuple(status)
    alias_map = {
        "PENDING": {"PENDING", "job_pending", "job_creating"},
        "RUNNING": {"RUNNING", "job_running"},
        "QUEUING": {"QUEUING", "job_queuing"},
        "SUCCEEDED": {"SUCCEEDED", "job_succeeded"},
        "FAILED": {"FAILED", "job_failed"},
        "CANCELLED": {"CANCELLED", "job_cancelled"},
    }
    statuses_set = set()
    for s in default_statuses:
        key = str(s).upper()
        statuses_set.update(alias_map.get(key, {s}))

    try:
        config, _ = Config.from_files_and_env()

        cache = job_deps.JobCache(config.get_expanded_cache_path())

        jobs = cache.list_jobs(limit=limit)
        jobs = [j for j in jobs if j.get("status") in statuses_set]

        updated = []
        errors = []

        for job in jobs:
            job_id = job.get("job_id")
            if not job_id:
                continue
            old_status = job.get("status", "UNKNOWN")
            try:
                result = browser_api_module.get_job_detail(job_id)
                data = result.get("data", {}) if isinstance(result, dict) else {}
                new_status = data.get("status") or data.get("job_status") or old_status
                if new_status:
                    cache.update_status(job_id, new_status)
                updated.append(
                    {
                        "job_id": job_id,
                        "old_status": old_status,
                        "new_status": new_status,
                    }
                )
            except Exception as e:  # noqa: BLE001
                errors.append({"job_id": job_id, "error": str(e)})
            if delay > 0:
                job_deps.time.sleep(delay)

        if ctx.json_output:
            payload = {
                "updated": updated,
                "errors": errors,
            }
            click.echo(json_formatter.format_json(payload))
            return

        if updated:
            refreshed_jobs = [cache.get_job(u["job_id"]) for u in updated]
            refreshed_jobs = [j for j in refreshed_jobs if j]
            click.echo(human_formatter.format_job_list(refreshed_jobs))
        else:
            click.echo("\nNo matching jobs to update.\n")

        if errors:
            click.echo("\nErrors during update:")
            for err in errors:
                click.echo(f"- {err['job_id']}: {err['error']}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except SessionExpiredError as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


@click.command("command")
@click.argument("job_id")
@pass_context
def show_command(ctx: Context, job_id: str) -> None:
    """Show the training command used for a job."""
    job_id = resolve_job_id(ctx, job_id)

    cached_command = None
    cache = job_deps.JobCache(os.getenv("INSPIRE_JOB_CACHE"))
    cached_job = cache.get_job(job_id)
    if cached_job:
        cached_command = cached_job.get("command")

    command_value = None
    source = None

    try:
        config, _ = Config.from_files_and_env()


        result = browser_api_module.get_job_detail(job_id)
        job_data = result.get("data", {})
        command_value = job_data.get("command")
        if command_value:
            source = "api"
    except ConfigError as e:
        if not cached_command:
            _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
            return
    except SessionExpiredError as e:
        if not cached_command:
            _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
            return
    except Exception as e:
        if not cached_command:
            msg = str(e).lower()
            if "not found" in msg or "invalid job id" in msg:
                _handle_error(ctx, "JobNotFound", str(e), EXIT_JOB_NOT_FOUND)
            else:
                _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
            return

    if not command_value and cached_command:
        command_value = cached_command
        source = "cache"

    if not command_value:
        _handle_error(
            ctx,
            "CommandNotFound",
            f"No command found for job {job_id}",
            EXIT_API_ERROR,
        )
        return

    if ctx.json_output:
        payload = {"job_id": job_id, "command": command_value}
        if source:
            payload["source"] = source
        click.echo(json_formatter.format_json(payload))
    else:
        click.echo(command_value)


@click.command("detail")
@click.argument("job_id")
@click.option(
    "--v2/--no-v2",
    is_flag=True,
    default=True,
    help="Use v2 API for rich detail (default: True)",
)
@pass_context
def detail(ctx: Context, job_id: str, v2: bool) -> None:
    """Show detailed job information with diagnostics.

    Uses v2 API by default for richer data (timeline, framework config,
    resource specs). Falls back to v1 browser API for diagnostic analysis.

    \b
    Example:
        inspire job detail job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf
    """
    job_id = resolve_job_id(ctx, job_id)

    try:
        config, _ = Config.from_files_and_env()
        job_data: dict = {}
        source = "v1"

        # Try v2 first
        if v2:
            try:
                from inspire.platform.web.v2_api.train import get_job_detail as v2_get_job
                from inspire.platform.web.session import get_v2_token

                token = get_v2_token(config)
                if token:
                    job_data = v2_get_job(token, config.base_url, job_id)
                    source = "v2"
            except Exception:
                pass

        # Fallback to v1
        if not job_data:
            try:
                result = browser_api_module.get_job_detail(job_id)
                job_data = result.get("data", {})
            except SessionExpiredError:
                _handle_error(ctx, "AuthenticationError", "Session expired", EXIT_AUTH_ERROR)
                return

        if not job_data:
            _handle_error(ctx, "JobNotFound", f"Job {job_id} not found", EXIT_JOB_NOT_FOUND)
            return

        # Analyze timeline
        timeline = job_data.get("timeline")
        timeline_analysis = analyze_timeline(timeline)

        # Framework config
        fc = job_data.get("framework_config", [{}])
        first_fc = fc[0] if fc else {}
        gpu_count = first_fc.get("gpu_count", 0)
        instance_count = first_fc.get("instance_count", 1)
        image = first_fc.get("image", "")

        # Build diagnostics
        diagnostics: list[str] = []
        cmd = str(job_data.get("command", ""))
        status_norm = normalize_status(job_data.get("status", ""))

        if timeline_analysis.never_started:
            diagnostics.append("Task never started running. Possible causes:")
            diagnostics.append("  - Image pull failure (check image exists and is registered)")
            diagnostics.append("  - spec_id mismatch (verify the spec/quotas for this compute group)")
            diagnostics.append("  - Scheduling anomaly (no matching nodes available)")

        run_ms = timeline_analysis.run_ms or 0
        if run_ms > 0 and run_ms < 30000 and status_norm.family == "failed":
            diagnostics.append("Task failed in <30s. Likely causes:")
            diagnostics.append("  - Command error or missing dependencies")
            diagnostics.append("  - Conda environment not initialized (add: source /opt/conda/etc/profile.d/conda.sh && conda activate <env>)")
            diagnostics.append("  - Working directory or file path incorrect")

        queue_ms = timeline_analysis.queue_ms or 0
        if queue_ms > 1800000:  # 30 min
            diagnostics.append(f"Task queued for {format_duration(queue_ms)} (>30min). Consider:")
            diagnostics.append("  - Image pull may be slow or failing")
            diagnostics.append("  - Cluster may be at capacity — check inspire resources allocate")
            diagnostics.append("  - Contact platform ops if this persists")

        if status_norm.family in ("failed", "stopped") and status_norm.family != "succeeded":
            if "/inspire/hdd/project/" not in cmd:
                diagnostics.append("Command may use incorrect paths. Use /inspire/hdd/project/{en_name}/ for storage.")

            if "pip install" in cmd or "git clone" in cmd or "wget " in cmd or "curl " in cmd:
                diagnostics.append("Network operations (pip/git/wget/curl) detected in command.")
                diagnostics.append("  - Offline workspaces (分布式训练空间) have NO internet.")
                diagnostics.append("  - Install dependencies in the Docker image instead.")

            if instance_count > 1 and gpu_count > 1:
                diagnostics.append("Multi-node multi-GPU job. Check:")
                diagnostics.append("  - NCCL environment variables (MASTER_ADDR, MASTER_PORT, etc.)")
                diagnostics.append("  - Shared memory size (shm >= 64GB recommended for multi-GPU)")
                diagnostics.append("  - Use: export NCCL_IB_DISABLE=1 if InfiniBand is unreliable")

        # Output
        if ctx.json_output:
            payload = {
                "job_id": job_id,
                "name": job_data.get("name", ""),
                "status": job_data.get("status", ""),
                "status_family": status_norm.family,
                "command": cmd,
                "priority": job_data.get("priority", job_data.get("task_priority", 0)),
                "gpu_count": gpu_count,
                "instance_count": instance_count,
                "image": image,
                "project_name": job_data.get("project_name", ""),
                "workspace_name": job_data.get("workspace_name", ""),
                "compute_group_name": job_data.get("logic_compute_group_name", ""),
                "created_at": job_data.get("created_at", ""),
                "finished_at": job_data.get("finished_at"),
                "timeline": {
                    "summary": timeline_analysis.summary,
                    "queue_ms": timeline_analysis.queue_ms,
                    "run_ms": timeline_analysis.run_ms,
                    "never_started": timeline_analysis.never_started,
                },
                "diagnostics": diagnostics,
                "source": source,
            }
            click.echo(json_formatter.format_json(payload))
        else:
            click.echo(_format_job_detail_human(
                job_data=job_data,
                status_norm=status_norm,
                timeline=timeline_analysis,
                diagnostics=diagnostics,
                gpu_count=gpu_count,
                instance_count=instance_count,
                image=image,
                source=source,
            ))
            if diagnostics:
                click.echo("\nDiagnostics:")
                for d in diagnostics:
                    click.echo(f"  {d}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        msg = str(e).lower()
        if "not found" in msg or "invalid job id" in msg:
            _handle_error(ctx, "JobNotFound", str(e), EXIT_JOB_NOT_FOUND)
        else:
            _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


def _format_job_detail_human(
    *,
    job_data: dict,
    status_norm,
    timeline,
    diagnostics: list[str],
    gpu_count: int,
    instance_count: int,
    image: str,
    source: str,
) -> str:
    """Format job detail as human-readable output."""
    lines = [
        "Job Detail",
        f"  Job ID:     {job_data.get('job_id', 'N/A')}",
        f"  Name:       {job_data.get('name', 'N/A')}",
        f"  Status:     {job_data.get('status', 'N/A')} ({status_norm.family})",
        f"  Priority:   {job_data.get('priority', job_data.get('task_priority', 'N/A'))}",
        f"  GPU:        {gpu_count}x ({instance_count} node(s))",
        f"  Image:      {image or 'N/A'}",
        f"  Project:    {job_data.get('project_name', 'N/A')}",
        f"  Workspace:  {job_data.get('workspace_name', 'N/A')}",
        f"  Compute:    {job_data.get('logic_compute_group_name', 'N/A')}",
        f"  Created:    {job_data.get('created_at', 'N/A')}",
    ]
    if job_data.get("finished_at"):
        lines.append(f"  Finished:   {job_data['finished_at']}")
    lines.append(f"  Data Source: {source}")
    lines.append("")
    lines.append(f"  Timeline: {timeline.summary}")
    if timeline.queue_ms is not None:
        lines.append(f"    Queue time: {format_duration(timeline.queue_ms)}")
    if timeline.run_ms is not None:
        lines.append(f"    Run time:   {format_duration(timeline.run_ms)}")
    if job_data.get("command"):
        cmd = str(job_data["command"])
        lines.append(f"\n  Command: {cmd[:200]}")
    return "\n".join(lines)


__all__ = [
    "detail",
    "list_jobs",
    "show_command",
    "status",
    "stop",
    "update_jobs",
    "wait",
]
