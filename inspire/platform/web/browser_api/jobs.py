"""Browser (web-session) APIs for jobs and users.

The web UI exposes job CRUD and listing endpoints (and related user listings)
that require a web-session cookie (SSO). All endpoints live under /api/v1/.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from inspire.platform.web.browser_api.core import _browser_api_path, _get_base_url, _request_json
from inspire.platform.web.session import DEFAULT_WORKSPACE_ID, WebSession, get_web_session

__all__ = [
    "InstanceInfo",
    "JobInfo",
    "create_job",
    "fetch_job_logs",
    "get_current_user",
    "get_job_detail",
    "get_train_job_workdir",
    "get_train_resource_prices",
    "list_job_events",
    "list_job_instances",
    "list_job_users",
    "list_jobs",
    "resolve_train_resource_spec_price",
    "stop_job",
]


@dataclass
class JobInfo:
    """Training job information."""

    job_id: str
    name: str
    status: str
    command: str
    created_at: str
    finished_at: Optional[str]
    created_by_name: str
    created_by_id: str
    project_id: str
    project_name: str
    compute_group_name: str
    gpu_type: str
    gpu_count: int
    instance_count: int
    priority: int
    workspace_id: str

    @classmethod
    def from_api_response(cls, data: dict) -> "JobInfo":
        framework_config = data.get("framework_config", [{}])[0]
        gpu_info = framework_config.get("instance_spec_price_info", {}).get("gpu_info", {})

        return cls(
            job_id=data.get("job_id", ""),
            name=data.get("name", ""),
            status=data.get("status", ""),
            command=data.get("command", ""),
            created_at=data.get("created_at", ""),
            finished_at=data.get("finished_at"),
            created_by_name=data.get("created_by", {}).get("name", ""),
            created_by_id=data.get("created_by", {}).get("id", ""),
            project_id=data.get("project_id", ""),
            project_name=data.get("project_name", ""),
            compute_group_name=data.get("logic_compute_group_name", ""),
            gpu_type=gpu_info.get("gpu_type_display", ""),
            gpu_count=framework_config.get("gpu_count", 0),
            instance_count=framework_config.get("instance_count", 1),
            priority=data.get("priority", 0),
            workspace_id=data.get("workspace_id", ""),
        )


def list_jobs(
    workspace_id: Optional[str] = None,
    created_by: Optional[str] = None,
    status: Optional[str] = None,
    page_num: int = 1,
    page_size: int = 50,
    session: Optional[WebSession] = None,
) -> tuple[list[JobInfo], int]:
    """List training jobs using the browser API."""
    if session is None:
        session = get_web_session()

    if workspace_id is None:
        workspace_id = session.workspace_id or DEFAULT_WORKSPACE_ID

    body: dict[str, Any] = {
        "workspace_id": workspace_id,
        "page_num": page_num,
        "page_size": page_size,
    }

    if created_by:
        body["created_by"] = created_by
    if status:
        body["status"] = status

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/train_job/list"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        body=body,
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"API error: {data.get('message')}")

    jobs_data = data.get("data", {}).get("jobs", [])
    total = data.get("data", {}).get("total", 0)

    jobs = [JobInfo.from_api_response(j) for j in jobs_data]
    return jobs, total


def get_current_user(session: Optional[WebSession] = None) -> dict:
    """Get current user details."""
    if session is None:
        session = get_web_session()

    data = _request_json(
        session,
        "GET",
        _browser_api_path("/user/detail"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        timeout=30,
    )
    return data.get("data", {})


def list_job_users(
    workspace_id: Optional[str] = None,
    session: Optional[WebSession] = None,
) -> list[dict]:
    """List users who have created jobs."""
    if session is None:
        session = get_web_session()

    if workspace_id is None:
        workspace_id = session.workspace_id or DEFAULT_WORKSPACE_ID

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/train_job/users"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        body={"workspace_id": workspace_id},
        timeout=30,
    )
    return data.get("data", {}).get("items", [])


def get_train_job_workdir(
    *,
    project_id: str,
    workspace_id: str,
    session: Optional[WebSession] = None,
) -> str | None:
    """Fetch the training job workdir for a project within a workspace."""
    if session is None:
        session = get_web_session()

    project_id = str(project_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    if not project_id or not workspace_id:
        raise ValueError("project_id and workspace_id are required")

    body = {
        "project_id": project_id,
        "workspace_id": workspace_id,
    }

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/train_job/workdir"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        body=body,
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"API error: {data.get('message')}")

    payload = data.get("data")
    if isinstance(payload, str):
        value = payload.strip()
        return value or None

    return None


def list_job_events(
    job_id: str,
    session: Optional[WebSession] = None,
) -> list[dict]:
    """List K8s events for a training job. Best-effort: returns [] on any error."""
    try:
        if session is None:
            session = get_web_session()

        data = _request_json(
            session,
            "POST",
            _browser_api_path("/train_job/job_event_list"),
            referer=f"{_get_base_url()}/jobs/distributedTraining",
            body={"job_id": job_id},
            timeout=30,
        )

        if data.get("code") != 0:
            return []

        events = data.get("data", {}).get("events", [])
        if not isinstance(events, list):
            return []
        return events
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Instance list & job logs (direct /api/v1 endpoints)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstanceInfo:
    """Instance (pod) information for a training job."""

    name: str
    instance_status: str
    instance_type: str
    node: str
    running_time_ms: int
    started_at: str
    finished_at: Optional[str]

    @classmethod
    def from_api_response(cls, data: dict) -> InstanceInfo:
        return cls(
            name=data.get("name", ""),
            instance_status=data.get("instance_status", ""),
            instance_type=data.get("instance_type", ""),
            node=data.get("node", ""),
            running_time_ms=data.get("running_time_ms", 0),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at"),
        )


def list_job_instances(
    job_id: str,
    session: Optional[WebSession] = None,
) -> list[InstanceInfo]:
    """List instances (pods) for a training job.

    POST /api/v1/train_job/instance_list

    Returns pod names needed to call fetch_job_logs().
    """
    if session is None:
        session = get_web_session()

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/train_job/instance_list"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        body={"jobId": job_id, "page_num": 1, "page_size": -1},
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"list_job_instances API error: {data.get('message')}")

    items = data.get("data", {}).get("items", [])
    if not items and isinstance(data.get("data"), list):
        items = data["data"]
    return [InstanceInfo.from_api_response(item) for item in items]


# Default sorter for log queries: newest first.
_DEFAULT_LOG_SORTER = [
    {"field": "time", "sort": "descend"},
    {"field": "log-id.keyword", "sort": "descend"},
]


def fetch_job_logs(
    *,
    pod_names: list[str],
    start_timestamp_ms: str | None = None,
    end_timestamp_ms: str | None = None,
    page_size: int = 200,
    session: Optional[WebSession] = None,
) -> list[dict]:
    """Fetch log entries for specific pods.

    POST /api/v1/logs/train

    Args:
        pod_names: Pod names from list_job_instances().
        start_timestamp_ms: Optional start time filter (milliseconds as string).
        end_timestamp_ms: Optional end time filter (milliseconds as string).
        page_size: Number of log entries per request (default 200).
        session: Optional web session.

    Returns:
        List of log entry dicts from the API. Each entry typically contains
        fields like "time", "log-id.keyword", "content", "level", etc.
    """
    if session is None:
        session = get_web_session()

    filter_body: dict[str, Any] = {"podNames": pod_names}
    if start_timestamp_ms is not None:
        filter_body["start_timestamp_ms"] = start_timestamp_ms
    if end_timestamp_ms is not None:
        filter_body["end_timestamp_ms"] = end_timestamp_ms

    body: dict[str, Any] = {
        "page_size": page_size,
        "filter": filter_body,
        "sorter": _DEFAULT_LOG_SORTER,
    }

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/logs/train"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        body=body,
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"fetch_job_logs API error: {data.get('message')}")

    logs_data = data.get("data", {})
    if isinstance(logs_data, dict):
        return logs_data.get("logs", [])
    if isinstance(logs_data, list):
        return logs_data
    return []


# ---------------------------------------------------------------------------
# Job detail & stop (via /api/v1 — replaces OpenAPI /openapi/v1)
# ---------------------------------------------------------------------------


def get_job_detail(
    job_id: str,
    session: Optional[WebSession] = None,
) -> dict:
    """Get training job details via browser API.

    POST /api/v1/train_job/detail

    Args:
        job_id: The job ID (e.g. ``job-xxxxxxxx-...``).
        session: Optional web session. If None, fetched automatically.

    Returns:
        Full API response dict with ``{"code": 0, "data": {...}}``.
    """
    if session is None:
        session = get_web_session()

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/train_job/detail"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        body={"job_id": job_id},
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"get_job_detail API error: {data.get('message')}")

    return data


def stop_job(
    job_id: str,
    session: Optional[WebSession] = None,
) -> dict:
    """Stop a training job via browser API.

    POST /api/v1/train_job/stop

    Args:
        job_id: The job ID.
        session: Optional web session. If None, fetched automatically.

    Returns:
        API response dict with ``{"code": 0}`` on success.
    """
    if session is None:
        session = get_web_session()

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/train_job/stop"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        body={"job_id": job_id},
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"stop_job API error: {data.get('message')}")

    return data


# ---------------------------------------------------------------------------
# Job creation & resource pricing (via /api/v1)
# ---------------------------------------------------------------------------


def get_train_resource_prices(
    *,
    workspace_id: str,
    logic_compute_group_id: str,
    session: Optional[WebSession] = None,
) -> list[dict]:
    """Fetch resource spec prices for a training compute group.

    POST /api/v1/resource_prices/logic_compute_groups/

    Args:
        workspace_id: Workspace ID.
        logic_compute_group_id: Compute group ID to query pricing for.
        session: Optional web session.

    Returns:
        List of price entries, each typically containing ``quota_id``,
        ``cpu_count``, ``gpu_count``, ``memory_size_gib``, ``gpu_info``,
        and ``cpu_info``.
    """
    if session is None:
        session = get_web_session()

    body = {
        "workspace_id": workspace_id,
        "schedule_config_type": "SCHEDULE_CONFIG_TYPE_TRAIN",
        "logic_compute_group_id": logic_compute_group_id,
    }

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/resource_prices/logic_compute_groups/"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        body=body,
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(
            f"get_train_resource_prices API error: {data.get('message')}"
        )

    payload = data.get("data", [])
    if isinstance(payload, list):
        return payload
    # Handle nested response shapes
    if isinstance(payload, dict):
        for key in ("lcg_resource_spec_prices", "resource_spec_prices", "list"):
            items = payload.get(key)
            if isinstance(items, list):
                return items
    return []


def resolve_train_resource_spec_price(
    *,
    resource_prices: list[dict],
    gpu_count: int,
    gpu_type: str,
    logic_compute_group_id: str,
    default_cpu_count: int = 180,
    default_mem_gi: int = 1800,
) -> dict:
    """Match pricing data to GPU count/type and build a ``resource_spec_price`` dict.

    Iterates *resource_prices* (from :func:`get_train_resource_prices`) to find
    the entry matching *gpu_count* (and, when possible, *gpu_type*). Falls back
    to conservative defaults if no exact match is found.

    Returns a dict with keys: ``cpu_type``, ``cpu_count``, ``gpu_type``,
    ``gpu_count``, ``memory_size_gib``, ``logic_compute_group_id``, ``quota_id``.
    """
    # Sensible fallback
    resource_spec_price: dict = {
        "cpu_type": "",
        "cpu_count": default_cpu_count,
        "gpu_type": gpu_type or "",
        "gpu_count": gpu_count,
        "memory_size_gib": default_mem_gi,
        "logic_compute_group_id": logic_compute_group_id,
        "quota_id": "",
    }

    for price_entry in resource_prices:
        entry_gpu_count = price_entry.get("gpu_count", 0)
        if entry_gpu_count != gpu_count:
            continue

        gpu_info = price_entry.get("gpu_info") or {}
        entry_gpu_type = str(gpu_info.get("gpu_type", ""))
        # Accept if GPU type matches (e.g. "NVIDIA_H200_SXM_141G" contains "H200")
        if gpu_type and entry_gpu_type and gpu_type not in entry_gpu_type:
            continue

        cpu_info = price_entry.get("cpu_info") or {}
        resource_spec_price = {
            "cpu_type": str(cpu_info.get("cpu_type", "")),
            "cpu_count": price_entry.get("cpu_count", default_cpu_count),
            "gpu_type": entry_gpu_type,
            "gpu_count": entry_gpu_count,
            "memory_size_gib": price_entry.get("memory_size_gib", default_mem_gi),
            "logic_compute_group_id": logic_compute_group_id,
            "quota_id": str(price_entry.get("quota_id", "")),
        }
        break

    return resource_spec_price


def create_job(
    *,
    name: str,
    command: str,
    framework: str,
    logic_compute_group_id: str,
    project_id: str,
    workspace_id: str,
    image: str,
    image_type: str,
    instance_count: int,
    gpu_count: int,
    cpu_count: int,
    mem_gi: int,
    shm_gi: int,
    resource_spec_price: dict,
    task_priority: int = 4,
    auto_fault_tolerance: bool = False,
    enable_slow_detect: bool = False,
    enable_vccl: bool = False,
    enable_troubleshoot: bool = False,
    session: Optional[WebSession] = None,
) -> dict:
    """Create a training job via browser API.

    POST /api/v1/train_job/create

    Args:
        name: Job name.
        command: Shell command to run.
        framework: Training framework (e.g. ``"pytorch"``).
        logic_compute_group_id: Compute group ID.
        project_id: Project ID.
        workspace_id: Workspace ID.
        image: Full Docker image URL.
        image_type: Image source type (e.g. ``"SOURCE_PRIVATE"``).
        instance_count: Number of instances (nodes).
        gpu_count: GPUs per instance.
        cpu_count: CPU cores per instance.
        mem_gi: Memory in GiB per instance.
        shm_gi: Shared memory in GiB.
        resource_spec_price: Pricing dict from :func:`resolve_train_resource_spec_price`.
        task_priority: Priority value (default 4).  Must not exceed project priority.
        auto_fault_tolerance: Enable automatic fault tolerance.
        enable_slow_detect: Enable slow node detection.
        enable_vccl: Enable VCCL.
        enable_troubleshoot: Enable troubleshooting.
        session: Optional web session.

    Returns:
        Full API response dict with ``{"code": 0, "data": {"job_id": "..."}}``.
    """
    if session is None:
        session = get_web_session()

    framework_config_item = {
        "image_type": image_type,
        "image": image,
        "instance_count": instance_count,
        "shm_gi": shm_gi,
        "cpu": cpu_count,
        "gpu_count": gpu_count,
        "mem_gi": mem_gi,
        "resource_spec_price": resource_spec_price,
    }

    body = {
        "name": name,
        "command": command,
        "framework": framework,
        "logic_compute_group_id": logic_compute_group_id,
        "project_id": project_id,
        "workspace_id": workspace_id,
        "task_priority": task_priority,
        "auto_fault_tolerance": auto_fault_tolerance,
        "enable_slow_detect": enable_slow_detect,
        "enable_vccl": enable_vccl,
        "enable_troubleshoot": enable_troubleshoot,
        "framework_config": [framework_config_item],
    }

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/train_job/create"),
        referer=f"{_get_base_url()}/jobs/distributedTraining",
        body=body,
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"create_job API error: {data.get('message')}")

    return data
