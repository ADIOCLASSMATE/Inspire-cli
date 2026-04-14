"""GPU availability overview for a given resource request.

Shows the current state of all matching compute groups and project budgets,
letting the user decide the best course of action. This is a **read-only
information tool** — it never creates jobs or instances, and it does NOT
recommend a specific strategy.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass

from .api import get_accurate_gpu_availability
from inspire.platform.web.browser_api.projects import (
    ProjectInfo,
    list_projects,
)
from inspire.platform.web.session import DEFAULT_WORKSPACE_ID, get_web_session

# Limit parallel workspace queries during allocate.
_ALLOCATE_MAX_WORKERS = 8


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class GroupStatus:
    """Availability status of a single compute group."""

    group_id: str
    group_name: str
    gpu_type: str
    available_gpus: int
    low_priority_gpus: int
    total_gpus: int
    gpus_needed: int

    @property
    def has_free(self) -> bool:
        return self.available_gpus >= self.gpus_needed

    @property
    def has_preemptible(self) -> bool:
        """Aggregate check: available + low_pri >= needed. May be spread across nodes."""
        return max(self.available_gpus, 0) + self.low_priority_gpus >= self.gpus_needed


@dataclass
class ProjectBudget:
    """Budget status of a single project."""

    project_id: str
    project_name: str
    priority: int
    remain_budget: float | None
    member_remain_budget: float | None
    has_budget: bool


@dataclass
class AllocateResult:
    """Result of the availability overview computation."""

    gpus: int
    gpu_type: str
    groups: list[GroupStatus]
    projects: list[ProjectBudget]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _priority_value(project: ProjectInfo) -> int:
    """Parse project priority_name as int, defaulting to 0."""
    try:
        return int(project.priority_name)
    except (ValueError, TypeError):
        return 0


def _has_budget(project: ProjectInfo) -> bool:
    """Check that both project-level and member-level GPU budget (卡时) remain."""
    if project.remain_budget is not None and project.remain_budget <= 0:
        return False
    if project.member_remain_budget is not None and project.member_remain_budget <= 0:
        return False
    return project.has_quota(needs_gpu=True)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _list_projects_all_workspaces() -> list[ProjectInfo]:
    """List projects across all accessible workspaces.

    Falls back to the default (single-workspace) query if session
    discovery fails or no workspace IDs are available.
    """
    try:
        session = get_web_session()
    except Exception:
        return list_projects()

    workspace_ids: list[str] = []
    seen: set[str] = set()

    def _add(ws_id: str | None) -> None:
        val = str(ws_id or "").strip()
        if val and val != DEFAULT_WORKSPACE_ID and val not in seen:
            seen.add(val)
            workspace_ids.append(val)

    for ws_id in session.all_workspace_ids or []:
        _add(ws_id)
    _add(session.workspace_id)

    if not workspace_ids:
        return list_projects(session=session)

    # Query first workspace serially, remaining in parallel.
    projects: list[ProjectInfo] = []
    project_seen: set[str] = set()

    def _merge(items: list[ProjectInfo]) -> None:
        for p in items:
            if p.project_id not in project_seen:
                project_seen.add(p.project_id)
                projects.append(p)

    try:
        _merge(list_projects(workspace_id=workspace_ids[0], session=session))
    except Exception:
        pass

    remaining = workspace_ids[1:]
    if remaining:
        max_workers = min(len(remaining), _ALLOCATE_MAX_WORKERS)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    list_projects, workspace_id=ws_id, session=session
                ): ws_id
                for ws_id in remaining
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    _merge(future.result())
                except Exception:
                    pass

    return projects or list_projects(session=session)


def compute_allocate_overview(
    *,
    gpus: int = 8,
    gpu_type: str = "H200",
) -> AllocateResult:
    """Compute an availability overview for a GPU request.

    This is a read-only information tool. It shows the current state
    of compute groups and project budgets without recommending a strategy.

    Args:
        gpus: Number of GPUs needed (default 8).
        gpu_type: GPU type filter (default "H200").

    Returns:
        AllocateResult with group statuses and project budgets.
    """
    # 1. Fetch availability
    availability = get_accurate_gpu_availability()

    # Filter by GPU type
    gpu_type_upper = gpu_type.upper()
    filtered = [
        a for a in availability
        if gpu_type_upper in (a.gpu_type or "").upper()
    ]

    if not filtered:
        raise ValueError(f"No compute groups found for GPU type '{gpu_type}'")

    # Sort: free groups first, then by available_gpus descending
    filtered.sort(key=lambda a: (a.available_gpus >= gpus, a.available_gpus), reverse=True)

    groups = [
        GroupStatus(
            group_id=a.group_id,
            group_name=a.group_name,
            gpu_type=a.gpu_type,
            available_gpus=a.available_gpus,
            low_priority_gpus=a.low_priority_gpus,
            total_gpus=a.total_gpus,
            gpus_needed=gpus,
        )
        for a in filtered
    ]

    # 2. Fetch projects across all accessible workspaces
    projects = _list_projects_all_workspaces()

    # Sort: projects with budget first, then by priority descending
    project_budgets = [
        ProjectBudget(
            project_id=p.project_id,
            project_name=p.name,
            priority=_priority_value(p),
            remain_budget=p.remain_budget,
            member_remain_budget=p.member_remain_budget,
            has_budget=_has_budget(p),
        )
        for p in projects
    ]
    project_budgets.sort(key=lambda p: (p.has_budget, p.priority), reverse=True)

    return AllocateResult(
        gpus=gpus,
        gpu_type=gpu_type,
        groups=groups,
        projects=project_budgets,
    )


__all__ = ["AllocateResult", "GroupStatus", "ProjectBudget", "compute_allocate_overview"]
