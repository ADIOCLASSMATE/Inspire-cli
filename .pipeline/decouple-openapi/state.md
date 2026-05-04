# State — decouple-openapi

## Current Round
Round 1 / 3 — IN PROGRESS

## Implementation Order
| # | Phase | Scope | Status | Verification |
|---|-------|-------|--------|-------------|
| 1 | Move utility symbols | `platform/web/models.py`, `cli/utils/resource_parser.py`, `cli/utils/id_format.py` (new); `platform/web/resources.py`, `cli/commands/job/job_create.py`, `cli/utils/job_cli.py` (import updates) | completed | Import checks pass, no openapi residue |
| 2 | Fix run.py CRITICAL bug | `cli/commands/run.py` | completed | Import check, `run --help` works |
| 3 | Fix job_logs.py AuthManager calls | `cli/commands/job/job_logs.py` | completed | Import check, no AuthManager references |
| 4 | Fix config/check.py, job_commands.py, job_create.py | `cli/commands/config/check.py`, `cli/commands/job/job_commands.py`, `cli/commands/job/job_create.py` | completed | All imports work, AuthenticationError replaced |
| 5 | Delete auth.py and openapi package | `cli/utils/auth.py` (delete), `inspire/platform/openapi/` (delete), `cli/utils/__init__.py` (update) | completed | `grep -r "openapi" inspire/` clean, `import inspire` works |
| 6 | Delete old tests, update test_cli_commands.py | 5 test files (delete), `tests/test_cli_commands.py` (update) | completed | 382 tests pass |
| 7 | Code duplication fixes + silent error logging | `cli/commands/project/project_commands.py`, `cli/utils/job_submit.py`, `cli/utils/job_cli.py` | completed | 382 tests pass |
| 8 | End-to-end verification | All CLI commands from /tmp | completed | All commands functional |

## Dependencies
- Phase 1 must complete before Phase 2-5 (import paths change)
- Phase 5 must complete before Phase 6 (tests import from openapi)
- Phases 1-7 are independent of Phase 8
- All phases are now complete

## Round History
| Round | Result | Score | Resolved | Deferred |
|-------|--------|-------|----------|----------|
| (none yet) | | | | |
