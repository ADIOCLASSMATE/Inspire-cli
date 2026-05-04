"""Metrics commands for monitoring GPU training jobs."""

from __future__ import annotations

import click

from .metrics_show import show
from .metrics_health import health


@click.group()
def metrics() -> None:
    """Monitor GPU training job resource metrics."""


metrics.add_command(show)
metrics.add_command(health)

__all__ = ["metrics"]
