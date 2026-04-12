"""Notebook SSH command (removed)."""

from __future__ import annotations

import click


@click.command("ssh")
def notebook_ssh() -> None:
    """Notebook SSH has been removed."""
    click.echo("inspire notebook ssh has been removed. Use 'inspire notebook exec' instead.")
    raise SystemExit(1)


__all__ = ["notebook_ssh"]
