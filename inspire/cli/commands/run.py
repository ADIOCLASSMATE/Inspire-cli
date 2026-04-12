"""Run command - Quick job submission with smart resource allocation.

Usage:
    inspire run "python train.py"
    inspire run "bash train.sh" --gpus 4 --type H100
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime

import click

from inspire.cli.context import (
    Context,
    EXIT_AUTH_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_GENERAL_ERROR,
    EXIT_SUCCESS,
    EXIT_VALIDATION_ERROR,
    pass_context,
)
from inspire.cli.formatters import human_formatter, json_formatter
from inspire.cli.utils import job_submit
from inspire.cli.utils.auth import AuthManager, AuthenticationError
from inspire.cli.utils.compute_group_autoselect import find_best_compute_group_location
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.config import Config, ConfigError
from inspire.config.workspaces import select_workspace_id


def _get_current_branch() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _resolve_run_resource_and_location(
    ctx: Context,
    *,
    api,  # noqa: ANN001
    gpus: int,
    gpu_type: str,
    location: str | None,
    nodes: int,
) -> tuple[str, str | None]:
    if location:
        return f"{gpus}x{gpu_type}", location

    if ctx.debug and not ctx.json_output:
        click.echo("Checking GPU availability...")

    best, selected_location, selected_group_name = find_best_compute_group_location(
        api,
        gpu_type=gpu_type,
        min_gpus=gpus,
        include_preemptible=True,
        instance_count=nodes,
    )

    if not best:
        if ctx.json_output:
            click.echo(
                json_formatter.format_json_error(
                    "InsufficientResources",
                    f"No compute groups with at least {gpus} {gpu_type} GPUs available",
                    EXIT_VALIDATION_ERROR,
                )
            )
        else:
            click.echo(
                human_formatter.format_error(
                    f"No compute groups with at least {gpus} {gpu_type} GPUs available",
                    hint=(
                        "Try different GPU type or fewer GPUs. Run 'inspire resources list' "
                        "to see availability."
                    ),
                ),
                err=True,
            )
        sys.exit(EXIT_VALIDATION_ERROR)

    resource_str = f"{gpus}x{gpu_type}"
    location = selected_location or selected_group_name or None

    if ctx.debug and not ctx.json_output:
        if getattr(best, "selection_source", "") == "nodes" and getattr(best, "free_nodes", 0):
            click.echo(
                "Auto-selected: "
                f"{selected_group_name}, {best.free_nodes} full nodes free "
                f"({best.available_gpus} GPUs)"
            )
        else:
            preempt_note = (
                f" (+{best.low_priority_gpus} preemptible)"
                if getattr(best, "low_priority_gpus", 0) > 0
                else ""
            )
            click.echo(
                f"Auto-selected: {selected_group_name}, "
                f"{best.available_gpus} GPUs available{preempt_note}"
            )

    return resource_str, location


def _run_flow(
    ctx: Context,
    *,
    command: str,
    gpus: int,
    gpu_type: str,
    name: str | None,
    priority: int | None,
    location: str | None,
    workspace: str | None,
    workspace_id_override: str | None,
    max_time: float,
    image: str | None,
    image_type: str | None,
    nodes: int,
    project: str | None,
    log_file: str | None,
) -> None:
    try:
        config, _ = Config.from_files_and_env(require_target_dir=True)
        api = AuthManager.get_api(config)

        if priority is None:
            priority = config.job_priority
        if image is None:
            image = config.job_image
        if image_type is None:
            image_type = config.job_image_type

        if not image:
            _handle_error(
                ctx,
                "ConfigError",
                "No default image configured. Set [job].image in config.toml "
                "or pass --image on the command line.",
                EXIT_CONFIG_ERROR,
            )
            return

        selected_workspace_id = select_workspace_id(
            config,
            gpu_type=gpu_type,
            explicit_workspace_id=workspace_id_override,
            explicit_workspace_name=workspace,
        )
        if not selected_workspace_id:
            _handle_error(
                ctx,
                "ConfigError",
                "No workspace_id configured for GPU workloads. "
                "Set [workspaces].gpu or INSPIRE_WORKSPACE_ID.",
                EXIT_CONFIG_ERROR,
            )
            return

        resource_str, location = _resolve_run_resource_and_location(
            ctx,
            api=api,
            gpus=gpus,
            gpu_type=gpu_type,
            location=location,
            nodes=nodes,
        )

        if not name:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            branch = _get_current_branch()
            branch_suffix = f"-{branch}" if branch else ""
            name = f"run-{timestamp}{branch_suffix}"

        if ctx.debug and not ctx.json_output:
            click.echo(f"Creating job '{name}'...")

        time.sleep(0.5)

        try:
            selected_project, fallback_msg = job_submit.select_project_for_workspace(
                config,
                workspace_id=selected_workspace_id,
                requested=project,
            )
        except ValueError as e:
            error_type = "QuotaExceeded" if "over quota" in str(e) else "ValidationError"
            _handle_error(ctx, error_type, str(e), EXIT_CONFIG_ERROR)
            return
        project_id = selected_project.project_id

        if not ctx.json_output and fallback_msg:
            click.echo(fallback_msg)
        if ctx.debug and not ctx.json_output:
            click.echo(
                f"Using project: {selected_project.name}{selected_project.get_quota_status()}"
            )

        try:
            submission = job_submit.submit_training_job(
                api,
                config=config,
                name=name,
                command=command,
                resource=resource_str,
                framework="pytorch",
                location=location,
                project_id=project_id,
                workspace_id=selected_workspace_id,
                image=image,
                image_type=image_type,
                priority=priority,
                nodes=nodes,
                max_time_hours=max_time,
                project_name=selected_project.name,
                log_file=log_file,
            )
        except ValueError as e:
            _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
            return

        wrapped_command = submission.wrapped_command
        log_path = submission.log_path
        result = submission.result
        data = submission.data
        job_id = submission.job_id

        if not job_id:
            if ctx.json_output:
                click.echo(json_formatter.format_json(data if data else result))
            else:
                if isinstance(result, dict):
                    message = result.get("message") or "Job created (no job ID returned)"
                    click.echo(human_formatter.format_success(message))
                    if result.get("data") and ctx.debug:
                        click.echo(str(result["data"]))
                else:
                    click.echo(human_formatter.format_success("Job created"))
                    if ctx.debug:
                        click.echo(str(result))
            sys.exit(EXIT_SUCCESS)

        if ctx.json_output:
            click.echo(json_formatter.format_json(data))
        else:
            click.echo(f"Job created: {job_id}")
            if log_path:
                click.echo(f"Log file:  {log_path}")
            if ctx.debug:
                click.echo(f"Name: {name}")
                click.echo(f"Resource: {resource_str}")
                if nodes > 1:
                    click.echo(f"Nodes: {nodes}")
                click.echo(
                    f"Command: {wrapped_command[:80]}{'...' if len(wrapped_command) > 80 else ''}"
                )
                click.echo(f"Check status with: inspire job status {job_id}")

        sys.exit(EXIT_SUCCESS)

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except AuthenticationError as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_GENERAL_ERROR)


@click.command()
@click.argument("command")
@click.option("--gpus", "-g", type=int, default=8, help="Number of GPUs (default: 8)")
@click.option(
    "--type",
    "gpu_type",
    type=click.Choice(["H100", "H200"], case_sensitive=False),
    default="H200",
    help="GPU type (default: H200)",
)
@click.option("--name", "-n", help="Job name (auto-generated if not specified)")
@click.option(
    "--priority",
    type=int,
    default=None,
    help="Task priority 1-10 (default from config [job].priority or 6)",
)
@click.option(
    "--project",
    "-p",
    default=None,
    help="Project name or ID (default from config [context].project or [job].project_id)",
)
@click.option("--location", help="Preferred datacenter location (overrides auto-selection)")
@click.option("--workspace", help="Workspace name (from [workspaces])")
@click.option(
    "--workspace-id",
    "workspace_id_override",
    help="Workspace ID override (highest precedence)",
)
@click.option("--max-time", type=float, default=100.0, help="Max runtime in hours (default: 100)")
@click.option(
    "--image",
    default=None,
    help="Custom Docker image (default from config [job].image)",
)
@click.option(
    "--image-type",
    "image_type",
    default=None,
    help="Image source type: SOURCE_OFFICIAL, SOURCE_PUBLIC, or SOURCE_PERSONAL_VISIBLE (default: SOURCE_PERSONAL_VISIBLE)",
)
@click.option(
    "--nodes", type=int, default=1, help="Number for multi-node training (default: 1)"
)
@click.option(
    "--log-file",
    default=None,
    help=(
        "Remote log file path (absolute). Overrides the default ${INSPIRE_TARGET_DIR}/.inspire/"
        "training_master_<timestamp>.log"
    ),
)
@pass_context
def run(
    ctx: Context,
    command: str,
    gpus: int,
    gpu_type: str,
    name: str | None,
    priority: int | None,
    project: str | None,
    location: str | None,
    workspace: str | None,
    workspace_id_override: str | None,
    max_time: float,
    image: str | None,
    image_type: str | None,
    nodes: int,
    log_file: str | None,
) -> None:
    """Quick job submission with smart resource allocation.

    Automatically selects the compute group with most available capacity.
    If --location is specified, uses that location instead of auto-selecting.

    \b
    Examples:
        inspire run "python train.py"
        inspire run "bash train.sh" --gpus 4 --type H100
    """
    _run_flow(
        ctx,
        command=command,
        gpus=gpus,
        gpu_type=gpu_type,
        name=name,
        priority=priority,
        project=project,
        location=location,
        workspace=workspace,
        workspace_id_override=workspace_id_override,
        max_time=max_time,
        image=image,
        image_type=image_type,
        nodes=nodes,
        log_file=log_file,
    )


__all__ = ["run"]
