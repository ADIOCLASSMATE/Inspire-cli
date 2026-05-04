# Inspire CLI

Command-line interface for the Inspire HPC training platform. Designed for both human and agent (Claude Code) use — all commands support `--json` for machine-readable output.

## Installation

```bash
# Via SSH (recommended)
uv tool install git+ssh://git@github.com/ADIOCLASSMATE/Inspire-cli.git

# Or via HTTPS
uv tool install git+https://github.com/ADIOCLASSMATE/Inspire-cli.git
```

Upgrade later with:

```bash
uv tool upgrade inspire
```

### Local Development

```bash
uv tool install -e .
inspire --help
```

## Quick Start

### 1. Auto-discover your platform

```bash
inspire init --discover -u YOUR_USERNAME --base-url https://your-platform.com
```

This opens a browser to log in, then automatically discovers your projects, workspaces, compute groups, and shared filesystem paths. Writes both global (`~/.config/inspire/config.toml`) and project (`.inspire/config.toml`) configs.

Set your password as an env var to avoid repeated prompts:
```bash
export INSPIRE_PASSWORD="your_password"
```

### 2. Verify

```bash
inspire config show    # Check all values resolved
inspire config check   # Validate API auth
```

### 3. Start using

```bash
inspire resources list          # View GPU availability
inspire notebook create --name dev --resource 4xCPU --wait
inspire notebook terminal <id>  # Direct terminal (recommended)
inspire notebook exec <id> "<cmd>"  # Run a command non-interactively
```

## Architecture

The CLI uses two API surfaces, both via the `/api/v1` prefix with web session (cookie-based) authentication:

- **Browser API** (`/api/v1/*`): All commands use this. Authenticated via Playwright SSO browser login.
- **Notebook WebSocket**: Interactive terminal (`notebook terminal`) and command execution (`notebook exec`) connect directly to Jupyter terminal WebSocket.

## Command Reference

### Job Management (`inspire job`)

| Command | Description |
|---------|-------------|
| `inspire job create -n NAME -r RESOURCE -c COMMAND` | Submit a training job |
| `inspire job list [-n LIMIT] [-s STATUS] [--active] [--watch]` | List recent jobs from local cache |
| `inspire job status <job-id>` | Check job status |
| `inspire job logs <job-id> [--tail N] [--follow]` | View training logs |
| `inspire job logs [--status RUNNING]` | Bulk fetch logs for cached jobs |
| `inspire job stop <job-id>` | Stop a running job |
| `inspire job wait <job-id> [--timeout S] [--interval S]` | Wait for job completion |
| `inspire job update [-s STATUS] [-n LIMIT]` | Refresh cached job statuses from API |
| `inspire job command <job-id>` | Show the training command used for a job |

Key options for `job create`:
- `--name`, `-n`: Job name (required)
- `--resource`, `-r`: Resource spec like `4xH200`, `8xH100` (required)
- `--command`, `-c`: Start command (required)
- `--framework`: Training framework (default: pytorch)
- `--priority`: Task priority 1-10
- `--max-time`: Max runtime in hours (default: 100)
- `--location`: Preferred datacenter location
- `--workspace`: Workspace name (from `[workspaces]`)
- `--project`, `-p`: Project name or ID
- `--image`: Docker image URL or short name
- `--nodes`: Number of nodes for multi-node training (default: 1)
- `--auto/--no-auto`: Auto-select best location (default: auto)
- `--log-file`: Custom remote log file path

### Quick Run (`inspire run`)

| Command | Description |
|---------|-------------|
| `inspire run "<cmd>" [--gpus N] [--type H100\|H200]` | Quick job submission with auto-selected resources |

A simplified wrapper around `job create` that auto-generates a job name and auto-selects the best compute group.

### Resource Management (`inspire resources`)

| Command | Description |
|---------|-------------|
| `inspire resources list` | GPU availability (accurate real-time by default) |
| `inspire resources list --workspace` | Per-node workspace-scoped availability |
| `inspire resources list --watch` | Continuously watch availability |
| `inspire resources nodes [--group NAME]` | Free 8-GPU nodes per compute group |
| `inspire resources allocate [--gpus N] [--type H100\|H200]` | GPU availability + project budget overview |

### Notebook Management (`inspire notebook`)

| Command | Description |
|---------|-------------|
| `inspire notebook list [-n LIMIT] [-s STATUS] [--all]` | List notebook instances |
| `inspire notebook reusable -r RESOURCE` | Find reusable idle running notebooks |
| `inspire notebook status <id>` | Get notebook status |
| `inspire notebook create [-r RESOURCE] [-n NAME]` | Create a new notebook instance |
| `inspire notebook start <id> [--wait]` | Start a stopped notebook |
| `inspire notebook stop <id>` | Stop a running notebook |
| `inspire notebook terminal <id> [--tmux SESSION]` | Open interactive terminal via WebSocket |
| `inspire notebook exec <id> "<cmd>" [--timeout S] [--session]` | Execute a command on a notebook |
| `inspire notebook exec-session start <id> [--cwd DIR] [--env KEY=VAL]` | Start persistent exec session |
| `inspire notebook exec-session stop <id>` | Stop persistent exec session |
| `inspire notebook exec-session list` | List local exec sessions |

### Image Management (`inspire image`)

| Command | Description |
|---------|-------------|
| `inspire image list [--source official\|public\|personal-visible\|all]` | List available Docker images |
| `inspire image detail <image-id>` | Show image details |
| `inspire image register -n NAME -v VERSION` | Register an external Docker image |
| `inspire image save <notebook-id> -n NAME` | Save a running notebook as an image |
| `inspire image delete <image-id> [--force]` | Delete a custom image |
| `inspire image set-default --job NAME --notebook NAME` | Set default images in project config |

### Project Management (`inspire project`)

| Command | Description |
|---------|-------------|
| `inspire project list [--all-workspaces]` | List projects and GPU quota/budget |

### Configuration (`inspire config`)

| Command | Description |
|---------|-------------|
| `inspire config show` | Display merged configuration with sources |
| `inspire config check` | Validate API auth and configuration |
| `inspire config env [--template full\|minimal]` | Generate .env template file |

### Setup (`inspire init`)

| Command | Description |
|---------|-------------|
| `inspire init` | Generate starter config from env vars |
| `inspire init --discover` | Auto-discover projects, workspaces, compute groups |
| `inspire init --template` | Create template with placeholders |
| `inspire init --force` | Overwrite existing files |

## Typical Agent Workflow

When an agent (Claude Code) needs to submit a training job:

1. **Check availability** — `inspire resources allocate --gpus 8 --type H200`
2. **Submit with chosen location** — `inspire job create -n NAME -r 8xH200 -c "cmd" --location "H200-1号机房" --project PROJ --priority N`
3. **Wait for completion** — `inspire job wait JOB_ID`
4. **Fetch logs** — `inspire job logs JOB_ID --tail 50` or `inspire job logs JOB_ID --follow`

Do NOT rely on `inspire run` for auto-location selection — its choice lacks strong evidence since per-node task priority data is unavailable. Always check `inspire resources allocate` first and specify `--location` explicitly.

## Examples

```bash
# Open a realtime notebook terminal (recommended for debugging)
inspire notebook terminal test-h100 --tmux train

# Submit a training job (check availability first!)
inspire resources allocate --gpus 8 --type H200
inspire job create --name "train-v1" --resource "8xH200" --command "bash train.sh" --location "H200-1号机房"

# Quick run with auto-selected resources
inspire run "python train.py --epochs 100"

# Execute a command on a notebook
inspire notebook exec dev-h200 "nvidia-smi"
inspire notebook exec dev-h200 "python train.py" --timeout 3600

# Check GPU availability and project budget
inspire resources allocate --gpus 8 --type H200
inspire resources list
inspire project list

# View job logs
inspire job logs <job-id> --tail 100
inspire job logs <job-id> --follow
```

## Notebook access modes

### `inspire notebook terminal` (interactive)

Use `inspire notebook terminal <notebook> [--tmux SESSION]` for realtime interactive work.
It connects directly to the notebook's Jupyter terminal WebSocket:

- live terminal output
- interactive debugging with `pdb`, `ipdb`, and `breakpoint()`
- quick iteration from the CPU machine
- reconnecting to a persistent tmux session

```bash
inspire notebook terminal dev-4090
inspire notebook terminal test-h100 --tmux train
```

Disconnect with `Ctrl+]`.

### `inspire notebook exec` (non-interactive)

Use `inspire notebook exec <notebook> "<command>"` to run a single command and capture output:

- scriptable, exit code propagated
- `--json` for machine-readable output
- `--session` for persistent sessions (keeps browser open for fast repeated commands)

```bash
inspire notebook exec dev-h200 "nvidia-smi"
inspire notebook exec dev-h200 "ls -la" --json
inspire notebook exec dev-h200 "python train.py" --session
```

Tip (shell quoting): remember your **local shell expands `$VAR`** first. Use `\$VAR` (or single quotes) so the *remote* shell prints it.

## Configuration

The recommended way to configure is `inspire init --discover`, which auto-detects projects, workspaces, compute groups, and writes config files.

Config files are loaded in order (later overrides earlier):
1. Global: `~/.config/inspire/config.toml`
2. Project: `./.inspire/config.toml`
3. Environment variables

Account password lookup follows the same layered model:
1. `[accounts."<username>"].password` from global config
2. `[accounts."<username>"].password` from project config (overrides global for same username)
3. `INSPIRE_PASSWORD` (fallback only if no account password was found)

Legacy `[auth].password` is still supported, but account passwords take precedence when both are present.

Run `inspire init --discover` to auto-configure, or `inspire config show` to inspect the merged result.

Example `config.toml`:

```toml
[auth]
username = "your_username"

[accounts."your_username"]
# Optional: supports multi-account setups in global and/or project config
password = "your_password"

[api]
base_url = "https://your-inspire-platform.com"

[workspaces]
# cpu = "ws-..."       # Default workspace (CPU jobs / notebooks)
# gpu = "ws-..."       # GPU workspace (H100/H200 jobs)
# internet = "ws-..."  # Internet-enabled GPU workspace (e.g. RTX 4090)
# special = "ws-..."   # Custom alias (use with --workspace special)

[[compute_groups]]
name = "H100 Cluster"
id = "lcg-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
gpu_type = "H100"
```

### Multi-Workspace Project Selection

Project selection automatically searches **all workspaces** you have access to — not just the primary GPU workspace. If a project lives in a different workspace (e.g., a public research project space), it will appear in auto-selection candidates for `inspire job create`, `inspire run`, and `inspire resources allocate`.

When a cross-workspace project is selected, the job is submitted with that project's `workspace_id` so billing and quota are tracked correctly. The compute group (physical GPU location) is still determined separately.

```
# Example: a project in a public workspace appears alongside your GPU workspace projects
inspire project list
#   Name                      Priority   Budget remain
#   ----------------------------------------------------
#   个人项目                   NORMAL     800
#   公共科研项目               NORMAL     552

# The public project is available for auto-selection when submitting H100 jobs
inspire job create -n train -r 8xH100 -c "bash train.sh"
# → Using project: 公共科研项目 (cross-workspace)
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `INSPIRE_USERNAME` | Platform username |
| `INSPIRE_PASSWORD` | Platform password |
| `INSPIRE_BASE_URL` | API base URL |
| `INSPIRE_TARGET_DIR` | Shared filesystem path for training logs |
| `INSPIRE_WORKSPACE_ID` | Default workspace ID |
| `INSPIRE_WORKSPACE_CPU_ID` | CPU workspace ID (default workspace) |
| `INSPIRE_WORKSPACE_GPU_ID` | GPU workspace ID (H100/H200) |
| `INSPIRE_WORKSPACE_INTERNET_ID` | Internet-enabled workspace ID (e.g. RTX 4090) |
| `INSPIRE_PROJECT_ID` | Default project ID |
| `INSP_IMAGE` | Default Docker image |
| `INSP_PRIORITY` | Job priority (1-10) |
| `INSPIRE_SHM_SIZE` | Shared memory size in GB for jobs/notebooks |
| `INSPIRE_DOCKER_REGISTRY` | Docker registry URL |
| `INSPIRE_JOB_CACHE` | Override path for job cache file |
