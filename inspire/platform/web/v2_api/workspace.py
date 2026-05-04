"""v2 Workspace API functions.

Endpoints under ``/api/v2/workspace``.
"""

from __future__ import annotations

import logging
from typing import Any

from inspire.platform.web.v2_api.client import post_v2

logger = logging.getLogger(__name__)


def get_basic_info(
    token: str,
    base_url: str,
    workspace_id: str,
) -> dict[str, Any]:
    """Get workspace cluster basic info via v2 API.

    POST /api/v2/workspace?Action=GetBasicInfo

    Returns dict with ``compute_groups``, ``logic_compute_groups``, etc.
    """
    return post_v2(
        "workspace", "GetBasicInfo",
        {"workspace_id": workspace_id},
        token, base_url,
    )


def list_node_dimension(
    token: str,
    base_url: str,
    workspace_id: str,
    *,
    compute_group_id: str | None = None,
) -> list[dict]:
    """List node dimensions (GPU/CPU/memory per node) via v2 API.

    POST /api/v2/workspace?Action=ListNodeDimension

    Returns list of node dimension dicts with ``gpu``, ``cpu``, ``memory``, etc.
    """
    filter_body: dict[str, Any] = {"workspace_id": workspace_id}
    if compute_group_id:
        filter_body["logic_compute_group_id"] = compute_group_id

    data = post_v2(
        "workspace", "ListNodeDimension",
        {"filter": filter_body},
        token, base_url,
    )
    return data.get("node_dimensions", [])


def list_resource_specs(
    token: str,
    base_url: str,
    workspace_id: str,
    compute_group_id: str,
    *,
    schedule_type: str = "SCHEDULE_CONFIG_TYPE_TRAIN",
) -> list[dict]:
    """List resource specs (quota/pricing) for a compute group via v2 API.

    POST /api/v2/workspace?Action=GetScheduleConfig

    ``schedule_type`` should be one of:
        - ``SCHEDULE_CONFIG_TYPE_TRAIN`` (default)
        - ``SCHEDULE_CONFIG_TYPE_DSW`` (notebook)
        - ``SCHEDULE_CONFIG_TYPE_SERVING`` (inference)

    Returns list of spec dicts with ``quota_id``, ``gpu_count``, ``cpu_count``,
    ``memory_size_gib``, ``total_price_per_hour``, ``gpu_info``.
    """
    try:
        data = post_v2(
            "workspace", "GetScheduleConfig",
            {
                "workspace_id": workspace_id,
                "logic_compute_group_id": compute_group_id,
                "schedule_config_type": schedule_type,
            },
            token, base_url,
        )

        raw_key = (
            "predef_train_spec"
            if schedule_type == "SCHEDULE_CONFIG_TYPE_TRAIN" and data.get("use_predef_train_spec")
            else "quota"
            if schedule_type in ("SCHEDULE_CONFIG_TYPE_TRAIN", "SCHEDULE_CONFIG_TYPE_DSW")
            else "serving_quota"
        )

        raw = data.get(raw_key, [])
        if isinstance(raw, str):
            import json
            raw = json.loads(raw)
        if not isinstance(raw, list):
            raw = []

        return [
            {
                "quota_id": s.get("id", s.get("quota_id", "")),
                "gpu_count": s.get("gpu_count", 0),
                "cpu_count": s.get("cpu_count", 0),
                "memory_size_gib": s.get("memory_size", s.get("memory_size_gib", 0)),
                "total_price_per_hour": s.get("total_price_per_hour", 0),
                "gpu_info": s.get("gpu_info"),
            }
            for s in raw
        ]
    except Exception:
        logger.warning(
            "Failed to list resource specs for workspace=%s compute_group=%s schedule_type=%s",
            workspace_id, compute_group_id, schedule_type, exc_info=True,
        )
        return []
