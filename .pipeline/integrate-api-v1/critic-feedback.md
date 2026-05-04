# Critic Feedback & Lead Rebuttal

## Round 1

### Critic Feedback
(See above — 7 issues, verdict FAIL)

### Lead Rebuttal

| Issue | Verdict | Action |
|-------|---------|--------|
| R1-C1 | ACCEPT | Fixed: Added final drain fetch in `_follow_logs_via_api` before `break` on terminal status. Lines 508-531 of job_logs.py. |
| R1-C2 | ACCEPT | Fixed: Removed `lcg_id_map` and `list_compute_groups` call entirely. Now iterate `group_ws_map` directly (only groups with low_priority_gpus > 0). Removed unused `list_compute_groups` import. |
| R1-C3 | ACCEPT | Fixed: `_fetch_log_via_api` now paginates through all log entries with `start_timestamp_ms` advancement, max 50 pages. `_fetch_log_entries_via_api` (used by follow mode) keeps page_size=200 since follow polls incrementally. |
| R1-C4 | ACCEPT | Fixed: Removed `key in short` from `_full_resource_type`. Only `short in key` remains. |
| R1-C5 | ACCEPT | Fixed: `_log_entry_key` now uses `hash(content)` as fallback when `log-id.keyword` is missing, instead of `id(entry)` which is per-object. |
| R1-C6 | ACCEPT | Fixed: `node_gpu_map` now uses `(node_name, group_id)` compound key. `node_dim_map` replaced with `node_group_map` (first-assignment-wins for node→group mapping). |
| R1-C7 | DEFER | MEDIUM severity. The API-first path requires mocking `list_job_instances`/`fetch_job_logs` which need WebSession. The fallback path is covered. Will add API-first tests in a follow-up. |

#### Diff Summary
- `job_logs.py`: Added final drain fetch (R1-C1), pagination in `_fetch_log_via_api` (R1-C3), stable dedup key (R1-C5)
- `allocate.py`: Removed `lcg_id_map`/`list_compute_groups` (R1-C2), compound key for node_gpu_map (R1-C6), removed `key in short` (R1-C4)

### Critic Verdict
| Issue | Verdict | Note |
|-------|---------|------|
| R1-C1 | FIXED | Verified: `_follow_logs_via_api` lines 542-565 now fetches drain entries via `_fetch_log_entries_via_api` with `start_timestamp_ms=last_timestamp_ms` before `break` on terminal status. Deduplication against `seen` set is applied correctly. |
| R1-C2 | FIXED | Verified: Grep confirms `lcg_id_map` and `list_compute_groups` are absent from allocate.py. The code now iterates `group_ws_map` directly (only groups with `low_priority_gpus > 0`), which is the correct approach. |
| R1-C3 | FIXED | Verified: `_fetch_log_via_api` (lines 216-248) paginates with `start_timestamp_ms` advancement, `page_size=500`, `max_pages=50` safety limit. `_fetch_log_entries_via_api` keeps `page_size=200` since follow mode polls incrementally. Correct separation of concerns. |
| R1-C4 | FIXED | Verified: `_full_resource_type` (lines 554-575) now only has `short in key` on line 573. The incorrect `key in short` check has been removed. |
| R1-C5 | FIXED | Verified: `_log_entry_key` (lines 311-318) now uses `hash(content)` as fallback when `log-id.keyword` is missing, producing a stable key across polls. The previous `id(entry)` was per-object and would never deduplicate. |
| R1-C6 | FIXED | Verified: `node_gpu_map` at line 461 uses `dict[tuple[str, str], int]` with compound key `(node_name, group_id)`. `node_group_map` at line 462 replaces `node_dim_map` with first-assignment-wins semantics for node-to-group mapping. Lookup at line 490 uses the compound key. |
| R1-C7 | DEFERRED | Severity is MEDIUM (test gap, not a bug). The Lead's explanation is valid: API-first tests require mocking WebSession internals, and the fallback path is covered (verified: tests at lines 837-838 and 877-878 patch `_follow_logs_via_api` to throw, forcing fallback). Acceptable to defer to follow-up. |

**Round Verdict: PASS** | Weighted Score: 8.0/10

All six ACCEPT fixes verified in code. The one DEFER item is MEDIUM severity and the justification is reasonable. No new CRITICAL issues introduced by the fixes.
