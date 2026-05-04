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
    GroupPreemptibility,
    ProjectBudget,
    compute_allocate_overview,
    LOW_PRIORITY_THRESHOLD,
)
from inspire.platform.web.session import SessionExpiredError


def _format_human(result: AllocateResult) -> str:
    """Format overview as human-readable output."""
    lines = [""]

    # Determine if workspace column is needed
    ws_ids_groups = {g.workspace_id for g in result.groups} - {""}
    ws_ids_projects = {p.workspace_id for p in result.projects} - {""}
    show_workspace = len(ws_ids_groups | ws_ids_projects) > 1

    lines.append(f"GPU Request: {result.gpus}x {result.gpu_type}")
    lines.append("")
    lines.append("Compute Groups:")
    lines.append("  available = idle GPUs ready to use")
    lines.append(
        f"  low_pri   = GPUs running low-priority tasks (priority <= {LOW_PRIORITY_THRESHOLD}, can be preempted)"
    )
    lines.append("  [FREE]        = enough idle GPUs for the request, no preemption needed")
    lines.append(
        "  [PREEMPTIBLE] = not enough idle GPUs, but low_pri count suggests preemption may work"
    )
    lines.append("  [QUEUED]      = not enough idle or preemptible GPUs, must queue and wait")
    lines.append("-" * 72)
    for g in result.groups:
        if g.has_free:
            tag = "FREE"
        elif g.has_preemptible:
            tag = "PREEMPTIBLE"
        else:
            tag = "QUEUED"
        ws_col = f"workspace={g.workspace_name or g.workspace_id:<20} " if show_workspace else ""
        lines.append(
            f"  {g.group_name:<20} "
            f"{ws_col}"
            f"available={g.available_gpus:>5}  "
            f"low_pri={g.low_priority_gpus:>3}  "
            f"total={g.total_gpus:>5}  "
            f"[{tag}]"
        )

        # Show preemptibility detail for this group if available
        pre = result.preemptibility.get(g.group_id)
        if pre and pre.preemptible_nodes:
            lines.append(
                f"    {'':<20} fully_preemptible_nodes={pre.fully_preemptible_nodes}  "
                f"partially_preemptible_nodes={pre.partially_preemptible_nodes}  "
                f"mixed_node_low_pri_gpus={pre.preemptible_gpus_on_mixed_nodes}"
            )
            if pre.unschedulable_preemptible_nodes > 0:
                lines.append(
                    f"    {'':<20} unschedulable_nodes={pre.unschedulable_preemptible_nodes}  "
                    f"unschedulable_low_pri_gpus={pre.unschedulable_preemptible_gpus}  "
                    f"(status != Ready, cannot schedule new tasks)"
                )
            # Only show nodes that have low-priority tasks (skip noise)
            for pn in pre.preemptible_nodes:
                if pn.gpu_preemptible == 0:
                    continue
                if pn.is_fully_preemptible:
                    label = "FULLY PREEMPTIBLE"
                elif not pn.is_schedulable:
                    label = f"SCHEDULING DISABLED ({pn.status})"
                else:
                    label = "mixed"
                lines.append(
                    f"      node={pn.node_name:<25} "
                    f"gpu_total={pn.gpu_total}  "
                    f"low_pri_gpu={pn.gpu_preemptible}  "
                    f"other_gpu={pn.gpu_other}  "
                    f"[{label}]"
                )
                for pt in pn.low_priority_tasks:
                    lines.append(
                        f"        task={pt.name:<30} "
                        f"priority={pt.priority}  "
                        f"user={pt.user:<15} "
                        f"gpu={pt.gpu_used}"
                    )
    lines.append("")

    # Projects
    lines.append("Projects (budget=卡时, sorted by has_budget then priority desc):")
    lines.append("-" * 72)
    for p in result.projects:
        budget_str = _format_budget(p)
        ws_col = f"workspace={p.workspace_name or p.workspace_id:<20} " if show_workspace else ""
        lines.append(f"  {p.project_name:<25} {ws_col}priority={p.priority:<3}  {budget_str}")
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
    groups_data = []
    for g in result.groups:
        g_dict: dict = {
            "id": g.group_id,
            "name": g.group_name,
            "gpu_type": g.gpu_type,
            "workspace_id": g.workspace_id,
            "workspace_name": g.workspace_name,
            "available": g.available_gpus,
            "low_priority": g.low_priority_gpus,
            "total": g.total_gpus,
            "has_free": g.has_free,
            "has_preemptible": g.has_preemptible,
        }
        pre = result.preemptibility.get(g.group_id)
        if pre:
            g_dict["preemptibility"] = _preemptibility_to_dict(pre)
        groups_data.append(g_dict)

    data = {
        "gpus": result.gpus,
        "gpu_type": result.gpu_type,
        "groups": groups_data,
        "projects": [
            {
                "id": p.project_id,
                "name": p.project_name,
                "workspace_id": p.workspace_id,
                "workspace_name": p.workspace_name,
                "priority": p.priority,
                "remain_budget": p.remain_budget,
                "member_remain_budget": p.member_remain_budget,
                "has_budget": p.has_budget,
            }
            for p in result.projects
        ],
    }
    return json_formatter.format_json(data)


def _preemptibility_to_dict(pre: GroupPreemptibility) -> dict:
    """Convert GroupPreemptibility to a JSON-serializable dict."""
    return {
        "group_id": pre.group_id,
        "group_name": pre.group_name,
        "fully_preemptible_nodes": pre.fully_preemptible_nodes,
        "partially_preemptible_nodes": pre.partially_preemptible_nodes,
        "preemptible_gpus_on_mixed_nodes": pre.preemptible_gpus_on_mixed_nodes,
        "unschedulable_preemptible_nodes": pre.unschedulable_preemptible_nodes,
        "unschedulable_preemptible_gpus": pre.unschedulable_preemptible_gpus,
        "nodes": [
            {
                "node_name": pn.node_name,
                "gpu_total": pn.gpu_total,
                "gpu_preemptible": pn.gpu_preemptible,
                "gpu_other": pn.gpu_other,
                "is_fully_preemptible": pn.is_fully_preemptible,
                "is_schedulable": pn.is_schedulable,
                "status": pn.status,
                "low_priority_tasks": [
                    {
                        "id": pt.id,
                        "name": pt.name,
                        "priority": pt.priority,
                        "user": pt.user,
                        "gpu_used": pt.gpu_used,
                    }
                    for pt in pn.low_priority_tasks
                ],
            }
            for pn in pre.preemptible_nodes
        ],
    }


@click.command("allocate")
@click.option(
    "--gpus",
    "-g",
    type=int,
    default=8,
    help="Number of GPUs needed (default: 8)",
)
@click.option(
    "--type",
    "gpu_type",
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

    When preemptibility data is available, each PREEMPTIBLE group shows
    per-node detail: which nodes have low-priority tasks, how many GPUs
    they use, and whether the node is fully preemptible.

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
