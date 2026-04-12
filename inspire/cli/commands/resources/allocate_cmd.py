"""GPU availability overview command.

Shows the current state of compute groups and project budgets for a given
GPU request, letting the user decide the best course of action.
"""

from __future__ import annotations

import click

from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_AUTH_ERROR,
    EXIT_CONFIG_ERROR,
    pass_context,
)
from inspire.cli.formatters import json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.platform.web.browser_api.availability.allocate import (
    AllocateResult,
    ProjectBudget,
    compute_allocate_overview,
)
from inspire.platform.web.session import SessionExpiredError


def _format_human(result: AllocateResult) -> str:
    """Format overview as human-readable output."""
    lines = [""]
    lines.append(f"GPU Request: {result.gpus}x {result.gpu_type}")
    lines.append("")
    lines.append("Compute Groups:")
    lines.append("  available = idle GPUs ready to use")
    lines.append("  low_pri   = GPUs running low-priority tasks (can be preempted by higher priority)")
    lines.append("  [FREE]        = enough idle GPUs for the request, no preemption needed")
    lines.append("  [PREEMPTIBLE] = not enough idle GPUs, but low_pri count suggests preemption may work")
    lines.append("  [QUEUED]      = not enough idle or preemptible GPUs, must queue and wait")
    lines.append("-" * 72)
    for g in result.groups:
        if g.has_free:
            tag = "FREE"
        elif g.has_preemptible:
            tag = "PREEMPTIBLE"
        else:
            tag = "QUEUED"
        lines.append(
            f"  {g.group_name:<20} "
            f"available={g.available_gpus:>5}  "
            f"low_pri={g.low_priority_gpus:>3}  "
            f"total={g.total_gpus:>5}  "
            f"[{tag}]"
        )
    lines.append("")

    # Projects
    lines.append("Projects (budget=卡时, sorted by has_budget then priority desc):")
    lines.append("-" * 72)
    for p in result.projects:
        budget_str = _format_budget(p)
        lines.append(
            f"  {p.project_name:<25} "
            f"priority={p.priority:<3}  "
            f"{budget_str}"
        )
    lines.append("")

    return "\n".join(lines)


def _format_budget(p: ProjectBudget) -> str:
    """Format budget info for a project."""
    if not p.has_budget:
        return "budget: EXHAUSTED"
    parts = []
    if p.remain_budget is not None:
        parts.append(f"project={_short_num(p.remain_budget)}")
    if p.member_remain_budget is not None:
        parts.append(f"member={_short_num(p.member_remain_budget)}")
    if parts:
        return f"budget: {', '.join(parts)}"
    return "budget: OK"


def _short_num(n: float) -> str:
    """Format large numbers compactly."""
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:.0f}"


def _format_json(result: AllocateResult) -> str:
    """Format overview as JSON output."""
    data = {
        "gpus": result.gpus,
        "gpu_type": result.gpu_type,
        "groups": [
            {
                "id": g.group_id,
                "name": g.group_name,
                "gpu_type": g.gpu_type,
                "available": g.available_gpus,
                "low_priority": g.low_priority_gpus,
                "total": g.total_gpus,
                "has_free": g.has_free,
                "has_preemptible": g.has_preemptible,
            }
            for g in result.groups
        ],
        "projects": [
            {
                "id": p.project_id,
                "name": p.project_name,
                "priority": p.priority,
                "remain_budget": p.remain_budget,
                "member_remain_budget": p.member_remain_budget,
                "has_budget": p.has_budget,
            }
            for p in result.projects
        ],
    }
    return json_formatter.format_json(data)


@click.command("allocate")
@click.option(
    "--gpus", "-g",
    type=int,
    default=8,
    help="Number of GPUs needed (default: 8)",
)
@click.option(
    "--type", "gpu_type",
    type=click.Choice(["H100", "H200"], case_sensitive=False),
    default="H200",
    help="GPU type (default: H200)",
)
@pass_context
def allocate(
    ctx: Context,
    gpus: int,
    gpu_type: str,
) -> None:
    """Show GPU availability and project budget for a resource request.

    Read-only overview. Run this BEFORE submitting jobs to decide which
    compute group (--location) and project (--project) to use.

    Output columns:
      available = idle GPUs ready to use
      low_pri   = GPUs running low-priority tasks (preemptible by higher priority)
      [FREE]        = enough idle GPUs, no preemption needed
      [PREEMPTIBLE] = not enough idle, but low_pri count suggests preemption may work
      [QUEUED]      = must queue and wait

    Note: low_pri is an aggregate per-group number. Actual preemption depends on
    whether low-pri tasks are concentrated on the same node, which this tool
    cannot determine.

    \b
    Examples:
        inspire allocate                    # Default: 8x H200
        inspire allocate --gpus 4 --type H100
    """
    try:
        result = compute_allocate_overview(
            gpus=gpus,
            gpu_type=gpu_type,
        )

        if ctx.json_output:
            click.echo(_format_json(result))
        else:
            click.echo(_format_human(result))

    except SessionExpiredError as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except ValueError as e:
        _handle_error(ctx, "ValidationError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
