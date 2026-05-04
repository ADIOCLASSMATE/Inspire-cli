"""GPU availability overview for a given resource request.

Shows the current state of all matching compute groups and project budgets,
letting the user decide the best course of action. This is a **read-only
information tool** — it never creates jobs or instances, and it does NOT
recommend a specific strategy.

When low-priority GPUs are detected, enriches the result with per-node
preemptibility analysis via cluster_metric endpoints.
"""

from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass, field

from .api import get_accurate_gpu_availability
from .metrics import TaskDimension, list_task_dimension, list_node_dimension
from .models import GPUAvailability
from inspire.platform.web.browser_api.projects import (
    ProjectInfo,
    list_projects,
)
from inspire.platform.web.session import DEFAULT_WORKSPACE_ID, get_web_session

logger = logging.getLogger(__name__)

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
    workspace_id: str = ""
    workspace_name: str = ""

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
    workspace_id: str = ""
    workspace_name: str = ""


@dataclass
class AllocateResult:
    """Result of the availability overview computation."""

    gpus: int
    gpu_type: str
    groups: list[GroupStatus]
    projects: list[ProjectBudget]
    preemptibility: dict[str, GroupPreemptibility] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Preemptibility models
# ---------------------------------------------------------------------------

# Priority threshold: tasks with priority <= this are considered preemptible.
LOW_PRIORITY_THRESHOLD = 3


@dataclass(frozen=True)
class PreemptibleTask:
    """A low-priority task found on a node."""

    id: str
    name: str
    priority: int
    user: str
    gpu_used: int


@dataclass(frozen=True)
class PreemptibleNode:
    """A node containing low-priority tasks."""

    node_name: str
    gpu_total: int
    gpu_preemptible: int  # GPUs held by priority <= LOW_PRIORITY_THRESHOLD tasks
    gpu_other: int  # GPUs held by priority > LOW_PRIORITY_THRESHOLD tasks
    is_fully_preemptible: bool  # all tasks on node are low-priority AND node is schedulable
    is_schedulable: bool  # True when status == "Ready"
    status: str  # node status from API, e.g. "Ready" or "SchedulingDisabled"
    low_priority_tasks: tuple[PreemptibleTask, ...]


@dataclass(frozen=True)
class GroupPreemptibility:
    """Preemptibility analysis for a single compute group."""

    group_id: str
    group_name: str
    fully_preemptible_nodes: int  # schedulable nodes where ALL tasks are low-priority
    partially_preemptible_nodes: int  # schedulable nodes with SOME low-priority tasks
    preemptible_gpus_on_mixed_nodes: int  # low-pri GPUs on schedulable mixed nodes
    unschedulable_preemptible_nodes: int  # preemptible nodes with status != Ready
    unschedulable_preemptible_gpus: int  # low-pri GPUs on unschedulable nodes
    preemptible_nodes: tuple[PreemptibleNode, ...]


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


def _get_workspace_name_map() -> dict[str, str]:
    """Build workspace_id -> workspace_name lookup from the current session."""
    try:
        session = get_web_session()
    except Exception:
        return {}
    names = getattr(session, "all_workspace_names", None) or {}
    result: dict[str, str] = dict(names)
    ws_id = getattr(session, "workspace_id", None)
    if ws_id and ws_id not in result:
        result[ws_id] = ""
    return result


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
                pool.submit(list_projects, workspace_id=ws_id, session=session): ws_id
                for ws_id in remaining
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    _merge(future.result())
                except Exception:
                    pass

    return projects or list_projects(session=session)


def _get_gpu_availability_all_workspaces() -> list[GPUAvailability]:
    """Fetch GPU availability across all accessible workspaces.

    Falls back to the default (single-workspace) query if session
    discovery fails or no workspace IDs are available.
    """
    try:
        session = get_web_session()
    except Exception:
        return get_accurate_gpu_availability()

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
        return get_accurate_gpu_availability(session=session)

    # Query first workspace serially, remaining in parallel.
    results: list[GPUAvailability] = []
    group_seen: set[str] = set()

    def _merge(items: list[GPUAvailability]) -> None:
        for g in items:
            if g.group_id not in group_seen:
                group_seen.add(g.group_id)
                results.append(g)

    try:
        _merge(get_accurate_gpu_availability(workspace_id=workspace_ids[0], session=session))
    except Exception:
        pass

    remaining = workspace_ids[1:]
    if remaining:
        max_workers = min(len(remaining), _ALLOCATE_MAX_WORKERS)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    get_accurate_gpu_availability, workspace_id=ws_id, session=session
                ): ws_id
                for ws_id in remaining
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    _merge(future.result())
                except Exception:
                    pass

    return results or get_accurate_gpu_availability(session=session)


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
    # 1. Fetch availability across ALL workspaces
    availability = _get_gpu_availability_all_workspaces()

    # 2. Filter by GPU type
    gpu_type_upper = gpu_type.upper()
    filtered = [a for a in availability if gpu_type_upper in (a.gpu_type or "").upper()]

    if not filtered:
        raise ValueError(f"No compute groups found for GPU type '{gpu_type}'")

    # 3. Determine which workspaces have the requested GPU type
    gpu_accessible_workspace_ids: set[str] = {a.workspace_id for a in filtered if a.workspace_id}

    # 4. Build workspace_id -> workspace_name lookup
    ws_name_map = _get_workspace_name_map()

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
            workspace_id=a.workspace_id,
            workspace_name=ws_name_map.get(a.workspace_id, ""),
        )
        for a in filtered
    ]

    # 5. Fetch projects across all accessible workspaces
    projects = _list_projects_all_workspaces()

    # 6. Filter projects: only those in workspaces that have the requested GPU type
    if gpu_accessible_workspace_ids:
        projects = [p for p in projects if p.workspace_id in gpu_accessible_workspace_ids]

    # Sort: projects with budget first, then by priority descending
    project_budgets = [
        ProjectBudget(
            project_id=p.project_id,
            project_name=p.name,
            priority=_priority_value(p),
            remain_budget=p.remain_budget,
            member_remain_budget=p.member_remain_budget,
            has_budget=_has_budget(p),
            workspace_id=p.workspace_id,
            workspace_name=ws_name_map.get(p.workspace_id, ""),
        )
        for p in projects
    ]
    project_budgets.sort(key=lambda p: (p.has_budget, p.priority), reverse=True)

    # 7. Enrich with preemptibility analysis for groups that have low-priority GPUs
    preemptibility = _compute_preemptibility(filtered)

    return AllocateResult(
        gpus=gpus,
        gpu_type=gpu_type,
        groups=groups,
        projects=project_budgets,
        preemptibility=preemptibility,
    )


__all__ = [
    "AllocateResult",
    "GroupPreemptibility",
    "GroupStatus",
    "PreemptibleNode",
    "PreemptibleTask",
    "ProjectBudget",
    "compute_allocate_overview",
    "LOW_PRIORITY_THRESHOLD",
]


# ---------------------------------------------------------------------------
# Preemptibility analysis
# ---------------------------------------------------------------------------


def _compute_preemptibility(
    filtered_availability: list,
) -> dict[str, GroupPreemptibility]:
    """Compute per-group preemptibility using cluster_metric endpoints.

    For each group that has low_priority_gpus > 0, fetches per-task
    priority data and per-node task assignments, then cross-references
    to identify which nodes have preemptible tasks.

    Returns a dict keyed by group_id. Groups with no low-priority GPUs
    or that fail to fetch are omitted.
    """
    result: dict[str, GroupPreemptibility] = {}

    # Collect (workspace_id, resource_type) pairs from filtered groups.
    # resource_type is the GPU type string (e.g. "NVIDIA_H200_SXM_141G").
    # We need it for the cluster_metric API filter.
    ws_resource_map: dict[str, str] = {}
    group_ws_map: dict[str, str] = {}

    for avail in filtered_availability:
        if avail.low_priority_gpus <= 0:
            continue
        ws_id = avail.workspace_id
        gpu_type_str = _full_resource_type(avail.gpu_type)
        if ws_id and gpu_type_str:
            ws_resource_map[ws_id] = gpu_type_str
            group_ws_map[avail.group_id] = ws_id

    if not ws_resource_map:
        return result

    # For each workspace, fetch task and node dimensions.
    for ws_id, resource_type in ws_resource_map.items():
        tasks: list[TaskDimension] = []
        try:
            tasks = list_task_dimension(
                workspace_id=ws_id,
                resource_type=resource_type,
            )
        except Exception as e:
            logger.debug("list_task_dimension failed for ws=%s: %s", ws_id, e)
            continue

        low_pri_tasks = {t.id: t for t in tasks if t.priority <= LOW_PRIORITY_THRESHOLD}

        if not low_pri_tasks:
            continue

        # Build node -> task assignment map from task dimensions.
        # Each task's `nodes` field tells us which nodes it occupies.
        node_tasks: dict[str, list[TaskDimension]] = {}
        for task in tasks:
            for tn in task.nodes:
                node_tasks.setdefault(tn.name, []).append(task)

        # Also fetch node dimensions for groups in this workspace
        # to get gpu totals per node. Use (node_name, group_id) as key
        # to avoid overwriting when a node appears in multiple groups.
        node_gpu_map: dict[tuple[str, str], int] = {}  # (node_name, group_id) -> gpu_total
        node_status_map: dict[
            str, str
        ] = {}  # node_name -> status (e.g. "Ready", "SchedulingDisabled")
        node_group_map: dict[str, str] = {}  # node_name -> group_id (first assignment)

        for group_id in group_ws_map:
            if group_ws_map.get(group_id) != ws_id:
                continue
            try:
                node_dims = list_node_dimension(
                    workspace_id=ws_id,
                    resource_type=resource_type,
                    logic_compute_group_id=group_id,
                )
                for nd in node_dims:
                    node_gpu_map[(nd.name, group_id)] = nd.gpu_total
                    # Track which group each node belongs to.
                    # First assignment wins if a node appears in multiple groups.
                    if nd.name not in node_group_map:
                        node_group_map[nd.name] = group_id
                    if nd.name not in node_status_map:
                        node_status_map[nd.name] = nd.status
            except Exception as e:
                logger.debug("list_node_dimension failed for group=%s: %s", group_id, e)
                continue

        # Group nodes by their compute group
        group_nodes: dict[str, list[PreemptibleNode]] = {}
        for node_name, node_task_list in node_tasks.items():
            group_id = node_group_map.get(node_name, "")
            if not group_id:
                continue

            gpu_total = node_gpu_map.get((node_name, group_id), 8)

            # Calculate preemptible vs other GPUs.
            # The API doesn't provide per-node GPU usage per task.
            # We estimate: task GPU on this node = gpu_total / node_count.
            gpu_preemptible = 0
            gpu_other = 0
            low_pri_on_node: list[PreemptibleTask] = []

            for task in node_task_list:
                node_count = max(len(task.nodes), 1)
                gpu_used = task.gpu_total // node_count if task.gpu_total > 0 else 0
                # If this task is on this specific node, count it
                is_on_node = any(tn.name == node_name for tn in task.nodes)
                if not is_on_node:
                    continue

                if task.priority <= LOW_PRIORITY_THRESHOLD:
                    gpu_preemptible += gpu_used
                    low_pri_on_node.append(
                        PreemptibleTask(
                            id=task.id,
                            name=task.name,
                            priority=task.priority,
                            user=task.user,
                            gpu_used=gpu_used,
                        )
                    )
                else:
                    gpu_other += gpu_used

            node_status = node_status_map.get(node_name, "Ready")
            is_schedulable = node_status == "Ready"
            # A node is only "fully preemptible" if all tasks are low-priority
            # AND the node can actually accept new workloads after preemption.
            is_fully = gpu_other == 0 and gpu_preemptible > 0 and is_schedulable

            pnode = PreemptibleNode(
                node_name=node_name,
                gpu_total=gpu_total,
                gpu_preemptible=gpu_preemptible,
                gpu_other=gpu_other,
                is_fully_preemptible=is_fully,
                is_schedulable=is_schedulable,
                status=node_status,
                low_priority_tasks=tuple(low_pri_on_node),
            )
            group_nodes.setdefault(group_id, []).append(pnode)

        # Build GroupPreemptibility for each group
        for group_id, pnodes in group_nodes.items():
            # Find group name
            group_name = ""
            for avail in filtered_availability:
                if avail.group_id == group_id:
                    group_name = avail.group_name
                    break

            schedulable = [p for p in pnodes if p.is_schedulable]
            unschedulable = [p for p in pnodes if not p.is_schedulable]

            fully = sum(1 for p in schedulable if p.is_fully_preemptible)
            partially = sum(
                1 for p in schedulable if not p.is_fully_preemptible and p.gpu_preemptible > 0
            )
            mixed_gpus = sum(p.gpu_preemptible for p in schedulable if not p.is_fully_preemptible)

            result[group_id] = GroupPreemptibility(
                group_id=group_id,
                group_name=group_name,
                fully_preemptible_nodes=fully,
                partially_preemptible_nodes=partially,
                preemptible_gpus_on_mixed_nodes=mixed_gpus,
                unschedulable_preemptible_nodes=len(unschedulable),
                unschedulable_preemptible_gpus=sum(p.gpu_preemptible for p in unschedulable),
                preemptible_nodes=tuple(pnodes),
            )

    return result


def _full_resource_type(gpu_type_display: str) -> str:
    """Convert a display GPU type (e.g. 'H200') to the full resource_type
    string expected by cluster_metric APIs (e.g. 'NVIDIA_H200_SXM_141G').

    Returns the display string unchanged if no mapping is found — the API
    will return an empty result in that case.
    """
    _TYPE_MAP: dict[str, str] = {
        "H100": "NVIDIA_H100_SXM5_80G",
        "H200": "NVIDIA_H200_SXM_141G",
        "A100": "NVIDIA_A100_SXM4_80G",
        "A800": "NVIDIA_A800_SXM4_80G",
        "A100-40G": "NVIDIA_A100_SXM4_40G",
    }
    key = (gpu_type_display or "").upper().strip()
    # Try exact match first, then substring match
    if key in _TYPE_MAP:
        return _TYPE_MAP[key]
    for short, full in _TYPE_MAP.items():
        if short in key:
            return full
    return gpu_type_display
