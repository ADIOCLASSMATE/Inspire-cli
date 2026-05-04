# Implementation Summary: Integrate /api/v1 Endpoints for Logs & Preemptibility

## What was done

Integrated newly discovered `/api/v1` endpoints into the Inspire CLI, replacing two suboptimal approaches:

1. **Job Logs**: Replaced notebook-exec subprocess (180s timeout, required running notebook) with direct `/api/v1/logs/train` API calls.
2. **Allocate Preemptibility**: Replaced aggregate `low_priority_gpus` number with per-node preemptibility analysis using `/api/v1/cluster_metric/list_task_dimension` + `list_node_dimension`.

## Files created

- `inspire/platform/web/browser_api/availability/metrics.py` — New module for `cluster_metric/*` endpoints with `TaskDimension`, `NodeDimension`, `ClusterBasicInfo` data models and `list_task_dimension()`, `list_node_dimension()`, `get_cluster_basic_info()` functions.

## Files modified

- `inspire/platform/web/browser_api/jobs.py` — Added `InstanceInfo` dataclass, `list_job_instances()` (POST /train_job/instance_list), `fetch_job_logs()` (POST /logs/train with filter.podNames structure).
- `inspire/cli/commands/job/job_logs.py` — Complete rewrite: primary log fetch via direct API (`_fetch_log_via_api`, `_fetch_log_entries_via_api`), notebook-exec as fallback. New `_follow_logs_via_api` with 3s polling, exponential backoff to 15s, (timestamp, log_id) dedup, window advancement via start_timestamp_ms. Retained `_follow_logs_via_notebook` as fallback.
- `inspire/platform/web/browser_api/availability/allocate.py` — Added `PreemptibleTask`, `PreemptibleNode`, `GroupPreemptibility` models with `LOW_PRIORITY_THRESHOLD=3`. Extended `AllocateResult` with `preemptibility: dict[str, GroupPreemptibility]`. Added `_compute_preemptibility()` that cross-references task_dimension (priority per task) with node_dimension (task assignment per node). Added `_full_resource_type()` mapping (H200 → NVIDIA_H200_SXM_141G).
- `inspire/cli/commands/resources/allocate_cmd.py` — Updated `_format_human` to show per-node preemptibility detail under PREEMPTIBLE groups. Updated `_format_json` with `preemptibility` data via `_preemptibility_to_dict()`.
- `inspire/platform/web/browser_api/availability/__init__.py` — Added metrics module re-exports.
- `inspire/platform/web/browser_api/__init__.py` — Added all new public names.
- `tests/test_cli_commands.py` — Fixed 2 tests that patched `_follow_logs_via_notebook` to also patch `_follow_logs_via_api` (which now runs first).

## Key design decisions

- **Logs API two-step**: Must call `list_job_instances()` first to get pod names, then `fetch_job_logs(pod_names=...)` — the API doesn't accept job_id directly.
- **Fallback strategy**: Every code path tries API first, falls back to notebook-exec if API fails. No breaking changes for existing users.
- **Follow-logs polling**: 3s base interval (fast: API is lightweight), exponential backoff to 15s on empty results, deduplication via `(time, log-id.keyword)` seen-set, window advancement via `start_timestamp_ms`.
- **Preemptibility threshold**: `priority <= 3` is low priority (configurable via `LOW_PRIORITY_THRESHOLD`).
- **Resource type mapping**: Display names (H200) → full API names (NVIDIA_H200_SXM_141G) via `_full_resource_type()` dict.

## Test results

All 398 tests pass. Ruff lint clean.
