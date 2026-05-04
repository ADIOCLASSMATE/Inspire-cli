"""Job logs command.

Implements `inspire job logs` including:
- Single-job mode (with JOB_ID)
- Bulk mode (without JOB_ID)

Logs are fetched primarily via the /api/v1/logs/train endpoint (fast,
direct API). Falls back to notebook exec if the API is unavailable.
"""

from __future__ import annotations

import logging
import os
import sys
import time
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

logger = logging.getLogger(__name__)

# Polling constants for --follow via direct API.
_POLL_INTERVAL_SEC = 3
_POLL_MAX_INTERVAL_SEC = 15
_STATUS_CHECK_EVERY_N_POLLS = 5

TERMINAL_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "job_succeeded",
    "job_failed",
    "job_cancelled",
}


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


# ---------------------------------------------------------------------------
# Direct API log fetching (primary method)
# ---------------------------------------------------------------------------


def _fetch_log_via_api(
    job_id: str,
    *,
    session: object | None = None,
) -> str | None:
    """Fetch full log text via /api/v1/logs/train (direct API).

    Paginates through all log entries to avoid truncation.
    Returns the concatenated log content as a string, or None on failure.
    """
    from inspire.platform.web.browser_api.jobs import (
        fetch_job_logs,
        list_job_instances,
    )

    try:
        instances = list_job_instances(job_id, session=session)
    except Exception as e:
        logger.debug("list_job_instances failed: %s", e)
        return None

    if not instances:
        logger.debug("No instances found for job %s", job_id)
        return None

    pod_names = [inst.name for inst in instances if inst.name]
    if not pod_names:
        return None

    # Paginate: fetch in batches, advancing the time window.
    all_entries: list[dict] = []
    start_ts: str | None = None
    max_pages = 50  # safety limit

    for _ in range(max_pages):
        try:
            entries = fetch_job_logs(
                pod_names=pod_names,
                start_timestamp_ms=start_ts,
                page_size=500,
                session=session,
            )
        except Exception as e:
            logger.debug("fetch_job_logs failed: %s", e)
            break

        if not entries:
            break

        all_entries.extend(entries)

        # If we got fewer than page_size, we've reached the end.
        if len(entries) < 500:
            break

        # Advance window: use the minimum timestamp from this batch
        # (entries are desc by time, so the last entry has the oldest timestamp).
        oldest_ts = entries[-1].get("time")
        if oldest_ts is not None:
            start_ts = str(oldest_ts)
        else:
            break

    if not all_entries:
        return None

    # Entries come sorted desc by time. Reverse for chronological order.
    # Each entry may have "content", "log", or "message" field.
    lines = []
    for entry in reversed(all_entries):
        text = entry.get("content") or entry.get("log") or entry.get("message") or ""
        if text:
            lines.append(text)

    return "\n".join(lines) if lines else None


def _fetch_log_entries_via_api(
    job_id: str,
    *,
    pod_names: list[str] | None = None,
    start_timestamp_ms: str | None = None,
    session: object | None = None,
) -> tuple[list[dict], list[str]]:
    """Fetch log entries via API, returning (entries, pod_names).

    If pod_names is None, discovers them via list_job_instances first.
    Returns ([], []) on failure so callers can fall back.
    """
    from inspire.platform.web.browser_api.jobs import (
        fetch_job_logs,
        list_job_instances,
    )

    if pod_names is None:
        try:
            instances = list_job_instances(job_id, session=session)
            pod_names = [inst.name for inst in instances if inst.name]
        except Exception as e:
            logger.debug("list_job_instances failed: %s", e)
            return [], []

    if not pod_names:
        return [], []

    try:
        entries = fetch_job_logs(
            pod_names=pod_names,
            start_timestamp_ms=start_timestamp_ms,
            page_size=200,
            session=session,
        )
    except Exception as e:
        logger.debug("fetch_job_logs failed: %s", e)
        return [], pod_names

    return entries, pod_names


def _log_entry_text(entry: dict) -> str:
    """Extract display text from a log entry."""
    return entry.get("content") or entry.get("log") or entry.get("message") or ""


def _log_entry_key(entry: dict) -> tuple:
    """Build a deduplication key for a log entry."""
    log_id = entry.get("log-id.keyword")
    if log_id is None:
        # Fallback: hash the content to produce a stable key when log-id is absent.
        content = entry.get("content") or entry.get("log") or entry.get("message") or ""
        log_id = hash(content)
    return (entry.get("time", ""), log_id)


# ---------------------------------------------------------------------------
# Notebook exec log fetching (fallback method)
# ---------------------------------------------------------------------------


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


def _get_explicit_notebook_id(
    ctx: Context,
    *,
    config: Config,
    job_id: str,
    notebook: Optional[str] = None,
) -> Optional[str]:
    """Return the explicitly provided notebook ID, or None.

    (Auto-discovery was removed when the old OpenAPI ``AuthManager`` path
    was deleted; it relied on ``api.list_notebooks()`` which never existed
    on ``InspireAPI``.)
    """
    return notebook


# ---------------------------------------------------------------------------
# Follow logs via direct API (primary) or notebook exec (fallback)
# ---------------------------------------------------------------------------


def _follow_logs_via_api(
    ctx: Context,
    *,
    job_id: str,
    config: Config,
    cache_path: Path,
    tail_lines: int = 50,
    interval: int = _POLL_INTERVAL_SEC,
) -> int:
    """Follow logs by polling the /api/v1/logs/train endpoint."""
    from inspire.platform.web.browser_api.jobs import (
        get_job_detail,
        list_job_instances,
    )

    # Get initial instances
    try:
        instances = list_job_instances(job_id)
        pod_names = [inst.name for inst in instances if inst.name]
    except Exception as e:
        if not ctx.json_output:
            click.echo(f"Warning: cannot list instances for --follow: {e}", err=True)
        return EXIT_GENERAL_ERROR

    if not pod_names:
        if not ctx.json_output:
            click.echo("No instances found for job.", err=True)
        return EXIT_JOB_NOT_FOUND

    if not ctx.json_output:
        click.echo(f"Following log for job {job_id} via API (interval: {interval}s)")
        click.echo("Press Ctrl+C to stop\n")

    # Show initial tail
    try:
        initial_entries, pod_names = _fetch_log_entries_via_api(
            job_id, pod_names=pod_names,
        )
    except Exception:
        initial_entries = []

    seen: set[tuple] = set()
    last_timestamp_ms: str | None = None
    backoff = float(interval)
    poll_count = 0

    # Process and display initial entries (chronological order)
    if initial_entries:
        for entry in reversed(initial_entries):
            key = _log_entry_key(entry)
            seen.add(key)
            ts = entry.get("time")
            if ts is not None:
                ts_str = str(ts)
                if last_timestamp_ms is None or ts_str > last_timestamp_ms:
                    last_timestamp_ms = ts_str

        text_lines = [_log_entry_text(e) for e in reversed(initial_entries)]
        text_lines = [t for t in text_lines if t]
        if text_lines:
            display = "\n".join(text_lines[-tail_lines:])
            if not ctx.json_output:
                click.echo(display)
            else:
                click.echo(json_formatter.format_json({
                    "event": "initial_content",
                    "job_id": job_id,
                    "size_bytes": len(display),
                    "content": display,
                }))

    # Polling loop
    final_status = None
    try:
        while True:
            time.sleep(backoff)
            poll_count += 1

            # Fetch new entries
            try:
                entries, pod_names = _fetch_log_entries_via_api(
                    job_id,
                    pod_names=pod_names,
                    start_timestamp_ms=last_timestamp_ms,
                )
            except Exception as e:
                if not ctx.json_output:
                    click.echo(f"\nWarning: fetch failed: {e}", err=True)
                backoff = min(backoff * 1.5, _POLL_MAX_INTERVAL_SEC)
                continue

            # Deduplicate
            new_entries: list[dict] = []
            for entry in reversed(entries):  # API returns desc, reverse for chrono
                key = _log_entry_key(entry)
                if key not in seen:
                    seen.add(key)
                    new_entries.append(entry)
                    ts = entry.get("time")
                    if ts is not None:
                        ts_str = str(ts)
                        if last_timestamp_ms is None or ts_str > last_timestamp_ms:
                            last_timestamp_ms = ts_str

            if new_entries:
                text_lines = [_log_entry_text(e) for e in new_entries]
                text_lines = [t for t in text_lines if t]
                if text_lines:
                    output = "\n".join(text_lines)
                    if not ctx.json_output:
                        click.echo(output, nl=False)
                        if not output.endswith("\n"):
                            click.echo()
                    else:
                        click.echo(json_formatter.format_json({
                            "event": "new_content",
                            "job_id": job_id,
                            "content": output,
                        }))
                backoff = float(interval)  # reset on new data
            else:
                backoff = min(backoff * 1.5, _POLL_MAX_INTERVAL_SEC)

            # Periodic job status check
            if poll_count % _STATUS_CHECK_EVERY_N_POLLS == 0:
                try:
                    result = get_job_detail(job_id)
                    job_data = result.get("data", {})
                    current_status = job_data.get("status", "UNKNOWN")
                    if current_status in TERMINAL_STATUSES:
                        final_status = current_status
                        # Final drain: fetch any remaining logs before breaking
                        try:
                            drain_entries, _ = _fetch_log_entries_via_api(
                                job_id,
                                pod_names=pod_names,
                                start_timestamp_ms=last_timestamp_ms,
                            )
                            new_drain: list[dict] = []
                            for entry in reversed(drain_entries):
                                key = _log_entry_key(entry)
                                if key not in seen:
                                    seen.add(key)
                                    new_drain.append(entry)
                            if new_drain:
                                text_lines = [_log_entry_text(e) for e in new_drain]
                                text_lines = [t for t in text_lines if t]
                                if text_lines:
                                    output = "\n".join(text_lines)
                                    if not ctx.json_output:
                                        click.echo(output, nl=False)
                                        if not output.endswith("\n"):
                                            click.echo()
                        except Exception:
                            pass
                        break
                except Exception:
                    logger.debug("Status check failed for job %s", job_id, exc_info=True)

        if final_status and not ctx.json_output:
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
                from inspire.platform.web.browser_api.jobs import get_job_detail as _bgjd
                result = _bgjd(job_id)
                job_data = result.get("data", {})
                current_status = job_data.get("status", "UNKNOWN")

                if current_status in TERMINAL_STATUSES:
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


# ---------------------------------------------------------------------------
# Bulk mode
# ---------------------------------------------------------------------------


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

            cache_path = cache_dir / f"{job_id_item}.log"

            # Try direct API first
            try:
                log_content = _fetch_log_via_api(job_id_item)
                if log_content is not None:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(log_content, encoding="utf-8")
                    updated.append({"job_id": job_id_item, "log_path": str(cache_path), "source": "api"})
                    continue
            except Exception:
                pass

            # Fall back to notebook exec
            if not remote_log_path_str:
                skipped_no_log.append(job_id_item)
                continue

            try:
                nb_id = _get_explicit_notebook_id(
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
                updated.append({"job_id": job_id_item, "log_path": str(cache_path), "source": "notebook"})
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
                source = entry.get("source", "unknown")
                click.echo(f"- {entry['job_id']}: {entry['log_path']} ({source})")

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


# ---------------------------------------------------------------------------
# Single-job mode
# ---------------------------------------------------------------------------


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
    v2: bool = True,
    start_time: str | None = None,
    end_time: str | None = None,
) -> None:
    try:
        config, _ = Config.from_files_and_env(require_credentials=False, require_target_dir=False)
        cache = job_deps.JobCache(config.get_expanded_cache_path())

        cached = cache.get_job(job_id)
        if not cached:
            _handle_error(ctx, "JobNotFound", f"Job not found: {job_id}", EXIT_JOB_NOT_FOUND)
            return

        remote_log_path_str = cached.get("log_path")

        if path:
            if remote_log_path_str:
                _echo_log_path(ctx, job_id=job_id, remote_log_path=str(remote_log_path_str))
            else:
                _echo_log_path(ctx, job_id=job_id, remote_log_path=f"(via API: {job_id})")
            sys.exit(EXIT_SUCCESS)

        cache_paths = _build_log_cache_paths(config, job_id)
        cache_path = _migrate_legacy_log_filename(cache_paths)
        cache_exists = cache_path.exists()

        # --follow mode: prefer API, fall back to notebook
        if follow:
            try:
                follow_exit_code = _follow_logs_via_api(
                    ctx=ctx,
                    job_id=job_id,
                    config=config,
                    cache_path=cache_path,
                    tail_lines=tail or 50,
                    interval=interval or _POLL_INTERVAL_SEC,
                )
                sys.exit(follow_exit_code)
                return  # unreachable, for type checkers
            except Exception as e:
                logger.debug("API follow failed, falling back to notebook: %s", e)
                # Fall through to notebook fallback

            nb_id = _get_explicit_notebook_id(ctx, config=config, job_id=job_id, notebook=notebook)
            if nb_id:
                follow_exit_code = _follow_logs_via_notebook(
                    ctx=ctx,
                    notebook_id=nb_id,
                    job_id=job_id,
                    config=config,
                    remote_log_path=str(remote_log_path_str or ""),
                    cache_path=cache_path,
                    tail_lines=tail or 50,
                    interval=interval or 30,
                )
                sys.exit(follow_exit_code)
            else:
                _handle_error(
                    ctx,
                    "NoNotebook",
                    (
                        "Cannot follow logs: API unavailable and no notebook found.\n\n"
                        "Try: inspire job logs <job_id> --follow --notebook <notebook_id>"
                    ),
                    EXIT_GENERAL_ERROR,
                )
                return

        # Non-follow mode: fetch log content
        needs_remote_fetch = refresh or not cache_exists

        if needs_remote_fetch:
            # Try v2 API first if enabled
            v2_ok = False
            if v2:
                try:
                    from inspire.platform.web.v2_api.train import get_job_logs as v2_get_logs
                    from inspire.platform.web.session import get_v2_token

                    v2_token = get_v2_token(config)
                    if v2_token:
                        v2_logs, _ = v2_get_logs(
                            v2_token, config.base_url, job_id,
                            instance_count=1,
                            page_size=tail or 500,
                            start_timestamp_ms=start_time or None,
                            end_timestamp_ms=end_time or None,
                        )
                        if v2_logs:
                            lines = []
                            for entry in reversed(v2_logs):
                                text = (
                                    entry.get("content")
                                    or entry.get("log")
                                    or entry.get("message")
                                    or ""
                                )
                                if text:
                                    lines.append(text)
                            log_content = "\n".join(lines)
                            if log_content:
                                cache_path.parent.mkdir(parents=True, exist_ok=True)
                                cache_path.write_text(log_content, encoding="utf-8")
                                _update_log_offset_to_filesize(cache, job_id=job_id, cache_path=cache_path)
                                v2_ok = True
                                if not ctx.json_output:
                                    click.echo(f"Fetched log for job {job_id} via v2 API")
                except Exception:
                    logger.debug("v2 log fetch failed, falling back to v1", exc_info=True)

            # Try direct v1 API if v2 didn't work
            api_ok = v2_ok
            if not v2_ok:
                try:
                    log_content = _fetch_log_via_api(job_id)
                    if log_content is not None:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(log_content, encoding="utf-8")
                        _update_log_offset_to_filesize(cache, job_id=job_id, cache_path=cache_path)
                        api_ok = True
                        if not ctx.json_output:
                            click.echo(f"Fetched log for job {job_id} via API")
                except Exception as e:
                    logger.debug("API log fetch failed, will try notebook: %s", e)

            # Fall back to notebook exec if API didn't work
            if not api_ok:
                nb_id = _get_explicit_notebook_id(
                    ctx, config=config, job_id=job_id, notebook=notebook,
                )
                if nb_id and remote_log_path_str:
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
                elif not nb_id and not cache_exists:
                    # If there's no log_path at all, it's LogNotFound;
                    # otherwise it's a connectivity issue.
                    if not remote_log_path_str:
                        _handle_error(
                            ctx,
                            "LogNotFound",
                            f"No log file found for job {job_id}",
                            EXIT_LOG_NOT_FOUND,
                        )
                    else:
                        _handle_error(
                            ctx,
                            "NoNotebook",
                            (
                                "Cannot fetch logs: API unavailable and no notebook found.\n\n"
                                "To view job logs, do one of the following:\n"
                                "  1. Start a notebook and use: "
                                f"inspire job logs <job_id> --notebook <notebook_id>\n"
                                "  2. Run directly: "
                                f"inspire notebook exec <notebook> \"cat {remote_log_path_str}\""
                            ),
                            EXIT_GENERAL_ERROR,
                        )
                    return

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


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


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
    default=0,
    help="Poll interval for --follow in seconds (default: 3 for API, 30 for notebook)",
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
    help="Notebook ID to use for fetching logs via notebook exec (fallback)",
)
@click.option(
    "--v2/--no-v2",
    is_flag=True,
    default=True,
    help="Use v2 GetJobLog API for log fetching (default: True, falls back to v1)",
)
@click.option(
    "--start-time",
    help="Start time filter for v2 logs (ISO format or unix ms)",
)
@click.option(
    "--end-time",
    help="End time filter for v2 logs (ISO format or unix ms)",
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
    v2: bool,
    start_time: str | None,
    end_time: str | None,
) -> None:
    """View logs for a training job.

    Fetches logs via the direct /api/v1/logs/train endpoint (fast, no
    notebook required). Falls back to notebook exec if the API is
    unavailable.

    With --v2 (default), uses the v2 GetJobLog API which supports
    time-range filtering via --start-time and --end-time.

    \b
    Single job mode (with JOB_ID):
        Fetches and displays the log for a specific job.

    Bulk mode (without JOB_ID):
        Fetches and caches logs for multiple jobs from local cache.
        Use --status to filter by job status.

    \b
    Examples:
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --tail 100
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --follow
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --v2 --start-time 1700000000000
        inspire job logs job-c4eb3ac3-6d83-405c-aa29-059bc945c4bf --path
        inspire job logs --status RUNNING --status SUCCEEDED
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
        v2=v2,
        start_time=start_time,
        end_time=end_time,
    )


__all__ = ["logs"]
