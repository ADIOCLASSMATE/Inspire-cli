"""Notebook subcommands."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from .notebook_create_flow import (
    format_resource_display,
    match_gpu_type,
    maybe_run_post_start,
    parse_resource_string,
    run_notebook_create,
)
from .notebook_exec_flow import run_notebook_exec
from .notebook_lookup import (
    _ZERO_WORKSPACE_ID,
    _collect_all_workspace_ids,
    _format_notebook_resource,
    _list_notebooks_for_workspace,
    _list_notebooks_for_workspace_paginated,
    _notebook_id_from_item,
    _resolve_notebook_id,
    _sort_notebook_items,
    _try_get_current_user_ids,
    _unique_workspace_ids,
)
from .notebook_presenters import _print_notebook_detail, _print_notebook_list
from .notebook_reusable_flow import check_notebook_idle_via_nvidia_smi
from .notebook_ssh_flow import run_notebook_ssh
from .notebook_terminal_flow import run_notebook_terminal
from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_VALIDATION_ERROR,
    pass_context,
)
from inspire.cli.formatters import json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.notebook_cli import (
    get_base_url,
    load_config,
    require_web_session,
    resolve_json_output,
)
from inspire.cli.utils.notebook_post_start import (
    NO_WAIT_POST_START_WARNING,
    resolve_notebook_post_start_spec,
)
from inspire.config import ConfigError
from inspire.config.workspaces import select_workspace_id
from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web import session as web_session_module
from inspire.platform.web.browser_api import NotebookFailedError


@click.command("create")
@click.option(
    "--name",
    "-n",
    help="Notebook name (auto-generated if omitted)",
)
@click.option(
    "--workspace",
    help="Workspace name (from [workspaces])",
)
@click.option(
    "--workspace-id",
    help="Workspace ID (overrides auto-selection)",
)
@click.option(
    "--resource",
    "-r",
    default=None,
    help="Resource spec (e.g., 1xH200, 4xH100, 4CPU) (default from config [notebook].resource)",
)
@click.option(
    "--project",
    "-p",
    default=None,
    help="Project name or ID (default from config [context].project or [job].project_id)",
)
@click.option(
    "--image",
    "-i",
    default=None,
    help=(
        "Image name/URL (default from config [notebook].image or [job].image; prompts interactively "
        "if still omitted)"
    ),
)
@click.option(
    "--shm-size",
    type=int,
    default=None,
    help="Shared memory size in GB (default: INSPIRE_SHM_SIZE/job.shm_size, else 32)",
)
@click.option(
    "--auto-stop/--no-auto-stop",
    default=False,
    help="Auto-stop when idle",
)
@click.option(
    "--auto/--no-auto",
    default=True,
    help="Auto-select best available compute group based on availability (default: auto)",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help=(
        "Wait for notebook to reach RUNNING status "
        "(default: enabled; still required when a post-start action is configured)"
    ),
)
@click.option(
    "--post-start",
    type=str,
    default=None,
    help="Post-start action after RUNNING: none or a shell command",
)
@click.option(
    "--post-start-script",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="Local shell script to upload and run in the notebook after RUNNING",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@click.option(
    "--priority",
    type=click.IntRange(1, 10),
    default=None,
    help="Task priority (1-10, default from config [job].priority or 6)",
)
@pass_context
def create_notebook_cmd(
    ctx: Context,
    name: Optional[str],
    workspace: Optional[str],
    workspace_id: Optional[str],
    resource: Optional[str],
    project: Optional[str],
    image: Optional[str],
    shm_size: Optional[int],
    auto_stop: bool,
    auto: bool,
    wait: bool,
    post_start: Optional[str],
    post_start_script: Optional[Path],
    json_output: bool,
    priority: Optional[int],
) -> None:
    """Create a new interactive notebook instance.

    \b
    Examples:
        inspire notebook create                     # Interactive mode, auto-select GPU
        inspire notebook create -r 1xH200           # 1 GPU H200
        inspire notebook create -r 4xH100 -n mytest # 4 GPUs H100
        inspire notebook create -r 4x               # 4 GPUs, auto-select type
        inspire notebook create -r 8x               # 8 GPUs (full node), auto-select type
        inspire notebook create -r 4CPU             # 4 CPUs
        inspire notebook create -r 1xH100 --shm-size 64  # With 64GB shared memory
        inspire notebook create --no-auto -r 1xH200 # Disable auto-select
        inspire notebook create --post-start 'bash /workspace/bootstrap.sh'
        inspire notebook create --post-start-script scripts/notebook_bootstrap.sh
        inspire notebook create --post-start none --no-wait
        inspire notebook create --priority 5        # Set task priority to 5
    """
    if post_start and post_start_script:
        raise click.UsageError("Use either --post-start or --post-start-script, not both.")

    project_explicit = bool(project)

    run_notebook_create(
        ctx,
        name=name,
        workspace=workspace,
        workspace_id=workspace_id,
        resource=resource,
        project=project,
        image=image,
        shm_size=shm_size,
        auto_stop=auto_stop,
        auto=auto,
        wait=wait,
        post_start=post_start,
        post_start_script=post_start_script,
        json_output=json_output,
        priority=priority,
        project_explicit=project_explicit,
    )


@click.command("stop")
@click.argument("notebook")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def stop_notebook_cmd(
    ctx: Context,
    notebook: str,
    json_output: bool,
) -> None:
    """Stop a running notebook instance.

    \b
    Examples:
        inspire notebook stop abc123-def456
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Stopping notebooks requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )

    base_url = get_base_url()
    config = load_config(ctx)
    notebook_id, _ = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook,
        json_output=json_output,
    )

    try:
        result = browser_api_module.stop_notebook(notebook_id=notebook_id, session=session)
    except Exception as e:
        _handle_error(ctx, "APIError", f"Failed to stop notebook: {e}", EXIT_API_ERROR)
        return

    if json_output:
        click.echo(
            json_formatter.format_json(
                {
                    "notebook_id": notebook_id,
                    "status": "stopping",
                    "result": result,
                }
            )
        )
        return

    click.echo(f"Notebook '{notebook_id}' is being stopped.")
    click.echo(f"Use 'inspire notebook status {notebook_id}' to check status.")


@click.command("start")
@click.argument("notebook")
@click.option(
    "--wait/--no-wait",
    default=False,
    help="Wait for notebook to reach RUNNING status (still required for post-start actions)",
)
@click.option(
    "--post-start",
    type=str,
    default=None,
    help="Post-start action after RUNNING: none or a shell command",
)
@click.option(
    "--post-start-script",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="Local shell script to upload and run in the notebook after RUNNING",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def start_notebook_cmd(
    ctx: Context,
    notebook: str,
    wait: bool,
    post_start: Optional[str],
    post_start_script: Optional[Path],
    json_output: bool,
) -> None:
    """Start a stopped notebook instance.

    \b
    Examples:
        inspire notebook start 78822a57-3830-44e7-8d45-e8b0d674fc44
        inspire notebook start ring-8h100-test
        inspire notebook start ring-8h100-test --wait
        inspire notebook start ring-8h100-test --post-start 'bash /workspace/bootstrap.sh'
        inspire notebook start ring-8h100-test --post-start-script scripts/notebook_bootstrap.sh
        inspire notebook start ring-8h100-test --post-start none
    """
    if post_start and post_start_script:
        raise click.UsageError("Use either --post-start or --post-start-script, not both.")

    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Starting notebooks requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )

    base_url = get_base_url()
    config = load_config(ctx)
    try:
        post_start_spec = resolve_notebook_post_start_spec(
            config=config,
            post_start=post_start,
            post_start_script=post_start_script,
        )
    except ValueError as e:
        _handle_error(ctx, "ValidationError", str(e), EXIT_CONFIG_ERROR)
        return

    notebook_id, _ = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook,
        json_output=json_output,
    )

    try:
        result = browser_api_module.start_notebook(notebook_id=notebook_id, session=session)
    except Exception as e:
        _handle_error(ctx, "APIError", f"Failed to start notebook: {e}", EXIT_API_ERROR)
        return

    if not json_output:
        click.echo(f"Notebook '{notebook_id}' is being started.")

    notebook_detail = None
    if wait or post_start_spec is not None:
        if not wait and post_start_spec is not None and not json_output:
            click.echo(NO_WAIT_POST_START_WARNING, err=True)
        if not json_output:
            click.echo("Waiting for notebook to reach RUNNING status...")
        try:
            notebook_detail = browser_api_module.wait_for_notebook_running(
                notebook_id=notebook_id, session=session
            )
            if not json_output:
                click.echo("Notebook is now RUNNING.")
        except NotebookFailedError as e:
            _handle_error(
                ctx,
                "NotebookFailed",
                f"Notebook failed to start: {e}",
                EXIT_API_ERROR,
                hint=e.events or "Check Events tab in web UI for details.",
            )
            return
        except TimeoutError as e:
            _handle_error(
                ctx,
                "Timeout",
                f"Timed out waiting for notebook to reach RUNNING: {e}",
                EXIT_API_ERROR,
            )
            return

    if notebook_detail and post_start_spec is not None:
        quota = notebook_detail.get("quota") or {}
        gpu_count = quota.get("gpu_count", 0) or 0
        maybe_run_post_start(
            ctx,
            notebook_id=notebook_id,
            session=session,
            post_start_spec=post_start_spec,
            gpu_count=gpu_count,
            json_output=json_output,
        )

    if json_output:
        click.echo(
            json_formatter.format_json(
                {
                    "notebook_id": notebook_id,
                    "status": "starting",
                    "result": result,
                }
            )
        )
        return

    click.echo(f"Use 'inspire notebook status {notebook_id}' to check status.")


@click.command("status")
@click.argument("notebook")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def notebook_status(
    ctx: Context,
    notebook: str,
    json_output: bool,
) -> None:
    """Get status of a notebook instance.

    \b
    Examples:
        inspire notebook status notebook-abc-123
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Notebook status requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )

    base_url = get_base_url()

    config = load_config(ctx)
    notebook_id, _ = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook,
        json_output=json_output,
    )

    try:
        data = web_session_module.request_json(
            session,
            "GET",
            f"{base_url}/api/v1/notebook/{notebook_id}",
            headers={"Accept": "application/json"},
            timeout=30,
        )
    except ValueError as e:
        message = str(e)
        if "API returned 404" in message:
            _handle_error(
                ctx,
                "NotFound",
                f"Notebook instance '{notebook_id}' not found",
                EXIT_API_ERROR,
            )
        else:
            _handle_error(ctx, "APIError", message, EXIT_API_ERROR)
        return
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
        return

    if data.get("code") == 0:
        notebook = data.get("data", {})
        if json_output:
            click.echo(json_formatter.format_json(notebook))
        else:
            _print_notebook_detail(notebook)
        return

    _handle_error(
        ctx,
        "APIError",
        data.get("message", "Unknown error"),
        EXIT_API_ERROR,
    )
    return


@click.command("reusable")
@click.option(
    "--resource",
    "-r",
    required=True,
    help="Resource spec (e.g., 1xH200, 4xH100, 4CPU)",
)
@pass_context
def reusable_notebook_cmd(
    ctx: Context,
    resource: str,
) -> None:
    """Find reusable running notebooks (always JSON output)."""

    # Force JSON output regardless of global flags.
    ctx.json_output = True

    try:
        gpu_count_req, gpu_pattern_req, cpu_count_req = parse_resource_string(resource)
    except ValueError as e:
        _handle_error(ctx, "ValidationError", str(e), EXIT_VALIDATION_ERROR)
        return

    session = require_web_session(
        ctx,
        hint=(
            "Finding reusable notebooks requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )
    config = load_config(ctx)
    base_url = get_base_url()

    workspace_ids = _collect_all_workspace_ids(session, config)
    if not workspace_ids:
        _handle_error(
            ctx,
            "ConfigError",
            "No workspace_id configured or available for notebook lookup.",
            EXIT_CONFIG_ERROR,
            hint=(
                "Set [workspaces].cpu/[workspaces].gpu in config.toml, set INSPIRE_WORKSPACE_ID, "
                "or login again to discover accessible workspaces."
            ),
        )
        return

    user_ids = _try_get_current_user_ids(session, base_url=base_url)

    candidates: list[dict] = []
    for ws_id in workspace_ids:
        try:
            items = _list_notebooks_for_workspace_paginated(
                session,
                base_url=base_url,
                workspace_id=ws_id,
                user_ids=user_ids,
                status=["RUNNING"],
            )
        except Exception:
            continue

        for item in items:
            item_copy = dict(item)
            item_copy["workspace_id"] = ws_id
            candidates.append(item_copy)

    matched: list[dict] = []

    req_resource_display = format_resource_display(gpu_count_req, gpu_pattern_req, cpu_count_req)

    for item in candidates:
        quota = item.get("quota") or {}
        gpu_count_item = int(quota.get("gpu_count", 0) or 0)
        cpu_count_item = quota.get("cpu_count")

        gpu_type_item = ""
        if gpu_count_item > 0:
            # GPU type may appear in different places depending on API response.
            gpu_info = (item.get("resource_spec_price") or {}).get("gpu_info") or {}
            node_gpu_info = (item.get("node") or {}).get("gpu_info") or {}
            gpu_type_item = str(
                gpu_info.get("gpu_product_simple")
                or gpu_info.get("gpu_type_display")
                or node_gpu_info.get("gpu_product_simple")
                or node_gpu_info.get("gpu_type_display")
                or node_gpu_info.get("gpu_type")
                or quota.get("gpu_type")
                or "GPU"
            )

        # Strict match
        if gpu_count_req == 0 and gpu_pattern_req.upper() == "CPU":
            if gpu_count_item != 0:
                continue
            if cpu_count_req is not None and cpu_count_item is not None:
                try:
                    if int(cpu_count_item) != int(cpu_count_req):
                        continue
                except Exception:
                    pass
        else:
            if gpu_count_item != gpu_count_req:
                continue
            if gpu_pattern_req.upper() != "GPU":
                if not match_gpu_type(gpu_pattern_req, gpu_type_item):
                    continue

        notebook_id = _notebook_id_from_item(item)
        if not notebook_id:
            continue

        # Idle check for GPU notebooks only
        if gpu_count_req > 0:
            if not check_notebook_idle_via_nvidia_smi(notebook_id=notebook_id, session=session):
                continue

        matched.append(
            {
                "id": notebook_id,
                "name": str(item.get("name") or ""),
                "workspace_id": str(item.get("workspace_id") or ""),
                "resource": req_resource_display,
                "status": str(item.get("status") or ""),
                "resource_detail": _format_notebook_resource(item),
            }
        )

    click.echo(
        json_formatter.format_json(
            {
                "total": len(matched),
                "items": matched,
            }
        )
    )


@click.command("list")
@click.option(
    "--workspace",
    help="Workspace name (from [workspaces])",
)
@click.option(
    "--workspace-id",
    help="Workspace ID (defaults to configured workspace)",
)
@click.option(
    "--all",
    "-a",
    "show_all",
    is_flag=True,
    help="Show all notebooks (not just your own)",
)
@click.option(
    "--all-workspaces",
    "-A",
    is_flag=True,
    help="List notebooks across all configured workspaces (cpu/gpu/internet)",
)
@click.option(
    "--limit",
    "-n",
    type=int,
    default=20,
    show_default=True,
    help="Max number of notebooks to show",
)
@click.option(
    "--status",
    "-s",
    multiple=True,
    help="Filter by status (e.g. RUNNING, STOPPED). Repeatable.",
)
@click.option(
    "--name",
    "keyword",
    default="",
    help="Filter by notebook name (keyword search)",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def list_notebooks(
    ctx: Context,
    workspace: Optional[str],
    workspace_id: Optional[str],
    show_all: bool,
    all_workspaces: bool,
    limit: int,
    status: tuple[str, ...],
    keyword: str,
    json_output: bool,
) -> None:
    """List notebook/interactive instances.

    \b
    Examples:
        inspire notebook list
        inspire notebook list --all
        inspire notebook list -n 10
        inspire notebook list -s RUNNING
        inspire notebook list -s RUNNING -s STOPPED
        inspire notebook list --name my-notebook
        inspire notebook list --workspace gpu -s RUNNING -n 5
        inspire notebook list --all-workspaces
        inspire notebook list --json
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Listing notebooks requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )
    config = load_config(ctx)

    workspace_ids: list[str] = []
    if workspace_id:
        workspace_ids = [workspace_id]
    elif workspace:
        try:
            resolved = select_workspace_id(config, explicit_workspace_name=workspace)
        except ConfigError as e:
            _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
            return
        if resolved:
            workspace_ids = [resolved]
    elif all_workspaces:
        candidates: list[str] = []
        for ws_id in (
            config.workspace_cpu_id,
            config.workspace_gpu_id,
            config.workspace_internet_id,
            config.job_workspace_id,
        ):
            if ws_id:
                candidates.append(ws_id)
        if config.workspaces:
            candidates.extend(config.workspaces.values())
        if getattr(session, "workspace_id", None):
            candidates.append(str(session.workspace_id))

        workspace_ids = _unique_workspace_ids(candidates)
        for ws_id in workspace_ids:
            try:
                select_workspace_id(config, explicit_workspace_id=ws_id)
            except ConfigError as e:
                _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
                return

    if not workspace_ids:
        # Default to GPU workspace for notebook list (notebooks are typically GPU workloads)
        try:
            resolved = select_workspace_id(config, gpu_type="H200")
        except ConfigError:
            # Fall back to any available workspace
            try:
                resolved = select_workspace_id(config)
            except ConfigError as e:
                _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
                return

        resolved = resolved or getattr(session, "workspace_id", None)
        resolved = None if resolved == _ZERO_WORKSPACE_ID else resolved
        if not resolved:
            _handle_error(
                ctx,
                "ConfigError",
                "No workspace_id configured or provided.",
                EXIT_CONFIG_ERROR,
                hint=(
                    "Use --workspace-id, set [workspaces].cpu/[workspaces].gpu in config.toml, "
                    "or set INSPIRE_WORKSPACE_ID."
                ),
            )
            return
        workspace_ids = [str(resolved)]

    base_url = get_base_url()

    user_ids = [] if show_all else _try_get_current_user_ids(session, base_url=base_url)

    all_items: list[dict] = []
    for ws_id in workspace_ids:
        status_filter = [s.upper() for s in status] if status else []
        try:
            items = _list_notebooks_for_workspace(
                session,
                base_url=base_url,
                workspace_id=ws_id,
                user_ids=user_ids,
                keyword=keyword,
                page_size=limit,
                status=status_filter,
            )
            all_items.extend(items)
        except ValueError as e:
            if len(workspace_ids) == 1:
                _handle_error(
                    ctx,
                    "APIError",
                    str(e),
                    EXIT_API_ERROR,
                    hint="Check auth and proxy configuration.",
                )
                return
            if not ctx.json_output:
                click.echo(f"Warning: workspace {ws_id} failed: {e}", err=True)
            continue
        except Exception as e:
            if len(workspace_ids) == 1:
                _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
                return
            if not ctx.json_output:
                click.echo(f"Warning: workspace {ws_id} failed: {e}", err=True)
            continue

    if not all_items and len(workspace_ids) > 1:
        _handle_error(
            ctx,
            "APIError",
            "Failed to list notebooks from configured workspaces.",
            EXIT_API_ERROR,
        )
        return

    all_items = _sort_notebook_items(all_items)
    _print_notebook_list(all_items, json_output)


@click.command("ssh")
@click.argument("notebook")
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Wait for notebook to reach RUNNING status",
)
@click.option(
    "--pubkey",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help=(
        "SSH public key path to authorize (defaults to ~/.ssh/id_ed25519.pub or ~/.ssh/id_rsa.pub)"
    ),
)
@click.option(
    "--save-as",
    help=(
        "Save this notebook tunnel as a named profile (usable with 'ssh <name>' after "
        "'inspire tunnel ssh-config --install')"
    ),
)
@click.option(
    "--port",
    default=31337,
    show_default=True,
    help="rtunnel server listen port inside notebook",
)
@click.option(
    "--ssh-port",
    default=22222,
    show_default=True,
    help="sshd port inside notebook",
)
@click.option(
    "--command",
    help="Optional remote command to run (if omitted, opens an interactive shell)",
)
@click.option(
    "--rtunnel-bin",
    help="Path to pre-cached rtunnel binary (e.g., /inspire/.../rtunnel)",
)
@click.option(
    "--debug-playwright",
    is_flag=True,
    help="Run browser automation with visible window for debugging",
)
@click.option(
    "--timeout",
    "setup_timeout",
    default=300,
    show_default=True,
    help="Timeout in seconds for rtunnel setup to complete",
)
@pass_context
def ssh_notebook_cmd(
    ctx: Context,
    notebook: str,
    wait: bool,
    pubkey: Optional[str],
    save_as: Optional[str],
    port: int,
    ssh_port: int,
    command: Optional[str],
    rtunnel_bin: Optional[str],
    debug_playwright: bool,
    setup_timeout: int,
) -> None:
    """SSH into a running notebook instance via rtunnel ProxyCommand."""
    run_notebook_ssh(
        ctx,
        notebook_id=notebook,
        wait=wait,
        pubkey=pubkey,
        save_as=save_as,
        port=port,
        ssh_port=ssh_port,
        command=command,
        rtunnel_bin=rtunnel_bin,
        debug_playwright=debug_playwright,
        setup_timeout=setup_timeout,
    )


@click.command("terminal")
@click.argument("notebook")
@click.option(
    "--tmux",
    "-t",
    "tmux_session",
    default=None,
    help="Auto-create or attach to a tmux session with this name",
)
@pass_context
def terminal_notebook_cmd(
    ctx: Context,
    notebook: str,
    tmux_session: Optional[str],
) -> None:
    """Open an interactive terminal to a running notebook.

    Connects directly via Jupyter terminal WebSocket — no SSH or rtunnel
    required. Works on all notebook types (CPU, 4090, H100, H200).

    \b
    Examples:
        inspire notebook terminal dev-4090
        inspire notebook terminal dev-h100 --tmux train
        inspire notebook terminal abc123-def456

    \b
    Disconnect with Ctrl+] (like telnet).
    """
    run_notebook_terminal(
        ctx,
        notebook_id=notebook,
        tmux_session=tmux_session,
    )


@click.command("exec")
@click.argument("notebook")
@click.argument("command")
@click.option(
    "--timeout",
    "-T",
    default=120,
    show_default=True,
    help="Timeout in seconds for command completion",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@click.option(
    "--session",
    "-s",
    "use_session",
    is_flag=True,
    help="Use persistent session (keeps browser open for fast repeated commands)",
)
@click.option(
    "--cwd",
    default=None,
    help="Working directory on the notebook (prepends 'cd ... &&')",
)
@click.option(
    "--env",
    "env_vars",
    multiple=True,
    help="Environment variables (repeatable, KEY=VAL). Prepends 'export ...'",
)
@pass_context
def exec_notebook_cmd(
    ctx: Context,
    notebook: str,
    command: str,
    timeout: int,
    json_output: bool,
    use_session: bool,
    cwd: str | None,
    env_vars: tuple[str, ...],
) -> None:
    """Execute a command on a notebook and capture output.

    Connects via Jupyter terminal WebSocket — no SSH or rtunnel required.
    Output is streamed in real time. Exit code is propagated.

    Use --session to keep the connection alive between calls. The first
    call starts a background daemon; subsequent calls reuse it for
    near-instant command execution. The daemon auto-exits after 15 min idle.

    \b
    Examples:
        inspire notebook exec dev-h200 "nvidia-smi"
        inspire notebook exec dev-h200 "python train.py" --timeout 3600
        inspire notebook exec dev-h200 "ls -la" --json
        inspire notebook exec dev-h200 "nvidia-smi" --session
    """
    run_notebook_exec(
        ctx,
        notebook_id=notebook,
        command=command,
        timeout=timeout,
        json_output=json_output,
        use_session=use_session,
        cwd=cwd,
        env_vars=env_vars,
    )


__all__ = [
    "create_notebook_cmd",
    "exec_notebook_cmd",
    "list_notebooks",
    "notebook_status",
    "ssh_notebook_cmd",
    "start_notebook_cmd",
    "stop_notebook_cmd",
    "terminal_notebook_cmd",
]
