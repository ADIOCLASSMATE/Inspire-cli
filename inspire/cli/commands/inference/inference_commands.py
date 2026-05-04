"""Inference serving subcommands: create, stop, list, detail."""

from __future__ import annotations

import click

from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_AUTH_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_GENERAL_ERROR,
    pass_context,
)
from inspire.cli.formatters import json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.config import Config, ConfigError


def _get_v2_resources(config: Config) -> tuple[str, str]:
    """Get v2 token and base_url. Raises on failure.

    Returns:
        (token, base_url)
    """
    from inspire.platform.web.session import get_v2_token

    token = get_v2_token(config)
    if not token:
        raise ConfigError(
            "v2 API authentication required. Set INSPIRE_USERNAME and INSPIRE_PASSWORD."
        )
    return token, config.base_url


@click.command("create")
@click.option("--name", required=True, help="Serving name")
@click.option("--image", required=True, help="Docker image URL")
@click.option("--model-id", required=True, help="Model ID from model repository")
@click.option("--model-version", type=int, default=1, help="Model version (default: 1)")
@click.option("--command", default="sleep infinity", help="Startup command (default: sleep infinity)")
@click.option("--port", type=int, default=2400, help="Service port (default: 2400)")
@click.option("--replicas", type=int, default=1, help="Number of replicas (default: 1)")
@click.option("--nodes-per-replica", type=int, default=1, help="Nodes per replica (default: 1)")
@click.option("--spec-id", help="Spec/quota ID (use inspire_status to discover)")
@click.option("--workspace", help="Workspace name or ID")
@click.option("--project", help="Project name or ID")
@click.option("--priority", type=int, default=4, help="Task priority (default: 4)")
@click.option(
    "--image-type",
    type=click.Choice(["SOURCE_PUBLIC", "SOURCE_PRIVATE", "SOURCE_OFFICIAL"]),
    default="SOURCE_PRIVATE",
    help="Image source type (default: SOURCE_PRIVATE)",
)
@pass_context
def create(
    ctx: Context,
    name: str,
    image: str,
    model_id: str,
    model_version: int,
    command: str,
    port: int,
    replicas: int,
    nodes_per_replica: int,
    spec_id: str,
    workspace: str,
    project: str,
    priority: int,
    image_type: str,
) -> None:
    """Create a new inference (model serving) deployment.

    \b
    Example:
        inspire inference create --name my-model \\
            --image docker.sii.shaipower.online/my-model:v1 \\
            --model-id model-abc --spec-id quota-xyz \\
            --workspace ws-xxx --project my-project
    """
    try:
        config, _ = Config.from_files_and_env()
        token, base_url = _get_v2_resources(config)

        # Resolve workspace
        if not workspace:
            workspace = config.job_workspace_id
        if not workspace:
            _handle_error(ctx, "ValidationError", "Workspace is required (--workspace)", EXIT_CONFIG_ERROR)
            return

        # Build config
        infer_config = {
            "name": name,
            "workspace_id": workspace,
            "project_id": project or "",
            "logic_compute_group_id": "",  # resolved by server if empty
            "command": command,
            "image": image,
            "image_type": image_type,
            "model_id": model_id,
            "model_version": model_version,
            "port": port,
            "replicas": replicas,
            "node_num_per_replica": nodes_per_replica,
            "task_priority": priority,
            "spec_id": spec_id or "",
        }

        from inspire.platform.web.v2_api.inference import create_inference

        result = create_inference(token, base_url, infer_config)
        serving_id = result.get("inference_serving_id", result.get("id", ""))

        if ctx.json_output:
            click.echo(json_formatter.format_json({
                "serving_id": serving_id,
                "name": name,
                "status": "created",
            }))
        else:
            click.echo(f"Inference serving created: {serving_id}")
            click.echo(f"  Name: {name}")
            click.echo(f"  Model: {model_id} v{model_version}")
            click.echo(f"  Port: {port} | Replicas: {replicas}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


@click.command("stop")
@click.argument("serving_id")
@pass_context
def stop(ctx: Context, serving_id: str) -> None:
    """Stop a running inference serving.

    \b
    Example:
        inspire inference stop sv-abc123
    """
    try:
        config, _ = Config.from_files_and_env()
        token, base_url = _get_v2_resources(config)

        from inspire.platform.web.v2_api.inference import stop_inference

        stop_inference(token, base_url, serving_id)

        if ctx.json_output:
            click.echo(json_formatter.format_json({
                "serving_id": serving_id,
                "status": "stopped",
            }))
        else:
            click.echo(f"Inference serving stopped: {serving_id}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


@click.command("list")
@click.option("--workspace", "-w", help="Workspace ID to filter by")
@click.option("--limit", "-n", type=int, default=10, help="Max items to show (default: 10)")
@pass_context
def list_servings(ctx: Context, workspace: str, limit: int) -> None:
    """List inference serving deployments.

    \b
    Example:
        inspire inference list
        inspire inference list --workspace ws-xxx --limit 20
    """
    try:
        config, _ = Config.from_files_and_env()
        token, base_url = _get_v2_resources(config)

        if not workspace:
            workspace = config.job_workspace_id or ""
        if not workspace:
            _handle_error(ctx, "ValidationError", "Workspace is required (--workspace)", EXIT_CONFIG_ERROR)
            return

        from inspire.platform.web.v2_api.inference import list_inference

        items, total = list_inference(token, base_url, workspace, page_size=limit)

        if ctx.json_output:
            click.echo(json_formatter.format_json({
                "items": [
                    {
                        "serving_id": i.inference_serving_id,
                        "name": i.name,
                        "status": i.status,
                        "image": i.image,
                        "model_id": i.model_id,
                        "replicas": i.replicas,
                        "created_at": i.created_at,
                    }
                    for i in items
                ],
                "total": total,
            }))
        else:
            if not items:
                click.echo("No inference servings found.")
                return
            click.echo(f"{'Serving ID':<40} {'Name':<20} {'Status':<12} {'Replicas':>8}")
            click.echo("-" * 84)
            for i in items:
                click.echo(
                    f"{i.inference_serving_id:<40} {i.name[:20]:<20} "
                    f"{i.status:<12} {i.replicas:>8}"
                )
            click.echo(f"Total: {total} serving(s)")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


@click.command("detail")
@click.argument("serving_id")
@pass_context
def detail(ctx: Context, serving_id: str) -> None:
    """Show inference serving details.

    \b
    Example:
        inspire inference detail sv-abc123
    """
    try:
        config, _ = Config.from_files_and_env()
        token, base_url = _get_v2_resources(config)

        from inspire.platform.web.v2_api.inference import get_inference_detail

        data = get_inference_detail(token, base_url, serving_id)

        if ctx.json_output:
            click.echo(json_formatter.format_json(data))
        else:
            click.echo(f"Inference Serving: {serving_id}")
            click.echo(f"  Name:       {data.get('name', 'N/A')}")
            click.echo(f"  Status:     {data.get('status', 'N/A')}")
            click.echo(f"  Image:      {data.get('image', 'N/A')}")
            click.echo(f"  Model ID:   {data.get('model_id', 'N/A')}")
            click.echo(f"  Port:       {data.get('port', 'N/A')}")
            click.echo(f"  Replicas:   {data.get('replicas', 'N/A')}")
            click.echo(f"  Command:    {data.get('command', 'N/A')[:100]}")
            click.echo(f"  Created:    {data.get('created_at', 'N/A')}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


__all__ = ["create", "detail", "list_servings", "stop"]
