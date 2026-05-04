# Implementation Summary

## User Requirement
Completely decouple and delete the OpenAPI module (`inspire/platform/openapi/`) from the Inspire CLI codebase. Fix all resulting issues including a CRITICAL runtime bug in `run.py`, AuthManager residue in `job_logs.py`, code duplication, and silent exception swallowing. All commands must work end-to-end after the cleanup.

## Plan Reference
See `.pipeline/decouple-openapi/plan.md` for the full approved plan.
Key phases: Move utilities → Fix broken code → Delete AuthManager/openapi → Fix tests → Fix duplications → E2E verify.

## Changes

| File | What Changed |
|------|-------------|
| `inspire/platform/web/models.py` | **NEW**: `GPUType` enum moved from `openapi/models.py` |
| `inspire/cli/utils/resource_parser.py` | **NEW**: `parse_resource_request()` moved from `openapi/resources.py` |
| `inspire/cli/utils/id_format.py` | **NEW**: `_validate_job_id_format()` moved from `openapi/errors.py` |
| `inspire/platform/web/resources.py:15` | Updated import: `from inspire.platform.openapi.models` → `from inspire.platform.web.models` |
| `inspire/cli/commands/job/job_create.py:19,25` | Replaced `AuthenticationError` import + `parse_resource_request` import path |
| `inspire/cli/commands/job/job_commands.py:26-31` | Added `_ACTIVE_EXCLUDE_STATUSES` const, replaced `AuthenticationError` with `SessionExpiredError`, fixed `except Exception: pass` → `logger.debug()` |
| `inspire/cli/commands/run.py:28,64-70,138,229` | Removed `AuthManager` import; fixed `find_best_compute_group_location` call (removed positional `api`, added `config_compute_groups`, unpack 4 values); fixed `submit_training_job` (removed `api`, added `gpu_type/gpu_count/compute_group_id`) |
| `inspire/cli/commands/job/job_logs.py:373-402,517-552,577-582,657` | Renamed `_resolve_notebook_for_job` → `_get_explicit_notebook_id`, removed dead `api.list_notebooks()` code; replaced `AuthManager.get_api()` + `api.get_job_detail()` with `browser_api_module.get_job_detail()` in `_follow_logs_via_api` and `_follow_logs_via_notebook` |
| `inspire/cli/commands/config/check.py:20,235-239` | Replaced `AuthManager.get_api(cfg)` with `WebSession.load()` cached session check |
| `inspire/cli/utils/__init__.py` | Removed `AuthManager` and `AuthenticationError` re-exports |
| `inspire/cli/utils/auth.py` | **DELETED** — entire AuthManager class |
| `inspire/platform/openapi/` | **DELETED** — 10 files (client, auth, endpoints, errors, http, jobs, models, nodes, resources, \_\_init\_\_) |
| `inspire/cli/utils/job_submit.py:417-423` | Removed `select_project_for_workspace` from `__all__` (no external callers); added `logger` and replaced 3 `except Exception: pass` with `logger.debug()` |
| `inspire/cli/utils/job_cli.py:21-25` | Extracted `_strip_job_prefix()` helper; replaced 2 duplicate uuid_part expressions |
| `inspire/cli/commands/project/project_commands.py:223-236` | Removed duplicate `_project_info_to_dict`; unified on `_project_to_dict` |
| `tests/test_openapi_resource_manager.py` | **DELETED** |
| `tests/test_openapi_client_config.py` | **DELETED** |
| `tests/test_openapi_jobs.py` | **DELETED** |
| `test_api_v1_endpoints.py` | **DELETED** (root) |
| `test_api_compatibility.py` | **DELETED** (root) |
| `tests/test_cli_commands.py` | Replaced `AuthManager.get_api` monkeypatching with `browser_api_module` + `get_web_session` mocking; replaced `AuthenticationError` with `SessionExpiredError`; updated `_resolve_notebook_for_job` → `_get_explicit_notebook_id`; patched `browser_api_module.get_job_detail/create_job/get_train_resource_prices` in test setup |

## Design Decisions

1. **GPUType moved to `platform/web/models.py`** rather than a shared `common.py`: Keeps web-layer domain models together. GPUType is only used by web-layer code.
2. **`config/check.py` uses `WebSession.load()` not `get_web_session()`**: `get_web_session()` opens a browser on cache miss — too intrusive for a config validation command. `WebSession.load()` is a fast disk-only check.
3. **`_get_explicit_notebook_id` replaces `_resolve_notebook_for_job`**: The old function's auto-discovery path (`api.list_notebooks()`) was dead code — `InspireAPI` never had a `list_notebooks` method. The function now just returns the explicitly-provided notebook ID or None.
4. **Test mocking switched from `AuthManager.get_api` to `browser_api_module.*` patching**: Since job CRUD calls now go through `browser_api_module.get_job_detail()` etc. directly (not through an API object), tests must monkeypatch the browser API functions.

## Known Tradeoffs

- **`config check` reports "Authentication failed" when web session expired**: Old token auth was always-valid if password was configured. New web session needs periodic browser re-login. This is inherent to the SSO cookie model — the error message guides users to `inspire init --discover`.
- **`run.py` no longer supports `--location` with location-to-compute_group mapping via old `ResourceManager`**: Location is resolved via `find_best_compute_group_location` using config compute_groups. If the location string doesn't match any config compute group name/location, it falls through.
- **`select_project_for_workspace` remains in code but not in `__all__`**: Not deleted because it's a valid single-workspace variant, but no external callers exist.
