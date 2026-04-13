# Changelog

## Unreleased

### Breaking Changes

- Removed `inspire bridge` command group and all bridge execution code (`inspire/bridge/` package).
- Removed `inspire tunnel` command group and all tunnel/rtunnel code.
- Removed `inspire notebook ssh` and `inspire notebook top` commands.
- Removed `inspire sync` command (and its stub).
- Removed `--sync`, `--no-sync`, and `--watch` flags from `inspire run`.
- Removed SSH key generation and probe-related CLI options from `inspire init` (`--probe-shared-path`, `--probe-limit`, `--probe-keep-notebooks`, `--probe-pubkey`, `--probe-timeout`).
- Removed all forge/Git config fields: `git_platform`, `gitea_repo`, `gitea_token`, `gitea_server`, `gitea_log_workflow`, `gitea_sync_workflow`, `github_repo`, `github_token`, `github_server`, `github_log_workflow`, `github_sync_workflow`, `default_remote`, `remote_timeout`.
- Removed `options/forge.py` module and "Gitea", "GitHub", "Git Platform", "Sync" schema categories.
- Removed bridge-related config fields: `bridge_action_timeout`, `bridge_action_denylist`, `gitea_bridge_workflow`, `github_bridge_workflow`.
- Removed `config_from_env_for_sync` API and `INSPIRE_BRIDGE_ACTION_TIMEOUT` / `INSPIRE_BRIDGE_DENYLIST` env var support.
- Removed `_parse_denylist` and `_parse_remote_timeout` helpers.
- Removed `rtunnel_download_url` from host validation in `inspire config check`.
- Removed `RTUNNEL_BIN` from profile env map.
- Fixed `Config.from_env()` usage in auth, job_cli, and job_logs — now uses `from_files_and_env()` so TOML config is respected.
- Removed `Config.from_env()` method and `load_env.py` module (dead code; all production code uses `from_files_and_env()`).
- Removed `_parse_list` helper and `validator` field from `ConfigOption` (never used by any option).
- Removed `display_available_resources` function from `resources.py` (replaced by `inspire resources list` command).
- Removed `inspire/cli/utils/output.py` module (dead code — `format_warning`, `print_error` were unused).
- Removed stale example files: `examples/setup_ssh_dropbear.sh`, `examples/workflows/run_bridge_action.yml`.
- Removed stale evaluation reports: `INSPIRE_CLI_E2E_EVALUATION_REPORT.md`, `INSPIRE_CLAUDE_SKILL_SCENARIO_TEST_REPORT.md`.
- Fixed missing `prefer_source` and `project_order` defaults in `_default_config_values()`.
- Unified GPU type handling — removed duplicate `GPUType` enum from `web/resources.py`, now imports from `openapi/models`.

### Features

- Added `inspire resources allocate` command — shows GPU availability and project budget overview for a resource request (`--gpus`, `--type`). Displays per-group free/preemptible/queued status and per-project budget. Read-only tool — does not create jobs. Always check this before submitting jobs with `inspire job create --location`.
- `inspire image list` now defaults to `--source personal-visible` (your own images) instead of `official`.
- Removed `--source private` from `inspire image list` — `private` was a confusing superset that overlapped with both `personal-visible` and `public`. Use `personal-visible` for your own images, `public` for community images, or `all` to see everything.
- `inspire job create` and `inspire run` now accept short image names (e.g. `dev-wjx:v-base`, `pytorch:25.06-py3`) for `--image`. The tool auto-resolves short names to the full URL and correct `image_type` by searching across image sources. Full URLs still work as before.

### Migration Guide

- For remote command execution, use `inspire notebook exec <notebook> "<cmd>"` or `inspire notebook terminal <notebook>`.
- For persistent sessions, use `inspire notebook exec-session`.
- For job submission, use `inspire job create` with `--location` (check `inspire resources allocate` first).
- Remove any `[bridge]`, `[ssh]`, `[tunnel]`, `[git]`, `[gitea]`, `[github]`, or `[sync]` sections from your `config.toml`.
- Remove `bridge_workflow` settings from `[gitea]` and `[github]` sections.
- The `--sync` and `--watch` flags on `inspire run` no longer exist — just use `inspire run "command"`.

## v0.2.4 (2025-01-01)

### Features

- Job management commands (create, status, logs, list, stop, wait)
- Notebook management commands (list, create, start, stop, ssh)
- Resource availability listing (GPUs, nodes)
- Quick job submission with auto-resource selection (`run`)
- Code sync to Bridge runner (`sync`)
- Bridge remote execution (`bridge exec`, `bridge ssh`)
- SSH tunnel management (add, remove, status, list, ssh-config)
- Configuration management (show, check, env) with TOML + env var loading
- Project initialization with environment detection
- Dual execution paths: SSH tunnel (fast) and Gitea/GitHub Actions (fallback)
- Human-readable and JSON output formatting
- Remote environment variable injection via `[remote_env]` config
