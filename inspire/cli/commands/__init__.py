"""CLI command modules."""

from inspire.cli.commands.job import job
from inspire.cli.commands.resources import resources
from inspire.cli.commands.config import config
from inspire.cli.commands.run import run
from inspire.cli.commands.notebook import notebook
from inspire.cli.commands.init import init
from inspire.cli.commands.image import image
from inspire.cli.commands.project import project
from inspire.cli.commands.metrics import metrics
from inspire.cli.commands.inference import inference

__all__ = [
    "job",
    "resources",
    "config",
    "run",
    "notebook",
    "init",
    "image",
    "project",
    "metrics",
    "inference",
]
