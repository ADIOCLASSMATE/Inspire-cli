# Implementation Summary

## User Requirement
Add v2 API authentication (Keycloak Bearer token), GPU metrics monitoring (show + health assessment), inference service management, enhanced job listing/detail with diagnostics, and batch stop to the Inspire-cli Python CLI. Reference implementation: holos-inspire TypeScript plugin.

## Plan Reference
See `.pipeline/v2-api-metrics/plan.md` for the approved plan.

## Changes

| File | What Changed |
|------|-------------|
| `inspire/platform/web/v2_api/__init__.py` | **NEW** Package entry, re-exports public v2 symbols |
| `inspire/platform/web/v2_api/auth.py` | **NEW** Token acquisition via `POST /auth/token`, disk cache at `~/.cache/inspire-cli/v2_token.json`, `ensure_token()` with auto-refresh |
| `inspire/platform/web/v2_api/client.py` | **NEW** Generic v2 HTTP client: `post_v2(service, action, body, token, base_url)` with `V2ApiError` error handling for 401/403/ResponseMetadata.Error |
| `inspire/platform/web/v2_api/models.py` | **NEW** Shared frozen dataclasses: `V2JobInfo`, `V2InferenceInfo`, `MetricTimeSeries`, `MetricSummary`, `IdleWindow`, `StatusFamily` |
| `inspire/platform/web/v2_api/train.py` | **NEW** v2 Train API: `list_jobs`, `get_job_detail`, `stop_job`, `get_job_logs`, `get_task_metrics` |
| `inspire/platform/web/v2_api/workspace.py` | **NEW** v2 Workspace API: `get_basic_info`, `list_node_dimension`, `list_resource_specs` |
| `inspire/platform/web/v2_api/inference.py` | **NEW** v2 Inference API: `list_inference`, `get_inference_detail`, `create_inference`, `stop_inference` |
| `inspire/config/models.py` | Added `v2_enabled: bool = True` field to Config dataclass |
| `inspire/config/options/api.py` | Added `V2_OPTIONS` list with `INSPIRE_V2_ENABLED` env var mapping |
| `inspire/config/schema.py` | Registered `V2_OPTIONS` in `CONFIG_OPTIONS` aggregation |
| `inspire/platform/web/session/__init__.py` | Added `get_v2_token(config) -> str | None` helper for dual-auth strategy |
| `inspire/cli/utils/status_normalizer.py` | **NEW** `normalize_status()`, `analyze_timeline()`, `classify_job_id()`, `format_duration()` |
| `inspire/cli/utils/metrics_utils.py` | **NEW** `compute_summary()`, `detect_idle_windows()`, `compute_trend()`, `assess_health()` |
| `inspire/cli/commands/metrics/__init__.py` | **NEW** Click group `metrics` with `show` and `health` subcommands |
| `inspire/cli/commands/metrics/metrics_show.py` | **NEW** `metrics show <job-id>` — fetches 4 metric types via v2 API, displays statistical summary or raw time-series |
| `inspire/cli/commands/metrics/metrics_health.py` | **NEW** `metrics health <job-id>` — health assessment with 8 categories (正常/预热中/疑似卡住/GPU利用率低/CPU瓶颈/显存紧张/内存紧张/无数据) |
| `inspire/cli/commands/inference/__init__.py` | **NEW** Click group `inference` with `create`, `stop`, `list`, `detail` subcommands |
| `inspire/cli/commands/inference/inference_commands.py` | **NEW** Full CRUD for inference servings via v2 API |
| `inspire/cli/commands/job/__init__.py` | Added `detail` command import and registration |
| `inspire/cli/commands/job/job_commands.py` | Enhanced `list_jobs` with `--v2` flag (live v2 API listing with cache fallback). Added `detail` subcommand with timeline analysis and diagnostics. Enhanced `stop` with batch mode (`--workspace`, `--status`, `--dry-run`). |
| `inspire/cli/commands/__init__.py` | Added `metrics` and `inference` imports and exports |
| `inspire/cli/main.py` | Registered `metrics` and `inference` command groups |

## Design Decisions

1. **Dual-auth strategy**: `get_v2_token()` returns `None` on failure, callers fall back to v1 cookie auth. No breaking changes to existing v1 code paths.
2. **Platform /auth/token endpoint** (not direct Keycloak ROPC): Simpler — just username+password, no Keycloak client ID needed. Confirmed working in `test_all_endpoints.py`.
3. **Separate `v2_api/` package**: All v2 code isolated from `browser_api/` v1 code. No cross-contamination.
4. **Frozen dataclasses**: All data models use `@dataclass(frozen=True)` following Python immutability rules.
5. **Status normalizer handles both v1 and v2**: v2 numeric codes (1-8) and v1 snake_case strings mapped to 6 families.
6. **Health assessment ported from holos-inspire**: Same 8 categories with Chinese labels, idle window detection at 5% threshold.
7. **Batch stop uses v2 API exclusively**: v2 `ListJobs` + `StopJob` provides real-time data that v1 cache can't match.

## Known Tradeoffs

- **No refresh token support yet**: `/auth/token` returns `access_token` but may not return `refresh_token`. Token cache TTL is 1 hour, after which full re-login occurs. This is acceptable for CLI usage (short-lived sessions).
- **Metrics require v2 API**: No v1 fallback for metrics (v1 doesn't expose `GetTaskMetric`). Users without v2 access see a clear error message.
- **Inference create has limited resolution**: workspace/project/compute_group must be provided explicitly. Auto-resolution like `inspire run` could be added later.
- **`detail` subcommand may overlap with `status`**: `status` gives quick status; `detail` gives full diagnostics. Both are useful at different levels.
