"""Browser (web-session) APIs for resource availability."""

from __future__ import annotations

from .allocate import AllocateResult, GroupStatus, ProjectBudget, compute_allocate_overview
from .api import (
    get_accurate_gpu_availability,
    get_full_free_node_counts,
    list_compute_groups,
)
from .models import FullFreeNodeCount, GPUAvailability
from .select import find_best_compute_group_accurate

__all__ = [
    "AllocateResult",
    "FullFreeNodeCount",
    "GPUAvailability",
    "GroupStatus",
    "ProjectBudget",
    "compute_allocate_overview",
    "find_best_compute_group_accurate",
    "get_accurate_gpu_availability",
    "get_full_free_node_counts",
    "list_compute_groups",
]
