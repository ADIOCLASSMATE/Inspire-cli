"""v2 Inference serving API functions.

Endpoints under ``/api/v2/inference_serving``.
"""

from __future__ import annotations

from typing import Any

from inspire.platform.web.v2_api.client import post_v2
from inspire.platform.web.v2_api.models import V2InferenceInfo


def list_inference(
    token: str,
    base_url: str,
    workspace_id: str,
    *,
    page_num: int = 1,
    page_size: int = 100,
) -> tuple[list[V2InferenceInfo], int]:
    """List inference services via v2 API.

    POST /api/v2/inference_serving?Action=ListServings

    Returns:
        (list of V2InferenceInfo, total count)
    """
    data = post_v2(
        "inference_serving", "ListServings",
        {
            "workspace_id": workspace_id,
            "page_size": page_size,
            "page_num": page_num,
        },
        token, base_url,
    )
    items = data.get("list", [])
    total = data.get("total", 0)
    return [V2InferenceInfo.from_api_response(i) for i in items], total


def get_inference_detail(
    token: str,
    base_url: str,
    serving_id: str,
) -> dict[str, Any]:
    """Get inference serving details via v2 API.

    POST /api/v2/inference_serving?Action=GetServing
    """
    return post_v2(
        "inference_serving", "GetServing",
        {"inference_serving_id": serving_id},
        token, base_url,
    )


def create_inference(
    token: str,
    base_url: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Create an inference serving via v2 API.

    POST /api/v2/inference_serving?Action=CreateServing

    ``config`` must include: name, workspace_id, project_id,
    logic_compute_group_id, command, image, model_id, model_version,
    port, replicas, node_num_per_replica, task_priority, spec_id.
    """
    return post_v2(
        "inference_serving", "CreateServing", config, token, base_url,
    )


def stop_inference(
    token: str,
    base_url: str,
    serving_id: str,
) -> None:
    """Stop an inference serving via v2 API.

    POST /api/v2/inference_serving?Action=StopServing
    """
    post_v2(
        "inference_serving", "StopServing",
        {"inference_serving_id": serving_id},
        token, base_url,
    )
