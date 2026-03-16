#!/usr/bin/env bash
set -euo pipefail

# gpu-notebook-interact.sh
#
# Non-session notebook interaction wrapper.
#
# Goal:
# - Run a command on a GPU notebook using NON-session mode:
#     inspire notebook exec <notebook_id> <cmd> --json
# - Provide a stable CLI/JSON contract for automation.
#
# Notes:
# - This script runs on the CPU machine (control plane). GPU notebooks are offline.
# - Remote command failure (data.exit_code != 0) is NOT treated as a CLI failure.

usage() {
  cat <<'EOF'
Usage:
  tools/gpu-notebook-interact.sh <notebook_id> <cmd> [--cwd <DIR>] [--env KEY=VAL ...] [--json]

Args:
  <notebook_id>       Notebook UUID id (required). Names are NOT accepted.
  <cmd>               Command string to run on the notebook.
                      IMPORTANT: pass as a single shell argument (quote it).

Options:
  --cwd <DIR>         Remote working directory (passed to inspire notebook exec).
  --env KEY=VAL       Repeatable env var (passed to inspire notebook exec).
  --json              Emit a single JSON object (recommended for automation).
  -h, --help          Show this help.

Exit codes:
  0   Ran command (inspire success==true). Remote exit_code may still be non-zero.
  2   Invalid arguments.
  3   CLI/transport failure (inspire rc!=0, or JSON success==false / unparsable).
EOF
}

NB_ID=""
CMD=""
CWD=""
JSON_OUT=0
ENV_KVS=()

# Parse positional args
if [[ $# -lt 2 ]]; then
  usage >&2
  exit 2
fi
NB_ID="$1"; shift
CMD="$1"; shift

# Require UUID-ish notebook id (names are not accepted for this script).
if ! [[ "$NB_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
  echo "Error: notebook_id must be a UUID (names are not accepted): $NB_ID" >&2
  exit 2
fi

# Parse options
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage; exit 0 ;;
    --cwd)
      CWD="${2:-}"; shift 2 ;;
    --env)
      kv="${2:-}"; shift 2
      if [[ -z "$kv" || "$kv" != *"="* ]]; then
        echo "Error: --env expects KEY=VAL" >&2
        exit 2
      fi
      ENV_KVS+=("$kv")
      ;;
    --json)
      JSON_OUT=1; shift ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      echo "Unexpected arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mktemp_safe() {
  mktemp 2>/dev/null || python -c 'import tempfile; print(tempfile.mkstemp()[1])'
}

read_file() {
  local p="$1"
  [[ -f "$p" ]] && cat "$p" || true
}

run_exec() {
  local out_file="$1"
  local err_file="$2"

  local args=(notebook exec "$NB_ID" "$CMD" --json)

  if [[ -n "$CWD" ]]; then
    args+=(--cwd "$CWD")
  fi

  if [[ ${#ENV_KVS[@]} -gt 0 ]]; then
    for kv in "${ENV_KVS[@]}"; do
      args+=(--env "$kv")
    done
  fi

  if inspire "${args[@]}" >"$out_file" 2>"$err_file"; then
    return 0
  fi
  return $?
}

out="$(mktemp_safe)"
err="$(mktemp_safe)"
trap 'rm -f "$out" "$err"' EXIT

cli_rc=0
run_exec "$out" "$err" || cli_rc=$?
raw_stdout="$(read_file "$out")"
raw_stderr="$(read_file "$err")"

python - "$NB_ID" "$CMD" "$CWD" "$cli_rc" "$raw_stdout" "$raw_stderr" "$JSON_OUT" <<'PY'
import json,sys

nb,cmd,cwd = sys.argv[1], sys.argv[2], sys.argv[3]
cli_rc = int(sys.argv[4])
raw_stdout, raw_stderr = sys.argv[5], sys.argv[6]
json_out = sys.argv[7] == '1'


def try_json(s):
    try:
        return json.loads(s)
    except Exception:
        return None

p = try_json(raw_stdout)

result = {
    'notebook': nb,
    'cmd': cmd,
    'cwd': cwd or None,
    'use_session': False,
    'cli_rc': cli_rc,
    'raw_stdout': raw_stdout,
    'raw_stderr': raw_stderr,
    'parsed_json': p,
}

ok = False
remote_exit_code = None
resolved_id = None

if isinstance(p, dict) and p.get('success') is True:
    data = p.get('data') or {}
    resolved_id = data.get('notebook_id')
    remote_exit_code = data.get('exit_code')
    ok = True

result['success'] = bool(ok)
result['resolved_notebook_id'] = resolved_id
result['remote_exit_code'] = remote_exit_code

if json_out:
    print(json.dumps(result, ensure_ascii=False))
else:
    print(f"success: {result['success']}")
    if resolved_id:
        print(f"resolved_notebook_id: {resolved_id}")
    if remote_exit_code is not None:
        print(f"remote_exit_code: {remote_exit_code}")
    if isinstance(p, dict):
        print(json.dumps(p, ensure_ascii=False))
    else:
        if raw_stdout:
            print(raw_stdout)
        if raw_stderr:
            print(raw_stderr, file=sys.stderr)

raise SystemExit(0 if ok else 3)
PY
