"""Shared helpers for submitting jobs via the Inspire OpenAPI client."""

from __future__ import annotations

import concurrent.futures
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web import session as web_session_module
from inspire.platform.web.browser_api import ProjectInfo
from inspire.platform.web.session.models import DEFAULT_WORKSPACE_ID
from inspire.config import Config, ConfigError, build_env_exports
from inspire.cli.utils.job_cache import JobCache

# Limit parallel workspace queries during job submission.
_JOB_PROJECT_MAX_WORKERS = 8


@dataclass(frozen=True)
class JobSubmission:
    job_id: Optional[str]
    data: dict
    result: Any
    log_path: Optional[str]
    wrapped_command: str
    max_time_ms: str


def wrap_in_bash(command: str) -> str:
    """Wrap a command in bash -c unless already wrapped."""
    stripped = command.strip()

    if stripped.startswith(("bash -c ", "sh -c ", "/bin/bash -c ", "/bin/sh -c ")):
        return command

    escaped = command.replace("'", "'\\''")
    return f"bash -c '{escaped}'"


def build_remote_logged_command(
    config: Config,
    *,
    command: str,
    log_path_override: str | None = None,
) -> tuple[str, str | None]:
    """Build the remote command (with optional logging) and return (final_command, log_path).

    If *log_path_override* is provided, it will be used as the log file path.
    """
    env_exports = build_env_exports(config.remote_env)
    final_command = f"{env_exports}{command}" if env_exports else command

    log_path = None
    if config.target_dir:
        if log_path_override:
            log_path = log_path_override
            log_dir = os.path.dirname(log_path)
        else:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            log_dir = os.path.join(config.target_dir, ".inspire")
            log_filename = f"training_master_{timestamp}.log"
            log_path = os.path.join(log_dir, log_filename)

        final_command = (
            f'{env_exports}mkdir -p "{log_dir}" && ( cd "{config.target_dir}" && {command} ) '
            f'> "{log_path}" 2>&1'
        )

    return final_command, log_path


def select_project_for_workspace(
    config: Config,
    *,
    workspace_id: str,
    requested: str | None,
) -> tuple[ProjectInfo, str | None]:
    """Select a project for the given workspace, with quota-aware fallback."""
    try:
        session = web_session_module.get_web_session()
    except ValueError as e:
        raise ConfigError(str(e)) from e

    projects = browser_api_module.list_projects(workspace_id=workspace_id, session=session)
    if not projects:
        raise ConfigError("No projects available")

    congested = browser_api_module.check_scheduling_health(
        workspace_id=workspace_id,
        project_ids={p.project_id for p in projects},
        session=session,
    )

    requested_value = requested
    if not requested_value and not config.project_order:
        requested_value = config.job_project_id
    if requested_value and not requested_value.startswith("project-"):
        alias_map = config.projects or {}
        for alias, project_id in alias_map.items():
            if alias.lower() == requested_value.lower():
                requested_value = project_id
                break

    shared_groups = getattr(config, "project_shared_path_groups", None)
    if not isinstance(shared_groups, dict) or not shared_groups:
        shared_groups = None

    return browser_api_module.select_project(
        projects,
        requested_value,
        shared_path_group_by_id=shared_groups,
        project_order=config.project_order or None,
        congested_projects=congested or None,
    )


def _resolve_workspace_ids(config: Config, session: web_session_module.WebSession) -> list[str]:
    """Collect all accessible workspace IDs for project queries.

    Sources (merged, deduplicated):
      - session.all_workspace_ids (discovered at login)
      - config workspace fields (cpu, gpu, internet, arbitrary aliases)
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(ws_id: str | None) -> None:
        val = str(ws_id or "").strip()
        if val and val != DEFAULT_WORKSPACE_ID and val not in seen:
            seen.add(val)
            result.append(val)

    # Prefer session-discovered workspaces first.
    for ws_id in getattr(session, "all_workspace_ids", None) or []:
        _add(ws_id)

    # Supplement with config workspaces (may include manually-added ones).
    _add(getattr(session, "workspace_id", None))
    _add(config.workspace_cpu_id)
    _add(config.workspace_gpu_id)
    _add(config.workspace_internet_id)
    _add(config.job_workspace_id)
    for ws_id in (config.workspaces or {}).values():
        _add(ws_id)

    return result


def _collect_all_workspace_projects(
    workspace_ids: list[str],
    session: web_session_module.WebSession,
) -> list[ProjectInfo]:
    """Query projects across all workspaces, deduplicating by project_id.

    The first workspace is queried serially; remaining are fetched in parallel.
    Errors for individual workspaces are silently skipped so that a single
    bad workspace does not block job submission.
    """
    if not workspace_ids:
        return []

    projects: list[ProjectInfo] = []
    seen: set[str] = set()

    def _merge(items: list[ProjectInfo]) -> None:
        for p in items:
            if p.project_id not in seen:
                seen.add(p.project_id)
                projects.append(p)

    # First workspace serial.
    try:
        first = browser_api_module.list_projects(
            workspace_id=workspace_ids[0], session=session
        )
        _merge(first)
    except Exception:
        pass

    remaining = workspace_ids[1:]
    if not remaining:
        return projects

    # Remaining workspaces in parallel.
    max_workers = min(len(remaining), _JOB_PROJECT_MAX_WORKERS)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                browser_api_module.list_projects, workspace_id=ws_id, session=session
            ): ws_id
            for ws_id in remaining
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                _merge(future.result())
            except Exception:
                pass

    return projects


def select_project_for_job(
    config: Config,
    *,
    primary_workspace_id: str,
    requested: str | None,
) -> tuple[ProjectInfo, str | None]:
    """Select a project for a job, searching across all accessible workspaces.

    Unlike :func:`select_project_for_workspace` which queries a single workspace,
    this function fans out to all workspaces the user can access, merges the
    project lists, and selects from the combined pool.  This allows projects in
    other workspaces (e.g. a public project space) to participate in automatic
    project selection.

    Returns:
        ``(selected_project, fallback_msg)`` — the project carries its own
        ``workspace_id`` which must be used when submitting the job so that
        billing/quota goes to the correct project.
    """
    try:
        session = web_session_module.get_web_session()
    except ValueError as e:
        raise ConfigError(str(e)) from e

    # Determine all workspace IDs to query.
    workspace_ids = _resolve_workspace_ids(config, session)
    # Ensure the primary workspace is first (already queried serially).
    if primary_workspace_id in workspace_ids:
        workspace_ids.remove(primary_workspace_id)
    workspace_ids.insert(0, primary_workspace_id)

    # Collect projects from all workspaces.
    projects = _collect_all_workspace_projects(workspace_ids, session)
    if not projects:
        raise ConfigError("No projects available across any workspace")

    # Check scheduling health for each workspace that has projects.
    congested: set[str] = set()
    ws_to_projects: dict[str, set[str]] = {}
    for p in projects:
        ws_to_projects.setdefault(p.workspace_id, set()).add(p.project_id)
    for ws_id, pids in ws_to_projects.items():
        try:
            ws_congested = browser_api_module.check_scheduling_health(
                workspace_id=ws_id, project_ids=pids, session=session
            )
            congested.update(ws_congested)
        except Exception:
            pass

    # Resolve requested project name/alias.
    requested_value = requested
    if not requested_value and not config.project_order:
        requested_value = config.job_project_id
    if requested_value and not requested_value.startswith("project-"):
        alias_map = config.projects or {}
        for alias, project_id in alias_map.items():
            if alias.lower() == requested_value.lower():
                requested_value = project_id
                break

    shared_groups = getattr(config, "project_shared_path_groups", None)
    if not isinstance(shared_groups, dict) or not shared_groups:
        shared_groups = None

    return browser_api_module.select_project(
        projects,
        requested_value,
        shared_path_group_by_id=shared_groups,
        project_order=config.project_order or None,
        congested_projects=congested or None,
    )


def cache_created_job(
    config: Config,
    *,
    job_id: str,
    name: str,
    resource: str,
    command: str,
    log_path: str | None,
    project: str | None = None,
) -> None:
    cache = JobCache(config.get_expanded_cache_path())
    cache.add_job(
        job_id=job_id,
        name=name,
        resource=resource,
        command=command,
        status="PENDING",
        log_path=log_path,
        project=project,
    )


def submit_training_job(
    api,  # noqa: ANN001
    *,
    config: Config,
    name: str,
    command: str,
    resource: str,
    framework: str,
    location: Optional[str],
    project_id: str,
    workspace_id: str,
    image: Optional[str],
    image_type: Optional[str] = None,
    priority: int,
    nodes: int,
    max_time_hours: float,
    project_name: Optional[str] = None,
    log_file: str | None = None,
) -> JobSubmission:
    wrapped_command = wrap_in_bash(command)
    final_command, log_path = build_remote_logged_command(
        config,
        command=wrapped_command,
        log_path_override=log_file,
    )

    max_time_ms = str(int(max_time_hours * 3600 * 1000))

    create_kwargs = dict(
        name=name,
        command=final_command,
        resource=resource,
        framework=framework,
        prefer_location=location,
        project_id=project_id,
        workspace_id=workspace_id,
        image=image,
        image_type=image_type,
        task_priority=priority,
        instance_count=nodes,
        max_running_time_ms=max_time_ms,
    )

    if config.shm_size is not None:
        shm_size = int(config.shm_size)
        if shm_size < 1:
            raise ValueError(
                "Shared memory size must be >= 1 (set INSPIRE_SHM_SIZE or job.shm_size)."
            )
        create_kwargs["shm_gi"] = shm_size

    result = api.create_training_job_smart(**create_kwargs)
    data = result.get("data", {}) if isinstance(result, dict) else {}
    job_id = data.get("job_id")

    if job_id:
        cache_created_job(
            config,
            job_id=job_id,
            name=name,
            resource=resource,
            command=wrapped_command,
            log_path=log_path,
            project=project_name,
        )

    return JobSubmission(
        job_id=job_id,
        data=data,
        result=result,
        log_path=log_path,
        wrapped_command=wrapped_command,
        max_time_ms=max_time_ms,
    )


__all__ = [
    "JobSubmission",
    "build_remote_logged_command",
    "cache_created_job",
    "select_project_for_job",
    "select_project_for_workspace",
    "submit_training_job",
    "wrap_in_bash",
]
