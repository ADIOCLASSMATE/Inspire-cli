"""Resource commands for Inspire CLI."""

from __future__ import annotations

import click

from .allocate_cmd import allocate
from .resources_list import list_resources
from .resources_nodes import list_nodes


@click.group()
def resources() -> None:
    """View available compute resources."""
    pass


resources.add_command(list_resources)
resources.add_command(list_nodes)
resources.add_command(allocate)
