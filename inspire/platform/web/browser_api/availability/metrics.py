"""Browser (web-session) APIs for cluster metric endpoints.

These /api/v1/cluster_metric/* endpoints provide per-task and per-node
dimensional data with priority information, enabling fine-grained
preemptibility analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from inspire.platform.web.browser_api.core import _browser_api_path, _get_base_url, _request_json
from inspire.platform.web.session import WebSession, get_web_session

logger = logging.getLogger(__name__)

__all__ = [
    "ClusterBasicInfo",
    "NodeDimension",
    "NodeTask",
    "TaskDimension",
    "TaskNode",
    "get_cluster_basic_info",
    "list_node_dimension",
    "list_task_dimension",
]

# Referer header matching the platform's distributed training page.
_REFERER_PATH = "/jobs/distributedTraining"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_user_name(user_val: str | dict) -> str:
    """Extract a display name from a user field.

    The API returns user as either a dict {name, id, ...} or a string.
    """
    if isinstance(user_val, dict):
        return user_val.get("name", "")
    return str(user_val)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskNode:
    """A node occupied (partially) by a task."""

    name: str
    gpu_used: int


@dataclass(frozen=True)
class TaskDimension:
    """A task's dimensional data from cluster_metric/list_task_dimension."""

    id: str
    name: str
    priority: int
    user: str
    gpu_total: int
    gpu_used: int
    nodes: tuple[TaskNode, ...]
    project: str
    status: str
    task_type: str
    running_time_ms: int
    created_at: str

    @classmethod
    def from_api_response(cls, data: dict) -> TaskDimension:
        nodes_raw = data.get("nodes_occupied", {}).get("nodes", [])
        # API returns nodes as a list of strings (node names) or dicts.
        nodes = tuple(
            TaskNode(name=n if isinstance(n, str) else n.get("name", ""),
                     gpu_used=n.get("gpu_used", 0) if isinstance(n, dict) else 0)
            for n in nodes_raw
        )
        gpu_info = data.get("gpu", {})
        # user can be a dict {name, id, ...} or a string
        user_val = data.get("user", "")
        user_str = user_val.get("name", "") if isinstance(user_val, dict) else str(user_val)
        # project can be a dict {name, id, ...} or a string
        proj_val = data.get("project", "")
        proj_str = proj_val.get("name", "") if isinstance(proj_val, dict) else str(proj_val)
        # running_time_ms can be a string or int
        rt = data.get("running_time_ms", 0)
        rt_int = int(rt) if isinstance(rt, str) and rt.isdigit() else (rt or 0)
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            priority=data.get("priority", 0),
            user=user_str,
            gpu_total=gpu_info.get("total", 0),
            gpu_used=gpu_info.get("used", 0),
            nodes=nodes,
            project=proj_str,
            status=data.get("status", ""),
            task_type=data.get("type", ""),
            running_time_ms=rt_int,
            created_at=data.get("created_at", ""),
        )


@dataclass(frozen=True)
class NodeTask:
    """A task associated with a node."""

    id: str
    name: str
    task_type: str
    user: str


@dataclass(frozen=True)
class NodeDimension:
    """A node's dimensional data from cluster_metric/list_node_dimension."""

    name: str
    status: str
    gpu_total: int
    gpu_used: int
    gpu_available: int
    tasks: tuple[NodeTask, ...]
    users: tuple[str, ...]

    @classmethod
    def from_api_response(cls, data: dict) -> NodeDimension:
        tasks_raw = data.get("tasks_associated", {}).get("tasks", [])
        tasks = tuple(
            NodeTask(
                id=t.get("id", ""),
                name=t.get("name", ""),
                task_type=t.get("task_type", ""),
                # user can be a dict {name, id, ...} or a string
                user=_extract_user_name(t.get("user", "")),
            )
            for t in tasks_raw
        )
        users_raw = data.get("users_associated", {}).get("users", [])
        users = tuple(str(u) for u in users_raw)
        gpu_info = data.get("gpu", {})
        return cls(
            name=data.get("name", ""),
            status=data.get("status", ""),
            gpu_total=gpu_info.get("total", 0),
            gpu_used=gpu_info.get("used", 0),
            gpu_available=gpu_info.get("available", 0),
            tasks=tasks,
            users=users,
        )


@dataclass(frozen=True)
class ClusterBasicInfo:
    """Cluster hierarchy info from cluster_metric/cluster_basic_info."""

    raw: dict


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------


def list_task_dimension(
    *,
    workspace_id: str,
    resource_type: str = "",
    page_size: int = 1000,
    session: Optional[WebSession] = None,
) -> list[TaskDimension]:
    """List tasks with priority and node-assignment data.

    POST /api/v1/cluster_metric/list_task_dimension
    """
    if session is None:
        session = get_web_session()

    body: dict = {
        "page_num": 1,
        "page_size": page_size,
        "filter": {"workspace_id": workspace_id},
    }
    if resource_type:
        body["filter"]["resource_type"] = resource_type

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/cluster_metric/list_task_dimension"),
        referer=f"{_get_base_url()}{_REFERER_PATH}",
        body=body,
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"list_task_dimension API error: {data.get('message')}")

    items = data.get("data", {}).get("task_dimensions", [])
    return [TaskDimension.from_api_response(item) for item in items]


def list_node_dimension(
    *,
    workspace_id: str,
    resource_type: str = "",
    logic_compute_group_id: str = "",
    page_size: int = 1000,
    session: Optional[WebSession] = None,
) -> list[NodeDimension]:
    """List nodes with task-association and GPU data.

    POST /api/v1/cluster_metric/list_node_dimension
    """
    if session is None:
        session = get_web_session()

    body: dict = {
        "page_num": 1,
        "page_size": page_size,
        "filter": {"workspace_id": workspace_id},
    }
    if resource_type:
        body["filter"]["resource_type"] = resource_type
    if logic_compute_group_id:
        body["filter"]["logic_compute_group_id"] = logic_compute_group_id

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/cluster_metric/list_node_dimension"),
        referer=f"{_get_base_url()}{_REFERER_PATH}",
        body=body,
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"list_node_dimension API error: {data.get('message')}")

    items = data.get("data", {}).get("node_dimensions", [])
    return [NodeDimension.from_api_response(item) for item in items]


def get_cluster_basic_info(
    *,
    workspace_id: str,
    session: Optional[WebSession] = None,
) -> ClusterBasicInfo:
    """Get cluster hierarchy (clusters > compute_groups > logic_compute_groups).

    POST /api/v1/cluster_metric/cluster_basic_info
    """
    if session is None:
        session = get_web_session()

    body = {"workspace_id": workspace_id}

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/cluster_metric/cluster_basic_info"),
        referer=f"{_get_base_url()}{_REFERER_PATH}",
        body=body,
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"cluster_basic_info API error: {data.get('message')}")

    return ClusterBasicInfo(raw=data.get("data", {}))
