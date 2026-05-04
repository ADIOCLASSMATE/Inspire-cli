"""Inference commands for managing model serving deployments."""

from __future__ import annotations

import click

from .inference_commands import create, detail, list_servings, stop


@click.group()
def inference() -> None:
    """Manage inference (model serving) deployments."""


inference.add_command(create)
inference.add_command(stop)
inference.add_command(list_servings)
inference.add_command(detail)

__all__ = ["inference"]
