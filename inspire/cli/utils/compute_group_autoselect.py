"""Helpers for auto-selecting compute groups based on browser availability."""

from __future__ import annotations

from typing import Any, Optional

from inspire.platform.web import browser_api as browser_api_module
from inspire.compute_groups import load_compute_groups_from_config


def find_best_compute_group_location(
    *,
    gpu_type: str,
    min_gpus: int,
    instance_count: int = 1,
    include_preemptible: bool = True,
    config_compute_groups: Optional[list[dict]] = None,
) -> tuple[object | None, str | None, str, str]:
    """Return (best, selected_location, selected_group_name, selected_group_id).

    Uses browser API to find the best compute group, then maps the result
    to a location and group name via *config_compute_groups*.

    The *selected_group_id* is the browser-returned ``group_id`` (a
    ``logic_compute_group_id`` string), which can be used directly in
    job creation without further mapping.
    """
    best = browser_api_module.find_best_compute_group_accurate(
        gpu_type=gpu_type,
        min_gpus=min_gpus,
        include_preemptible=include_preemptible,
        instance_count=instance_count,
    )
    if not best:
        return None, None, "", ""

    selected_group_name = getattr(best, "group_name", "") or ""
    selected_group_id = getattr(best, "group_id", "") or ""
    selected_location = None

    # Load compute groups from config for location mapping (fallback)
    groups = []
    if config_compute_groups:
        groups = list(load_compute_groups_from_config(config_compute_groups))

    for group in groups:
        if getattr(group, "compute_group_id", None) == selected_group_id:
            selected_group_name = getattr(group, "name", selected_group_name) or selected_group_name
            selected_location = getattr(group, "location", None)
            break

    return best, selected_location, selected_group_name, selected_group_id


__all__ = ["find_best_compute_group_location"]
