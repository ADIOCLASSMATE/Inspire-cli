"""Job logs command.

Implements `inspire job logs` including:
- Single-job mode (with JOB_ID)
- Bulk mode (without JOB_ID)

Logs are fetched by executing a read command on a running notebook
via `inspire notebook exec`.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

import click

from . import job_deps
from inspire.cli.context import (
    Context,
    EXIT_CONFIG_ERROR,
    EXIT_GENERAL_ERROR,
    EXIT_JOB_NOT_FOUND,
    EXIT_LOG_NOT_FOUND,
    EXIT_SUCCESS,
    EXIT_VALIDATION_ERROR,
    pass_context,
)
from inspire.cli.formatters import json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.job_cli import resolve_job_id
from inspire.config import Config, ConfigError


class _JobCacheProtocol(Protocol):
    def get_log_offset(self, job_id: str) -> int: ...

    def reset_log_offset(self, job_id: str) -> None: ...

    def set_log_offset(self, job_id: str, offset: int) -> None: ...


@dataclass(frozen=True)
class JobLogCachePaths:
    cache_path: Path
    legacy_cache_path: Path


def _build_log_cache_paths(config: Config, job_id: str) -> JobLogCachePaths:
    cache_dir = Path(os.path.expanduser(config.log_cache_dir))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return JobLogCachePaths(
        cache_path=cache_dir / f"{job_id}.log",
        legacy_cache_path=cache_dir / f"job-{job_id}.log",
    )


def _migrate_legacy_log_filename(paths: JobLogCachePaths) -> Path:
    cache_path = paths.cache_path
    legacy_cache_path = paths.legacy_cache_path

    if not cache_path.exists() and legacy_cache_path.exists():
        try:
            legacy_cache_path.replace(cache_path)
            return cache_path
        except OSError:
            return legacy_cache_path

    return cache_path


def _get_current_log_offset(
    cache: _JobCacheProtocol,
    *,
    job_id: str,
    cache_path: Path,
    refresh: bool,
) -> int:
    current_offset = 0 if refresh else cache.get_log_offset(job_id)

    if current_offset > 0 and not cache_path.exists():
        cache.reset_log_offset(job_id)
        return 0

    return current_offset


def _update_log_offset_to_filesize(
    cache: _JobCacheProtocol, *, job_id: str, cache_path: Path
) -> None:
    if cache_path.exists():
        cache.set_log_offset(job_id, cache_path.stat().st_size)


def _echo_log_path(ctx: Context, *, job_id: str, remote_log_path: str) -> None:
    if ctx.json_output:
        click.echo(json_formatter.format_json({"job_id": job_id, "log_path": remote_log_path}))
    else:
        click.echo(remote_log_path)


def _echo_file_tail(ctx: Context, *, cache_path: Path, tail: int) -> None:
    with cache_path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    tail_lines = lines[-tail:] if tail > 0 else lines

    if ctx.json_output:
        click.echo(
            json_formatter.format_json(
                {
                    "log_path": str(cache_path),
                    "lines": tail_lines,
                    "count": len(tail_lines),
                }
            )
        )
    else:
        click.echo(f"=== Last {len(tail_lines)} lines ===\n")
        for line in tail_lines:
            click.echo(line)


def _echo_file_head(ctx: Context, *, cache_path: Path, head: int) -> None:
    with cache_path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    head_lines = lines[:head] if head > 0 else lines

    if ctx.json_output:
        click.echo(
            json_formatter.format_json(
                {
                    "log_path": str(cache_path),
                    "lines": head_lines,
                    "count": len(head_lines),
                }
            )
        )
    else:
        click.echo(f"=== First {len(head_lines)} lines ===\n")
        for line in head_lines:
            click.echo(line)


def _echo_file_content(ctx: Context, *, cache_path: Path) -> None:
    content = cache_path.read_text(encoding="utf-8", errors="replace")

    if ctx.json_output:
        click.echo(
            json_formatter.format_json(
                {
                    "log_path": str(cache_path),
                    "content": content,
                    "size_bytes": len(content),
                }
            )
        )
    else:
        click.echo(content)


def _fetch_and_cache_log_via_notebook(
    ctx: Context,
    *,
    notebook_id: str,
    remote_log_path: str,
    cache_path: Path,
) -> None:
    """Fetch the full log via notebook exec and write it to the local cache.

    Uses a subprocess invocation of the CLI so we can capture the output
    for caching without run_notebook_exec calling sys.exit.
    """
    import subprocess

    cmd = [
        sys.executable,
        "-m",
        "inspire.cli.main",
        "notebook",
        "exec",
        notebook_id,
        f"cat '{remote_log_path}'",
        "--timeout",
        "120",
    ]
    if ctx.json_output:
        cmd.append("--json")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=180,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise IOError(
            f"Failed to read log via notebook exec (exit code {result.returncode}): {stderr}"
        )

    content = result.stdout
    if content:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(content, encoding="utf-8")


def _follow_logs_via_notebook(
    ctx: Context,
    *,
    notebook_id: str,
    job_id: str,
    config: Config,
    remote_log_path: str,
    cache_path: Path,
    tail_lines: int = 50,
    interval: int = 30,
) -> int:
    """Follow logs by polling via notebook exec at regular intervals."""
    from inspire.cli.utils.auth import AuthManager

    api = AuthManager.get_api(config)
    terminal_statuses = {
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "job_succeeded",
        "job_failed",
        "job_cancelled",
    }
    final_status = None

    try:
        if not ctx.json_output:
            click.echo(f"Following log for job {job_id} via notebook exec (interval: {interval}s)")
            click.echo("Press Ctrl+C to stop\n")

        # Show initial tail
        try:
            _fetch_and_cache_log_via_notebook(
                ctx,
                notebook_id=notebook_id,
                remote_log_path=remote_log_path,
                cache_path=cache_path,
            )
        except IOError as e:
            if not ctx.json_output:
                click.echo(f"Warning: initial fetch failed: {e}", err=True)

        if cache_path.exists():
            if ctx.json_output:
                content = cache_path.read_text(encoding="utf-8", errors="replace")
                click.echo(
                    json_formatter.format_json(
                        {
                            "event": "initial_content",
                            "job_id": job_id,
                            "size_bytes": len(content),
                            "content": content,
                        }
                    )
                )
            else:
                lines = cache_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[-tail_lines:]:
                    click.echo(line)

        last_size = cache_path.stat().st_size if cache_path.exists() else 0

        while True:
            import time

            time.sleep(interval)

            try:
                _fetch_and_cache_log_via_notebook(
                    ctx,
                    notebook_id=notebook_id,
                    remote_log_path=remote_log_path,
                    cache_path=cache_path,
                )
                current_size = cache_path.stat().st_size if cache_path.exists() else 0
                bytes_added = current_size - last_size

                if bytes_added > 0:
                    with cache_path.open("rb") as f:
                        f.seek(last_size)
                        new_content = f.read().decode("utf-8", errors="replace")

                    if ctx.json_output:
                        click.echo(
                            json_formatter.format_json(
                                {
                                    "event": "new_content",
                                    "job_id": job_id,
                                    "bytes_added": bytes_added,
                                    "content": new_content,
                                }
                            )
                        )
                    else:
                        click.echo(new_content, nl=False)

                    last_size = current_size
            except IOError as e:
                if not ctx.json_output:
                    click.echo(f"\nWarning: Fetch failed: {e}", err=True)

            try:
                result = api.get_job_detail(job_id)
                job_data = result.get("data", {})
                current_status = job_data.get("status", "UNKNOWN")

                if current_status in terminal_statuses:
                    final_status = current_status
                    break
            except Exception as e:
                if not ctx.json_output:
                    click.echo(f"\nWarning: Status check failed: {e}", err=True)

        if final_status:
            if not ctx.json_output:
                click.echo(f"\n--- Job completed with status: {final_status} ---")

        if final_status in {"SUCCEEDED", "job_succeeded"}:
            return EXIT_SUCCESS
        if final_status in {"FAILED", "CANCELLED", "job_failed", "job_cancelled"}:
            return EXIT_GENERAL_ERROR
        return EXIT_SUCCESS

    except KeyboardInterrupt:
        if not ctx.json_output:
            click.echo("\nStopped following.")
        return EXIT_SUCCESS


def _resolve_notebook_for_job(
    ctx: Context,
    *,
    config: Config,
    job_id: str,
    notebook: Optional[str] = None,
) -> Optional[str]:
    """Resolve a notebook ID to use for log fetching.

    If the user provides --notebook, use that.
    Otherwise, try to find a running notebook automatically.
    """
    if notebook:
        return notebook

    # Try to find a running notebook via the API
    try:
        from inspire.cli.utils.auth import AuthManager

        api = AuthManager.get_api(config)
        if hasattr(api, "list_notebooks"):
            result = api.list_notebooks()
            notebooks = result.get("data", result) if isinstance(result, dict) else result
            for nb in notebooks:
                status = nb.get("status", "")
                if status in ("running", "RUNNING", "active", "ACTIVE"):
                    return nb.get("notebook_id") or nb.get("id")
    except Exception:
        pass

    return None


def _bulk_update_logs(
    ctx: Context,
    status: tuple,
    limit: int,
    refresh: bool,
    notebook: Optional[str] = None,
) -> None:
    try:
        config, _ = Config.from_files_and_env(require_credentials=False, require_target_dir=False)
        cache = job_deps.JobCache(config.get_expanded_cache_path())

        alias_map = {
            "PENDING": {"PENDING", "job_pending"},
            "RUNNING": {"RUNNING", "job_running"},
            "SUCCEEDED": {"SUCCEEDED", "job_succeeded"},
            "FAILED": {"FAILED", "job_failed"},
            "CANCELLED": {"CANCELLED", "job_cancelled"},
        }

        status_filter = set()
        if status:
            for s in status:
                key = str(s).upper()
                status_filter.update(alias_map.get(key, {s}))

        jobs = cache.list_jobs(limit=limit)
        if status_filter:
            jobs = [j for j in jobs if j.get("status") in status_filter]

        total_candidates = len(jobs)

        cache_dir = Path(os.path.expanduser(config.log_cache_dir))
        cache_dir.mkdir(parents=True, exist_ok=True)

        updated = []
        errors = []
        skipped_no_log = []

        for job in jobs:
            job_id_item = job.get("job_id")
            remote_log_path_str = job.get("log_path")

            if not job_id_item:
                continue

            if not remote_log_path_str:
                skipped_no_log.append(job_id_item)
                continue

            cache_path = cache_dir / f"{job_id_item}.log"

            try:
                nb_id = _resolve_notebook_for_job(
                    ctx, config=config, job_id=job_id_item, notebook=notebook
                )
                if not nb_id:
                    errors.append({
                        "job_id": job_id_item,
                        "error": (
                            "No notebook available to fetch logs. "
                            "Start a notebook and use --notebook."
                        ),
                    })
                    continue

                _fetch_and_cache_log_via_notebook(
                    ctx,
                    notebook_id=nb_id,
                    remote_log_path=str(remote_log_path_str),
                    cache_path=cache_path,
                )
                updated.append({"job_id": job_id_item, "log_path": str(cache_path)})
            except TimeoutError as e:
                errors.append({"job_id": job_id_item, "error": str(e)})
            except IOError as e:
                errors.append({"job_id": job_id_item, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                errors.append({"job_id": job_id_item, "error": str(e)})

        success_flag = not errors

        payload = {
            "updated": updated,
            "errors": errors,
            "skipped_no_log_path": skipped_no_log,
            "processed": total_candidates,
            "fetched": len(updated),
            "refresh": refresh,
            "status_filter": sorted(status_filter),
            "limit": limit,
        }

        if ctx.json_output:
            click.echo(json_formatter.format_json(payload, success=success_flag))
            if not success_flag:
                sys.exit(EXIT_GENERAL_ERROR)
            return

        if not jobs:
            click.echo("No cached jobs matched the filter.")
            return

        status_label = f" with status in {sorted(status_filter)}" if status_filter else ""
        click.echo(
            f"Updating logs for {total_candidates} cached job(s){status_label} (refresh={refresh})"
        )

        if updated:
            click.echo("\nFetched:")
            for entry in updated:
                click.echo(f"- {entry['job_id']}: {entry['log_path']}")

        if skipped_no_log:
            click.echo("\nSkipped (no log_path in cache): " + ", ".join(skipped_no_log))

        if errors:
            click.echo("\nErrors:")
            for err in errors:
                click.echo(f"- {err['job_id']}: {err['error']}")
            sys.exit(EXIT_GENERAL_ERROR)

        click.echo("\nDone.")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "Error", str(e), EXIT_GENERAL_ERROR)


def _run_job_logs_single_job(
    ctx: Context,
    *,
    job_id: str,
    tail: int | None,
    head: int | None,
    path: bool,
    refresh: bool,
    follow: bool,
    interval: int,
    notebook: Optional[str] = None,
) -> None:
    try:
        config, _ = Config.from_files_and_env(require_credentials=False, require_target_dir=False)
        cache = job_deps.JobCache(config.get_expanded_cache_path())

        cached = cache.get_job(job_id)
        if not cached:
            _handle_error(ctx, "JobNotFound", f"Job not found: {job_id}", EXIT_JOB_NOT_FOUND)
            return

        remote_log_path_str = cached.get("log_path")
        if not remote_log_path_str:
            _handle_error(
                ctx,
                "LogNotFound",
                f"No log file found for job {job_id}",
                EXIT_LOG_NOT_FOUND,
            )
            return

        # Resolve notebook for log fetching
        nb_id = _resolve_notebook_for_job(ctx, config=config, job_id=job_id, notebook=notebook)

        if path:
            _echo_log_path(ctx, job_id=job_id, remote_log_path=str(remote_log_path_str))
            sys.exit(EXIT_SUCCESS)

        cache_paths = _build_log_cache_paths(config, job_id)
        cache_path = _migrate_legacy_log_filename(cache_paths)
        cache_exists = cache_path.exists()

        # If no notebook is available, we cannot fetch remotely
        if not nb_id:
            # If we have a cached log, show it
            if cache_exists:
                if tail:
                    _echo_file_tail(ctx, cache_path=cache_path, tail=tail)
                elif head:
                    _echo_file_head(ctx, cache_path=cache_path, head=head)
                else:
                    _echo_file_content(ctx, cache_path=cache_path)
                return

            _handle_error(
                ctx,
                "NoNotebook",
                (
                    "Cannot fetch logs: no notebook available.\n\n"
                    "To view job logs, do one of the following:\n"
                    "  1. Start a notebook and use: "
                    f"inspire job logs <job_id> --notebook <notebook_id>\n"
                    "  2. Run directly: "
                    f"inspire notebook exec <notebook> \"cat {remote_log_path_str}\""
                ),
                EXIT_GENERAL_ERROR,
            )
            return

        if follow:
            follow_exit_code = _follow_logs_via_notebook(
                ctx=ctx,
                notebook_id=nb_id,
                job_id=job_id,
                config=config,
                remote_log_path=str(remote_log_path_str),
                cache_path=cache_path,
                tail_lines=tail or 50,
                interval=interval,
            )
            sys.exit(follow_exit_code)

        # Fetch the log content via notebook exec
        needs_remote_fetch = refresh or not cache_exists

        if needs_remote_fetch:
            if not ctx.json_output:
                click.echo(f"Fetching log for job {job_id} via notebook exec...")

            try:
                _fetch_and_cache_log_via_notebook(
                    ctx,
                    notebook_id=nb_id,
                    remote_log_path=str(remote_log_path_str),
                    cache_path=cache_path,
                )
                _update_log_offset_to_filesize(cache, job_id=job_id, cache_path=cache_path)
            except IOError as e:
                _handle_error(ctx, "RemoteLogError", str(e), EXIT_GENERAL_ERROR)
            except TimeoutError as e:
                _handle_error(ctx, "Timeout", str(e), EXIT_GENERAL_ERROR)

        if not cache_path.exists():
            _handle_error(
                ctx,
                "LogNotFound",
                f"Failed to retrieve log for job {job_id}.",
                EXIT_LOG_NOT_FOUND,
            )
            return

        if tail:
            try:
                _echo_file_tail(ctx, cache_path=cache_path, tail=tail)
            except OSError as e:
                _handle_error(ctx, "LogNotFound", str(e), EXIT_LOG_NOT_FOUND)
            return

        if head:
            try:
                _echo_file_head(ctx, cache_path=cache_path, head=head)
            except OSError as e:
                _handle_error(ctx, "LogNotFound", str(e), EXIT_LOG_NOT_FOUND)
            return

        try:
            _echo_file_content(ctx, cache_path=cache_path)
        except OSError as e:
            _handle_error(ctx, "LogNotFound", str(e), EXIT_LOG_NOT_FOUND)

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "Error", str(e), EXIT_GENERAL_ERROR)


@click.command("logs")
@click.argument("job_id", required=False)
@click.option("--tail", "-n", type=int, help="Show last N lines only")
@click.option("--head", type=int, help="Show first N lines only")
@click.option("--path", is_flag=True, help="Just print log path, don't read content")
@click.option(
    "--refresh",
    is_flag=True,
    help="Re-fetch log from the beginning (ignore cached offset)",
)
@click.option("--follow", "-f", is_flag=True, help="Continuously poll for new log content")
@click.option(
    "--interval",
    type=int,
    default=30,
    help="Poll interval for --follow in seconds (default: 30)",
)
@click.option(
    "--status",
    "-s",
    multiple=True,
    help="Status filter for bulk mode (e.g., RUNNING). Repeatable.",
)
@click.option(
    "--limit",
    "-m",
    type=int,
    default=0,
    help="Max cached jobs to process in bulk mode (0 = all).",
)
@click.option(
    "--notebook",
    help="Notebook ID to use for fetching logs via notebook exec",
)
@pass_context
def logs(
    ctx: Context,
    job_id: Optional[str],
    tail: int | None,
    head: int | None,
    path: bool,
    refresh: bool,
    follow: bool,
    interval: int,
    status: tuple,
    limit: int,
    notebook: Optional[str],
) -> None:
    """View logs for a training job.

    Fetches logs by executing a read command on a running notebook
    via `inspire notebook exec` and caches them locally.

    \b
    Single job mode (with JOB_ID):
        Fetches and displays the log for a specific job.

    Bulk mode (without JOB_ID):
        Fetches and caches logs for multiple jobs from local cache.
        Use --status to filter by job status.

    \b
    Examples:
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --notebook my-notebook
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --tail 100
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --head 50
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --follow
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --follow --interval 10
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --path
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --refresh
        inspire job logs --status RUNNING --status SUCCEEDED
        inspire job logs --refresh --status RUNNING
    """
    if not job_id:
        if tail or head or path or follow or notebook:
            _handle_error(
                ctx,
                "InvalidUsage",
                "--tail, --head, --path, --follow and --notebook require a JOB_ID",
                EXIT_VALIDATION_ERROR,
            )
            return
        _bulk_update_logs(ctx, status=status, limit=limit, refresh=refresh, notebook=notebook)
        return

    job_id = resolve_job_id(ctx, job_id)

    _run_job_logs_single_job(
        ctx,
        job_id=job_id,
        tail=tail,
        head=head,
        path=path,
        refresh=refresh,
        follow=follow,
        interval=interval,
        notebook=notebook,
    )


__all__ = ["logs"]
