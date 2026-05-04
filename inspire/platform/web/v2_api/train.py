"""v2 Train (job) API functions.

Endpoints under ``/api/v2/train``.
"""

from __future__ import annotations

from typing import Any

from inspire.platform.web.v2_api.client import V2ApiError, post_v2
from inspire.platform.web.v2_api.models import V2JobInfo


def list_jobs(
    token: str,
    base_url: str,
    workspace_id: str,
    *,
    page_num: int = 1,
    page_size: int = 100,
    created_by: str | None = None,
    status: str | None = None,
) -> tuple[list[V2JobInfo], int]:
    """List training jobs via v2 API.

    POST /api/v2/train?Action=ListJobs

    Returns:
        (list of V2JobInfo, total count)
    """
    payload: dict[str, Any] = {
        "page_num": page_num,
        "page_size": page_size,
        "workspace_id": workspace_id,
    }
    if created_by:
        payload["created_by"] = created_by
    if status:
        payload["status"] = status

    data = post_v2("train", "ListJobs", payload, token, base_url)
    jobs_data = data.get("jobs", [])
    total = data.get("total", 0)
    return [V2JobInfo.from_api_response(j) for j in jobs_data], total


def get_job_detail(
    token: str,
    base_url: str,
    job_id: str,
) -> dict[str, Any]:
    """Get training job details via v2 API.

    POST /api/v2/train?Action=GetJob
    """
    return post_v2("train", "GetJob", {"job_id": job_id}, token, base_url)


def stop_job(
    token: str,
    base_url: str,
    job_id: str,
) -> None:
    """Stop a training job via v2 API.

    POST /api/v2/train?Action=StopJob
    """
    post_v2("train", "StopJob", {"job_id": job_id}, token, base_url)


def get_job_logs(
    token: str,
    base_url: str,
    job_id: str,
    instance_count: int = 1,
    *,
    page_size: int = 200,
    start_timestamp_ms: str | None = None,
    end_timestamp_ms: str | None = None,
) -> tuple[list[dict], int]:
    """Fetch training job logs via v2 API.

    POST /api/v2/train?Action=GetJobLog

    Returns:
        (list of log entry dicts, total count)
    """
    pod_names = [f"{job_id}-worker-{i}" for i in range(instance_count)]

    filter_body: dict[str, Any] = {"podNames": pod_names}
    if start_timestamp_ms:
        filter_body["start_timestamp_ms"] = start_timestamp_ms
    if end_timestamp_ms:
        filter_body["end_timestamp_ms"] = end_timestamp_ms

    body: dict[str, Any] = {
        "page_size": page_size,
        "filter": filter_body,
        "sorter": [
            {"field": "time", "sort": "descend"},
            {"field": "log-id.keyword", "sort": "descend"},
        ],
    }

    data = post_v2("train", "GetJobLog", body, token, base_url)
    logs = data.get("logs", [])
    total = data.get("total", 0)
    return logs, total


def get_task_metrics(
    token: str,
    base_url: str,
    compute_group_id: str,
    task_id: str,
    metric_types: list[str],
    start_timestamp: int,
    end_timestamp: int,
    *,
    interval_second: int = 60,
    task_type: str = "distributed_training",
    running_round: int | None = None,
) -> list[dict]:
    """Fetch task resource metrics via v2 API.

    POST /api/v2/train?Action=GetTaskMetric

    Supported metric_types:
        - ``gpu_usage_rate``
        - ``gpu_memory_usage_rate``
        - ``cpu_usage_rate``
        - ``memory_usage_rate``
        - ``disk_io_read``, ``disk_io_write``
        - ``network_io_read``, ``network_io_write``
        - ``network_storage_io_read``, ``network_storage_io_write``

    Returns:
        List of time series metric group dicts.
    """
    filter_body: dict[str, Any] = {
        "logic_compute_group_id": compute_group_id,
        "task_type": task_type,
        "task_id": task_id,
    }
    if running_round is not None:
        filter_body["running_round"] = running_round

    body: dict[str, Any] = {
        "metric_types": metric_types,
        "filter": filter_body,
        "time_range": {
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "interval_second": interval_second,
        },
    }

    data = post_v2("train", "GetTaskMetric", body, token, base_url)
    return data.get("time_seris_metric_groups", data.get("time_series_metric_groups", []))
