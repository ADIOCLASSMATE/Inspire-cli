"""Shared domain models for the Inspire web layer."""

from enum import Enum


class GPUType(Enum):
    """GPU type enumeration."""

    H100 = "H100"
    H200 = "H200"
