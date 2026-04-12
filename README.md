# Inspire CLI

Command-line interface for the Inspire HPC training platform.

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

## Commands

| Command | Description |
|---------|-------------|
| `inspire job create` | Submit a training job |
| `inspire job status/logs/list` | Monitor and manage jobs |
| `inspire job stop/wait` | Stop or wait for a job |
| `inspire run "<cmd>"` | Quick job with auto resource selection |
| `inspire notebook list/create` | List or create notebook instances |
| `inspire notebook start/stop` | Start or stop a notebook |
| `inspire notebook terminal <id>` | Open an interactive terminal via Jupyter WebSocket |
| `inspire notebook exec <id> "<cmd>"` | Execute a command on a notebook |
| `inspire notebook exec-session` | Manage persistent exec sessions |
| `inspire image list/detail` | Browse Docker images |
| `inspire image save/register` | Save or register custom images |
| `inspire project list` | View projects and GPU quota |
| `inspire resources list/nodes` | View GPU availability |
| `inspire config show/check` | Inspect and validate configuration |
| `inspire init` | Generate starter config from env vars |
| `inspire init --discover` | Auto-discover projects, workspaces, compute groups |

## Examples

```bash
# Open a realtime notebook terminal (recommended for debugging)
inspire notebook terminal test-h100 --tmux train

# Submit a training job
inspire job create --name "train-v1" --resource "4xH200" --command "bash train.sh"

# Quick run with auto-selected resources
inspire run "python train.py --epochs 100"

# Execute a command on a notebook
inspire notebook exec dev-h200 "nvidia-smi"
inspire notebook exec dev-h200 "python train.py" --timeout 3600

# Check GPU availability and project quota
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

View current config:
```bash
inspire config show
inspire config show --json
inspire config check   # Validate config + API auth
inspire --json config check
inspire config check --json
inspire init --json --template --project --force
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `INSPIRE_USERNAME` | Platform username |
| `INSPIRE_PASSWORD` | Platform password |
| `INSPIRE_BASE_URL` | API base URL |
| `INSPIRE_TARGET_DIR` | Shared filesystem path |
| `INSPIRE_WORKSPACE_ID` | Default workspace ID |
| `INSPIRE_WORKSPACE_CPU_ID` | CPU workspace ID (default workspace) |
| `INSPIRE_WORKSPACE_GPU_ID` | GPU workspace ID (H100/H200) |
| `INSPIRE_WORKSPACE_INTERNET_ID` | Internet-enabled workspace ID (e.g. RTX 4090) |
| `INSPIRE_PROJECT_ID` | Default project ID |
| `INSP_IMAGE` | Default Docker image |
| `INSP_PRIORITY` | Job priority (1-10) |
