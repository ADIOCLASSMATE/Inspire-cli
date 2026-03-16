#!/usr/bin/env bash
set -euo pipefail

# gpu-notebook-session-interact.sh
#
# Fixed session-mode notebook interaction wrapper.
#
# Goal:
# - Run a command on a GPU notebook using persistent exec-session mode.
# - If session-mode exec fails at the CLI/session layer, optionally reset the local exec-session once
#   (via tools/gpu-notebook-session-reset.sh) and retry once.
#
# Notes:
# - This script runs on the CPU machine (control plane). GPU notebooks are offline.
# - Remote command failure (data.exit_code != 0) is NOT a reason to reset the session.

usage() {
  cat <<'EOF'
Usage:
  tools/gpu-notebook-session-interact.sh <notebook_id> <cmd> [--cwd <DIR>] [--env KEY=VAL ...] [--no-reset] [--json]

Args:
  <notebook_id>       Notebook UUID id (required). Names are NOT accepted.
  <cmd>               Command string to run on the notebook.
                      IMPORTANT: pass as a single shell argument (quote it).

Options:
  --cwd <DIR>         Remote working directory (passed to inspire notebook exec).
  --env KEY=VAL       Repeatable env var (passed to inspire notebook exec).
  --no-reset          Do not attempt session reset+retry on session/CLI failure.
  --json              Emit a single JSON object (recommended for automation).
  -h, --help          Show this help.

Exit codes:
  0   Ran command (inspire success==true). Remote exit_code may still be non-zero.
  2   Invalid arguments.
  3   Session/CLI failure after optional reset+retry.
EOF
}

NB_ID=""
CMD=""
CWD=""
JSON_OUT=0
DO_RESET=1
ENV_KVS=()

# Parse positional args
if [[ $# -lt 2 ]]; then
  usage >&2
  exit 2
fi
NB_ID="$1"; shift
CMD="$1"; shift

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
    --no-reset)
      DO_RESET=0; shift ;;
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

run_exec_session() {
  local out_file="$1"
  local err_file="$2"

  local args=(notebook exec --session "$NB_ID" "$CMD" --json)

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

# Attempt 1
out1="$(mktemp_safe)"
err1="$(mktemp_safe)"
trap 'rm -f "$out1" "$err1" "${out2:-}" "${err2:-}"' EXIT

rc1=0
run_exec_session "$out1" "$err1" || rc1=$?
stdout1="$(read_file "$out1")"
stderr1="$(read_file "$err1")"

# Determine if we should reset+retry.
# Reset is for CLI/session-layer failures:
# - inspire command rc != 0
# - OR JSON parsed and success==false
# - OR rc==0 but non-JSON output
# Remote command failure (success==true but data.exit_code!=0) should not trigger reset.
need_reset="$(
  python - "$rc1" "$stdout1" <<'PY'
import json,sys
rc=int(sys.argv[1])
stdout=sys.argv[2]
if rc!=0:
    print('1'); raise SystemExit
try:
    p=json.loads(stdout)
except Exception:
    print('1'); raise SystemExit
print('1' if isinstance(p, dict) and p.get('success') is False else '0')
PY
)"

# Optional reset + retry once
reset_out=""
reset_rc=0
retried=0

if [[ "$DO_RESET" -eq 1 && "$need_reset" == "1" ]]; then
  retried=1

  reset_args=("/inspire/hdd/global_user/wanjiaxin-253108030048/Inspire-cli/tools/gpu-notebook-session-reset.sh" "$NB_ID" --json)
  if [[ -n "$CWD" ]]; then
    reset_args+=(--cwd "$CWD")
  fi

  set +e
  reset_out="$(${reset_args[@]} 2>&1)"
  reset_rc=$?
  set -e

  out2="$(mktemp_safe)"
  err2="$(mktemp_safe)"
  rc2=0
  run_exec_session "$out2" "$err2" || rc2=$?
  stdout2="$(read_file "$out2")"
  stderr2="$(read_file "$err2")"
else
  rc2="$rc1"
  stdout2="$stdout1"
  stderr2="$stderr1"
fi

python - "$NB_ID" "$CMD" "$CWD" "$retried" "$rc1" "$stdout1" "$stderr1" "$reset_rc" "$reset_out" "$rc2" "$stdout2" "$stderr2" "$JSON_OUT" <<'PY'
import json,sys

nb,cmd,cwd = sys.argv[1], sys.argv[2], sys.argv[3]
retried = sys.argv[4] == '1'
rc1 = int(sys.argv[5])
stdout1, stderr1 = sys.argv[6], sys.argv[7]
reset_rc = int(sys.argv[8])
reset_out = sys.argv[9]
rc2 = int(sys.argv[10])
stdout2, stderr2 = sys.argv[11], sys.argv[12]
json_out = sys.argv[13] == '1'


def try_json(s):
    try:
        return json.loads(s)
    except Exception:
        return None

p = try_json(stdout2)

result = {
    'notebook': nb,
    'cmd': cmd,
    'cwd': cwd or None,
    'use_session': True,
    'attempts': 2 if retried else 1,
    'retried_after_reset': retried,
    'first': {
        'cli_rc': rc1,
        'raw_stdout': stdout1,
        'raw_stderr': stderr1,
    },
    'reset': {
        'performed': retried,
        'rc': reset_rc if retried else None,
        'raw_output': reset_out if retried else None,
    },
    'final': {
        'cli_rc': rc2,
        'raw_stdout': stdout2,
        'raw_stderr': stderr2,
        'parsed_json': p,
    },
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
    if retried:
        print("retried_after_reset: true")
    if isinstance(p, dict):
        print(json.dumps(p, ensure_ascii=False))
    else:
        if stdout2:
            print(stdout2)
        if stderr2:
            print(stderr2, file=sys.stderr)

raise SystemExit(0 if ok else 3)
PY
