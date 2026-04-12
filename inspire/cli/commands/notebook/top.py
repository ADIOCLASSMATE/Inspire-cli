"""Notebook GPU telemetry command (`inspire notebook top`)."""

from __future__ import annotations

import click


@click.command("top")
def notebook_top() -> None:
    """Show GPU utilization and memory for notebooks."""
    click.echo(
        "inspire notebook top has been removed. "
        'Use \'inspire notebook exec <name> "nvidia-smi"\' instead.'
    )
    raise SystemExit(1)


__all__ = ["notebook_top"]
