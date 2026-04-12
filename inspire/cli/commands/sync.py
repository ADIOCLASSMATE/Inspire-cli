"""Sync command - Deprecated.

The inspire sync command has been removed.
Use rsync or scp to sync code to the remote machine.
"""

from __future__ import annotations

import click

from inspire.cli.context import pass_context, Context


@click.command()
@pass_context
def sync(ctx: Context) -> None:
    """Sync local code to the Bridge shared filesystem (deprecated)."""
    click.echo(
        "inspire sync has been removed. Use rsync or scp to sync code to the remote machine."
    )
