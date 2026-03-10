# Realtime GPU Terminal Workflow for Inspire CLI

This document describes the new recommended workflow for getting an SSH-like realtime terminal on Inspire GPU notebooks from the CPU machine, with live output and interactive debugging.

## Executive summary

Use:

```bash
inspire notebook terminal <notebook> --tmux train
```

Do **not** rely on:

```bash
inspire notebook ssh ...
```

on this platform for daily work, because the rtunnel/SSH path proved unreliable.

The new `inspire notebook terminal` command talks directly to the notebook's **Jupyter terminal WebSocket**, which gives you:

- realtime terminal output
- interactive shell input
- tmux persistence
- pdb/ipdb/breakpoint() debugging
- support across 4090 and H100 notebooks

---

## What was implemented

### 1. New command

```bash
inspire notebook terminal <notebook> [--tmux SESSION]
```

Examples:

```bash
inspire notebook terminal dev-4090
inspire notebook terminal dev-h100 --tmux train
inspire notebook terminal a2a81019-b479-4833-9fda-f68124841fd2 --tmux debug
```

Behavior:
- opens the notebook's authenticated Jupyter terminal via browser automation
- creates a terminal via Jupyter REST API
- connects to the terminal's WebSocket
- proxies your local keyboard input to the remote shell
- streams remote stdout/stderr back to your terminal
- if `--tmux` is provided, auto-attaches or creates that tmux session

Disconnect with:

```text
Ctrl+]
```

---

### 2. New implementation modules

Added:

- `inspire/bridge/jupyter_terminal.py`
- `inspire/cli/commands/notebook/notebook_terminal_flow.py`

Updated:

- `inspire/cli/commands/notebook/notebook_commands.py`
- `inspire/cli/commands/notebook/__init__.py`
- image lookup flow to search personal-visible images for notebook creation
- notebook browser image listing to support `SOURCE_PERSONAL_VISIBLE`

---

## Why this is better than SSH here

We tested `inspire notebook ssh` on both 4090 and H100.

### Result
It failed at rtunnel proxy readiness with errors like:

```text
500 connect ECONNREFUSED 0.0.0.0:31337
```

So although SSH was conceptually appealing, it was not reliable enough for your actual workflow.

The WebSocket terminal route is simpler:

- no rtunnel
- no SSH daemon requirement
- no proxying a local TCP port
- no dependence on notebook-side SSH bootstrap

Instead it uses the notebook's existing Jupyter terminal support directly.

---

## What was verified successfully

### 4090 verification
Notebook created with:

```bash
inspire notebook create -r 1x4090 -i base-wjx:v3 -n test-4090 --wait
```

Verified via terminal WebSocket:

```bash
echo HELLO_FROM_GPU && nvidia-smi --query-gpu=name --format=csv,noheader
```

Output included:

```text
HELLO_FROM_GPU
NVIDIA GeForce RTX 4090
```

### H100 verification
Notebook created with:

```bash
inspire notebook create -r 1xH100 -i base-wjx:v3 -n test-h100 --wait
```

Verified via terminal WebSocket:

```bash
echo HELLO_H100 && nvidia-smi --query-gpu=name --format=csv,noheader
```

Output included:

```text
HELLO_H100
NVIDIA H100 80GB HBM3
```

This proves the terminal-WebSocket approach works on both notebook types.

---

## Recommended daily workflow

## A. Create or reuse a notebook

List running notebooks:

```bash
inspire notebook list -A -s RUNNING
```

Create 4090 notebook:

```bash
inspire notebook create -r 1x4090 -i base-wjx:v3 -n dev-4090 --wait
```

Create H100 notebook:

```bash
inspire notebook create -r 1xH100 -i base-wjx:v3 -n dev-h100 --wait
```

---

## B. Open a realtime terminal

```bash
inspire notebook terminal dev-4090 --tmux train
```

or

```bash
inspire notebook terminal dev-h100 --tmux train
```

What happens:
- opens notebook terminal
- attaches to tmux session `train`
- if that session does not exist, creates it

---

## C. Start training interactively

Inside the notebook terminal:

```bash
cd /inspire/hdd/global_user/wanjiaxin-253108030048/<your-code-dir>
python -u train.py
```

Use `-u` so logs are unbuffered and appear immediately.

If you use a venv:

```bash
. .venv/bin/activate
python -u train.py
```

---

## D. Debug interactively

Examples:

### pdb

```bash
python -m pdb train.py
```

### breakpoint()

Put in code:

```python
breakpoint()
```

Then run normally:

```bash
python -u train.py
```

### ipdb

If your image already has it:

```python
import ipdb; ipdb.set_trace()
```

---

## E. Disconnect and resume later

Disconnect from local side:

```text
Ctrl+]
```

Reconnect later:

```bash
inspire notebook terminal dev-h100 --tmux train
```

Because the work is in tmux, your process keeps running.

---

## Best-practice split: debug vs production

### For active debugging and iteration
Use notebook terminal:

```bash
inspire notebook terminal ... --tmux train
```

Best for:
- editing and re-running quickly
- interactive debugging
- watching output live
- experimenting

### For final long-running production training
Use:

```bash
inspire run "cd /path && python -u train.py" --sync --watch
```

Best for:
- longer unattended runs
- managed job logging
- cleaner submission workflow

Recommended pattern:
1. Debug in notebook terminal
2. Once stable, switch to `inspire run --sync --watch`

---

## Important notes

### 1. This command needs a real terminal
If you run it from a non-interactive environment, you may see:

```text
stdin is not a terminal
```

That is expected. Run it directly from your shell.

### 2. The custom image name
Your usable notebook image is:

```text
base-wjx:v3
```

and it is found from your personal-visible image list.

### 3. Why image lookup had to be fixed
Originally notebook creation only searched official/public images, so your personal image was invisible to `inspire notebook create --image ...`.
That was fixed so `base-wjx:v3` can now be selected in notebook creation.

---

## Troubleshooting

## Problem: `stdin is not a terminal`
Cause:
- command was launched in a non-interactive environment

Fix:
- run directly in your shell, not through a non-TTY wrapper

---

## Problem: notebook not running
Check:

```bash
inspire notebook status <notebook>
```

Start or recreate if needed.

---

## Problem: image not found
Use:

```bash
inspire image list --source personal-visible
```

Expected image:

```text
base-wjx:v3
```

Then create notebook with:

```bash
inspire notebook create -r 1xH100 -i base-wjx:v3 -n dev-h100 --wait
```

---

## Problem: training logs do not appear immediately
Use unbuffered Python:

```bash
python -u train.py
```

or set:

```bash
export PYTHONUNBUFFERED=1
```

---

## Problem: notebook terminal disconnects
Reconnect with the same command:

```bash
inspire notebook terminal <notebook> --tmux train
```

Your tmux session should still be there.

---

## Suggested SOP

### 4090 debug session

```bash
inspire notebook create -r 1x4090 -i base-wjx:v3 -n dev-4090 --wait
inspire notebook terminal dev-4090 --tmux train
```

Inside:

```bash
cd /inspire/hdd/global_user/wanjiaxin-253108030048/<repo>
python -u train.py
```

### H100 debug session

```bash
inspire notebook create -r 1xH100 -i base-wjx:v3 -n dev-h100 --wait
inspire notebook terminal dev-h100 --tmux train
```

Inside:

```bash
cd /inspire/hdd/global_user/wanjiaxin-253108030048/<repo>
python -u train.py
```

---

## Global skill

A global Claude skill was added at:

```text
/root/.claude/skills/gpu-terminal.md
```

This skill tells Claude to prefer the new terminal workflow over SSH for Inspire GPU notebook interaction.

---

## Final recommendation

For your platform, the right answer is:

- **interactive debugging / realtime training output** → `inspire notebook terminal --tmux ...`
- **formal long training after code stabilizes** → `inspire run --sync --watch ...`

This gives you the practical benefits you wanted from SSH, without depending on the unreliable rtunnel SSH path.
