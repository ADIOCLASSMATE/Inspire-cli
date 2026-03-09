"""Resources predict command — predict scheduling outcome before submitting."""

from __future__ import annotations

import sys

import click

from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_AUTH_ERROR,
    EXIT_SUCCESS,
    pass_context,
)
from inspire.cli.formatters import human_formatter, json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.platform.web.session import SessionExpiredError


_PREDICTION_LABELS = {
    "immediate": "will start",
    "likely": "will start",
    "preemptible": "will start (preempt)",
    "queued": "will queue",
}


def _format_prediction_table(predictions, *, gpu_type: str, gpus: int) -> None:
    lines = [
        "",
        f"{gpu_type} Compute Groups (requesting {gpus} GPUs):",
        "─" * 85,
        (
            f"  {'Location':<25} {'Free GPUs':>10} {'Preempt':>10} "
            f"{'Free Nodes':>12} {'Prediction':>20}"
        ),
        "─" * 85,
    ]

    for p in predictions:
        location = p.group_name[:24]
        label = _PREDICTION_LABELS.get(p.prediction, p.prediction)
        free_gpus = max(p.aggregate_available, 0)

        if p.prediction in ("immediate", "likely"):
            marker = ">>>"
        elif p.prediction == "preemptible":
            marker = " >>"
        else:
            marker = "   "

        lines.append(
            f"{marker} {location:<24} {free_gpus:>10} "
            f"{p.aggregate_low_priority:>10} {p.node_free_nodes:>12} "
            f"{label:>20}"
        )

    lines.append("─" * 85)
    lines.append("")
    lines.append("Free GPUs   = GPUs not allocated to any workload (K8s scheduler view)")
    lines.append("Preempt     = GPUs running low-priority jobs (can be freed via preemption)")
    lines.append("")

    click.echo("\n".join(lines))


@click.command("predict")
@click.option(
    "--gpus",
    "-g",
    type=int,
    default=8,
    help="Number of GPUs to request (default: 8)",
)
@click.option(
    "--type",
    "gpu_type",
    type=click.Choice(["H100", "H200"], case_sensitive=False),
    required=True,
    help="GPU type to check",
)
@click.option(
    "--nodes",
    "-n",
    type=int,
    default=1,
    help="Number of nodes (default: 1)",
)
@pass_context
def predict_resources(
    ctx: Context,
    gpus: int,
    gpu_type: str,
    nodes: int,
) -> None:
    """Predict whether a job will start immediately or queue.

    Cross-references node-level and aggregate-level availability data
    to predict scheduling outcomes for each compute group.

    \b
    Examples:
        inspire resources predict --type H200
        inspire resources predict --gpus 16 --type H200
        inspire resources predict --type H100 --nodes 2
    """
    try:
        from inspire.platform.web.browser_api.availability.predict import predict_scheduling

        predictions = predict_scheduling(
            gpu_type=gpu_type,
            min_gpus=gpus,
            instance_count=nodes,
        )

        if not predictions:
            if ctx.json_output:
                click.echo(json_formatter.format_json({"predictions": []}))
            else:
                click.echo(
                    human_formatter.format_error(
                        f"No {gpu_type} compute groups found",
                    )
                )
            sys.exit(EXIT_SUCCESS)

        if ctx.json_output:
            output = [
                {
                    "group_id": p.group_id,
                    "group_name": p.group_name,
                    "gpu_type": p.gpu_type,
                    "aggregate_available": p.aggregate_available,
                    "aggregate_low_priority": p.aggregate_low_priority,
                    "node_free_nodes": p.node_free_nodes,
                    "node_free_gpus": p.node_free_gpus,
                    "prediction": p.prediction,
                    "reason": p.reason,
                }
                for p in predictions
            ]
            click.echo(json_formatter.format_json({"predictions": output}))
        else:
            _format_prediction_table(predictions, gpu_type=gpu_type, gpus=gpus)

    except (SessionExpiredError, ValueError) as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
