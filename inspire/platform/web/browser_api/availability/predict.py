"""Scheduling prediction logic — cross-references node and aggregate data."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .api import get_accurate_gpu_availability

logger = logging.getLogger(__name__)


@dataclass
class SchedulingPrediction:
    """Predicted scheduling outcome for a compute group."""

    group_id: str
    group_name: str
    gpu_type: str
    aggregate_available: int
    aggregate_low_priority: int
    node_free_nodes: int
    node_free_gpus: int
    gpu_per_node: int
    prediction: str  # "immediate", "likely", "preemptible", "queued"
    reason: str  # human-readable explanation


def predict_scheduling(
    gpu_type: str,
    min_gpus: int = 8,
    instance_count: int = 1,
) -> list[SchedulingPrediction]:
    """Predict scheduling outcome for each matching compute group.

    Cross-references node-level data (free nodes/GPUs from workspace API) with
    aggregate data (available_gpus from compute group API) to produce an accurate
    prediction.  The aggregate ``available_gpus`` reflects K8s-level accounting
    and is the ground truth for scheduler capacity.

    Returns predictions sorted best-to-worst (immediate > likely > preemptible > queued).
    """
    from inspire.platform.web.resources import fetch_resource_availability

    gpu_type_upper = (gpu_type or "").upper()
    required_instances = max(1, int(instance_count))
    normalized_min_gpus = max(1, int(min_gpus))

    # Fetch node-level data
    try:
        node_availability = fetch_resource_availability(known_only=False)
    except Exception as exc:
        logger.warning("Node availability fetch failed: %s", exc)
        node_availability = []

    # Fetch aggregate data
    try:
        aggregate = get_accurate_gpu_availability()
    except Exception as exc:
        logger.warning("Aggregate availability fetch failed: %s", exc)
        aggregate = []

    agg_map = {g.group_id: g for g in aggregate}

    # Build a unified map of groups from node data
    seen_groups: dict[str, dict] = {}
    for group in node_availability:
        if gpu_type_upper and gpu_type_upper != "ANY":
            if gpu_type_upper not in (group.gpu_type or "").upper():
                continue
        if (group.gpu_per_node or 0) <= 0:
            continue
        seen_groups[group.group_id] = {
            "group": group,
            "agg": agg_map.get(group.group_id),
        }

    # Also include aggregate-only groups not seen in node data
    for agg in aggregate:
        if agg.group_id in seen_groups:
            continue
        if gpu_type_upper and gpu_type_upper != "ANY":
            if gpu_type_upper not in (agg.gpu_type or "").upper():
                continue
        seen_groups[agg.group_id] = {"group": None, "agg": agg}

    predictions: list[SchedulingPrediction] = []
    for group_id, entry in seen_groups.items():
        node_group = entry["group"]
        agg = entry["agg"]

        agg_available = agg.available_gpus if agg else 0
        agg_low_priority = agg.low_priority_gpus if agg else 0

        gpu_per_node = node_group.gpu_per_node if node_group else (agg.gpu_per_node if agg else 0)
        free_nodes = node_group.free_nodes if node_group else 0
        free_gpus = node_group.free_gpus if node_group else 0
        group_name = (
            (node_group.group_name if node_group else None)
            or (agg.group_name if agg else None)
            or ""
        )
        group_gpu_type = (
            (node_group.gpu_type if node_group else None) or (agg.gpu_type if agg else None) or ""
        )

        total_required = normalized_min_gpus * required_instances
        if gpu_per_node > 0:
            nodes_per_instance = math.ceil(normalized_min_gpus / gpu_per_node)
        else:
            nodes_per_instance = 1
        required_nodes = required_instances * nodes_per_instance

        if agg_available >= total_required and free_nodes >= required_nodes:
            prediction = "immediate"
            reason = (
                f"{agg_available} GPUs available, {free_nodes} free nodes "
                f"(need {required_nodes})"
            )
        elif agg_available >= total_required:
            prediction = "likely"
            reason = (
                f"{agg_available} GPUs available but only {free_nodes} contiguous "
                f"free nodes (need {required_nodes})"
            )
        elif agg_available + agg_low_priority >= total_required:
            prediction = "preemptible"
            reason = (
                f"{agg_available} available + {agg_low_priority} preemptible GPUs "
                f"(need {total_required})"
            )
        else:
            prediction = "queued"
            reason = (
                f"{agg_available} available + {agg_low_priority} preemptible = "
                f"{agg_available + agg_low_priority} total (need {total_required})"
            )

        predictions.append(
            SchedulingPrediction(
                group_id=group_id,
                group_name=group_name,
                gpu_type=group_gpu_type,
                aggregate_available=agg_available,
                aggregate_low_priority=agg_low_priority,
                node_free_nodes=free_nodes,
                node_free_gpus=free_gpus,
                gpu_per_node=gpu_per_node,
                prediction=prediction,
                reason=reason,
            )
        )

    # Sort: immediate > likely > preemptible > queued; within tier prefer more free nodes
    tier_order = {"immediate": 0, "likely": 1, "preemptible": 2, "queued": 3}
    predictions.sort(
        key=lambda p: (tier_order.get(p.prediction, 9), -p.node_free_nodes, -p.aggregate_available),
    )

    return predictions


__all__ = ["SchedulingPrediction", "predict_scheduling"]
