# Local vs GitHub Difference Report

Repository compared against:
- Remote: `origin`
- Branch: `main`
- Upstream: `origin/main`
- Current local HEAD: `bc0ec8c`

## Summary

The local repository is **not ahead of GitHub by any commits yet**.

Current state:
- `HEAD` is still exactly at `origin/main`
- the differences are all in the **working tree** (modified/untracked files not yet committed)

So the practical difference is:
- **GitHub repo** = current published baseline
- **local repo** = GitHub baseline **plus the uncommitted notebook-terminal work below**

---

## Functional differences

### 1. New command: `inspire notebook terminal`

Local code adds a new notebook subcommand:

```bash
inspire notebook terminal <notebook> [--tmux SESSION]
```

What this adds compared with GitHub baseline:
- direct interactive terminal access to a running notebook
- uses the notebook's **Jupyter terminal WebSocket** instead of SSH/rtunnel
- supports realtime shell input/output
- supports optional `--tmux` auto-attach/create behavior
- disconnect shortcut is `Ctrl+]`

Files:
- `inspire/cli/commands/notebook/notebook_commands.py`
- `inspire/cli/commands/notebook/__init__.py`
- `inspire/cli/commands/notebook/notebook_terminal_flow.py`
- `inspire/bridge/jupyter_terminal.py`

### 2. New terminal bridge implementation

Local code adds a browser-driven terminal proxy that:
- opens an authenticated Jupyter terminal WebSocket through Playwright
- forwards local stdin to the notebook shell
- streams notebook stdout/stderr back to the local terminal
- sends terminal resize events

This is the core of the new SSH-like workflow.

File:
- `inspire/bridge/jupyter_terminal.py`

### 3. Notebook image lookup is expanded

Local code changes notebook creation so image lookup can find the user's personal-visible images.

Compared with GitHub baseline, local code now:
- searches `SOURCE_PERSONAL_VISIBLE` before public fallback
- accepts normalized image source aliases such as `personal-visible`
- supports selecting custom images like `base-wjx:v3` in notebook flows

Files:
- `inspire/cli/commands/notebook/notebook_create_flow.py`
- `inspire/platform/web/browser_api/notebooks.py`

### 4. Local documentation for the new workflow

Local repo includes documentation describing the new recommended GPU terminal workflow and why it is preferred over SSH on this platform.

File:
- `GPU_TERMINAL_WORKFLOW.md`

### 5. Local verification helper script

Local repo includes a helper script used to validate the Jupyter terminal WebSocket path manually.

File:
- `test_terminal_ws.py`

---

## Behavior change relative to GitHub baseline

### Before (GitHub baseline)
Primary notebook interactive path was effectively centered around:
- `inspire notebook ssh ...`
- rtunnel/SSH-based access patterns

### After (local working tree)
Preferred interactive notebook path becomes:

```bash
inspire notebook terminal <notebook> --tmux train
```

This means the local tree is optimized for:
- interactive debugging
- realtime terminal visibility
- persistent tmux sessions on the notebook side
- avoiding the unreliable rtunnel SSH path on this platform

---

## Important current caveats in the local working tree

### 1. cwd-dependent auth behavior still exists
The local tree currently still has the previously discussed limitation:
- `inspire notebook terminal ...` works reliably when run from the Inspire project directory
- it may fail from arbitrary directories if project config cannot be discovered

This was **not** fixed in the current tree.

### 2. `--tmux` requires tmux inside the notebook image
If the notebook image does not include `tmux`, then:

```bash
inspire notebook terminal <notebook> --tmux train
```

will connect successfully, but the remote shell may print:

```text
tmux: command not found
```

So `tmux` is an image/runtime dependency, not a CLI-only feature.

Because there is currently no active GPU worker, I did **not** run validation commands/tests in this session.

---

## File-level change inventory

### Modified tracked files
- `inspire/cli/commands/notebook/__init__.py`
- `inspire/cli/commands/notebook/notebook_commands.py`
- `inspire/cli/commands/notebook/notebook_create_flow.py`
- `inspire/platform/web/browser_api/notebooks.py`
- `tests/test_notebook_commands.py`

### New untracked files
- `GPU_TERMINAL_WORKFLOW.md`
- `inspire/bridge/jupyter_terminal.py`
- `inspire/cli/commands/notebook/notebook_terminal_flow.py`
- `test_terminal_ws.py`

---

## Net effect if synced to GitHub

If you commit and push the current local tree, your GitHub repository will gain:
- the new `inspire notebook terminal` command
- direct Jupyter-terminal-based interactive notebook access
- personal-visible image lookup for notebook creation
- workflow documentation for GPU terminal usage
- a manual verification helper script

That would make the repo much closer to an installable, plug-and-play CLI for your preferred notebook-terminal workflow.

---

## Suggested usage after syncing

After this is pushed, other projects can install your CLI from GitHub with `uv` and use the published version there.

What that solves:
- easier distribution of your customized Inspire CLI
- reuse across projects without copying the repo manually

What it does not solve by itself:
- cwd-sensitive project config discovery

So the best practical usage remains:
- install the CLI from your GitHub repo
- use it as your standard Inspire tool
- when necessary, run commands from a repo that has the appropriate `.inspire/config.toml`
