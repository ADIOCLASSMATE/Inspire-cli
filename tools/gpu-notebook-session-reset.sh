#!/usr/bin/env bash
set -euo pipefail

# gpu-notebook-session-reset.sh
#
# Reset (or initialize) the persistent exec-session for a notebook on THIS machine.
#
# Flow:
# 1) `inspire notebook exec-session list --json` to see if a local session exists.
# 2) If exists: stop it and wait for the old daemon PID to exit.
# 3) Start a session with --cwd (defaults to local $(pwd)).
# 4) Verify the new daemon is reachable by waiting for it to appear in
#    `exec-session list` and then doing a local socket ping (no remote exec).
#    The first start attempt may time out (race/slow Playwright); on failure,
#    we stop/start once and retry the verification.

SESSION_DIR="/tmp/inspire-exec-sessions"

_safe_session_key() {
  # Match inspire.bridge.exec_session's sanitization:
  # notebook_id.replace("/", "_").replace("\\", "_")
  local s="$1"
  s="${s//\//_}"
  s="${s//\\/_}"
  printf '%s' "$s"
}

_session_log_path() {
  local notebook_id="$1"
  printf '%s/%s.log' "$SESSION_DIR" "$(_safe_session_key "$notebook_id")"
}

diagnose_failure() {
  local phase="$1" # start|test
  local rc="$2"
  local cli_output="$3"

  local log_path
  log_path="$(_session_log_path "$NOTEBOOK_ID")"

  local log_tail=""
  if [[ -f "$log_path" ]]; then
    # Keep diagnostics bounded.
    log_tail="$(tail -n 120 "$log_path" 2>/dev/null || true)"
  fi

  if [[ "$JSON_OUT" -eq 1 ]]; then
    python -c '
import json,sys
phase=sys.argv[1]
rc=int(sys.argv[2])
notebook=sys.argv[3]
notebook_id=sys.argv[4]
cwd=sys.argv[5]
cli_output=sys.argv[6]
log_path=sys.argv[7]
log_tail=sys.argv[8]
print(json.dumps({
  "success": False,
  "phase": phase,
  "exit_code": rc,
  "notebook": notebook,
  "notebook_id": notebook_id,
  "cwd": cwd,
  "cli_output": cli_output,
  "session_log_path": log_path,
  "session_log_tail": log_tail,
}, ensure_ascii=False))
' "$phase" "$rc" "$NB" "$NOTEBOOK_ID" "$CWD" "$cli_output" "$log_path" "$log_tail"
  else
    echo "Error: phase='$phase' failed (exit_code=$rc)" >&2
    echo "Notebook: $NB" >&2
    echo "Resolved notebook_id: $NOTEBOOK_ID" >&2
    echo "CWD: $CWD" >&2
    if [[ -n "$cli_output" ]]; then
      echo "--- CLI output ---" >&2
      printf '%s\n' "$cli_output" >&2
    fi
    echo "--- session log ---" >&2
    echo "Path: $log_path" >&2
    if [[ -n "$log_tail" ]]; then
      printf '%s\n' "$log_tail" >&2
    else
      echo "(no log found or empty)" >&2
    fi
  fi
}

usage() {
  cat <<'EOF'
Usage:
  tools/gpu-notebook-session-reset.sh <notebook> [--cwd <DIR>] [--json]

Args:
  <notebook>        Notebook id or name accepted by inspire.

Options:
  --cwd <DIR>       CWD to init in session (default: local $(pwd)).
  --json            Emit a single JSON line on success.
  -h, --help        Show this help.

Exit codes:
  0 success
  1 session test failed after retry
  2 invalid arguments
EOF
}

NB=""
CWD=""
JSON_OUT=0

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage; exit 0 ;;
    --cwd)
      CWD="${2:-}"; shift 2 ;;
    --json)
      JSON_OUT=1; shift ;;
    --*)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
    *)
      if [[ -z "$NB" ]]; then
        NB="$1"; shift
      else
        echo "Unexpected arg: $1" >&2
        usage
        exit 2
      fi
      ;;
  esac
done

if [[ -z "$NB" ]]; then
  echo "Error: notebook is required." >&2
  usage
  exit 2
fi

if [[ -z "$CWD" ]]; then
  CWD="$(pwd)"
fi

wait_pid_exit() {
  local pid="$1"
  local max_wait_s="${2:-20}"

  local ticks=0
  local max_ticks=$((max_wait_s * 2))
  while [[ $ticks -lt $max_ticks ]]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.5
    ticks=$((ticks + 1))
  done
  return 1
}

# Snapshot existing local sessions (local-only) via exec-session list.
# We use this to resolve what the user passed (name or id) into a concrete notebook_id.
list_json="$(inspire notebook exec-session list --json 2>/dev/null || true)"

# Try to match user-provided ident against existing sessions by:
# - notebook_id == ident
# - name == ident
# Prints a JSON object: {"found": bool, "notebook_id": str, "name": str, "pid": int|null}
find_session_by_ident() {
  local ident="$1"
  local list_json_in="$2"
  python -c '
import json,sys
ident=sys.argv[1]

try:
    p=json.load(sys.stdin)
except Exception:
    print(json.dumps({"found": False, "notebook_id": "", "name": "", "pid": None}, ensure_ascii=False))
    raise SystemExit(0)

sessions=None
if isinstance(p, dict):
    if isinstance(p.get("data"), dict) and isinstance(p["data"].get("sessions"), list):
        sessions=p["data"]["sessions"]
    elif isinstance(p.get("sessions"), list):
        sessions=p["sessions"]

if not sessions:
    print(json.dumps({"found": False, "notebook_id": "", "name": "", "pid": None}, ensure_ascii=False))
    raise SystemExit(0)

for s in sessions:
    if not isinstance(s, dict):
        continue
    nbid=str(s.get("notebook_id") or "")
    name=str(s.get("name") or "")
    if ident and (ident == nbid or ident == name):
        pid=s.get("pid")
        out={"found": True, "notebook_id": nbid, "name": name, "pid": pid}
        print(json.dumps(out, ensure_ascii=False))
        raise SystemExit(0)

print(json.dumps({"found": False, "notebook_id": "", "name": "", "pid": None}, ensure_ascii=False))
' "$ident" <<<"$list_json_in"
}

match0="$(find_session_by_ident "$NB" "$list_json")"
NOTEBOOK_ID="$(python -c 'import json,sys; print(json.loads(sys.argv[1]).get("notebook_id", ""))' "$match0")"
old_pid="$(python -c 'import json,sys; p=json.loads(sys.argv[1]); v=p.get("pid", None); print("" if v is None else str(v))' "$match0")"

# If a local session exists for this ident, stop it first (reset semantics).
if [[ -n "$NOTEBOOK_ID" ]]; then
  inspire notebook exec-session stop "$NOTEBOOK_ID" >/dev/null || true
  if [[ -n "${old_pid:-}" ]]; then
    wait_pid_exit "$old_pid" 20 >/dev/null || true
  fi
fi

# Start session (best-effort). If we already resolved notebook_id, start by id.
start_target="$NB"
if [[ -n "$NOTEBOOK_ID" ]]; then
  start_target="$NOTEBOOK_ID"
fi

start_out=""
start_rc=0
start_out="$(inspire notebook exec-session start "$start_target" --cwd "$CWD" 2>&1)" || start_rc=$?

# After start, resolve the actual notebook_id again (important when user passed a name).
list_json1="$(inspire notebook exec-session list --json 2>/dev/null || true)"
match1="$(find_session_by_ident "$NB" "$list_json1")"
resolved_id1="$(python -c 'import json,sys; print(json.loads(sys.argv[1]).get("notebook_id", ""))' "$match1")"
if [[ -z "$resolved_id1" && "$start_target" != "$NB" ]]; then
  # If NB was a name but we started by id (or vice versa), try matching by start_target too.
  match1b="$(find_session_by_ident "$start_target" "$list_json1")"
  resolved_id1="$(python -c 'import json,sys; print(json.loads(sys.argv[1]).get("notebook_id", ""))' "$match1b")"
fi
if [[ -n "$resolved_id1" ]]; then
  NOTEBOOK_ID="$resolved_id1"
else
  NOTEBOOK_ID="$start_target"
fi

# Smoke test with retry once (exactly like your manual repro): exec --session ... "pwd".
run_pwd_test() {
  local nbid="$1"
  local out_file="$2"
  if inspire notebook exec --session "$nbid" "pwd" --json >"$out_file" 2>&1; then
    return 0
  fi
  return 1
}

tmp1="$(mktemp)"
trap 'rm -f "$tmp1" "${tmp2:-}"' EXIT

if run_pwd_test "$NOTEBOOK_ID" "$tmp1"; then
  if [[ "$JSON_OUT" -eq 1 ]]; then
    python -c 'import json,sys; print(json.dumps({"success":True,"notebook":sys.argv[1],"notebook_id":sys.argv[2],"cwd":sys.argv[3],"tested":True,"start_rc":int(sys.argv[4]),"start_output":sys.argv[5],"test_output":sys.argv[6]}, ensure_ascii=False))' "$NB" "$NOTEBOOK_ID" "$CWD" "$start_rc" "$start_out" "$(cat "$tmp1" 2>/dev/null || true)"
  else
    if [[ "$start_rc" -ne 0 ]]; then
      echo "Warning: exec-session start returned rc=$start_rc (continuing):" >&2
      printf '%s\n' "$start_out" >&2
    fi
    echo "Session ok: $NOTEBOOK_ID"
    echo "CWD: $CWD"
    cat "$tmp1"
  fi
  exit 0
fi

# Retry flow: stop/start then re-test.
list_json2="$(inspire notebook exec-session list --json 2>/dev/null || true)"
match2="$(find_session_by_ident "$NOTEBOOK_ID" "$list_json2")"
old_pid2="$(python -c 'import json,sys; p=json.loads(sys.argv[1]); v=p.get("pid", None); print("" if v is None else str(v))' "$match2")"

inspire notebook exec-session stop "$NOTEBOOK_ID" >/dev/null || true
if [[ -n "${old_pid2:-}" ]]; then
  wait_pid_exit "$old_pid2" 20 >/dev/null || true
fi

retry_start_out=""
retry_start_rc=0
retry_start_out="$(inspire notebook exec-session start "$NOTEBOOK_ID" --cwd "$CWD" 2>&1)" || retry_start_rc=$?

# Refresh id again (should still be NOTEBOOK_ID, but keep name/id robustness).
list_json3="$(inspire notebook exec-session list --json 2>/dev/null || true)"
match3="$(find_session_by_ident "$NB" "$list_json3")"
resolved_id3="$(python -c 'import json,sys; print(json.loads(sys.argv[1]).get("notebook_id", ""))' "$match3")"
if [[ -n "$resolved_id3" ]]; then
  NOTEBOOK_ID="$resolved_id3"
fi

tmp2="$(mktemp)"
if run_pwd_test "$NOTEBOOK_ID" "$tmp2"; then
  if [[ "$JSON_OUT" -eq 1 ]]; then
    python -c 'import json,sys; print(json.dumps({"success":True,"notebook":sys.argv[1],"notebook_id":sys.argv[2],"cwd":sys.argv[3],"tested":True,"retried":True,"retry_start_rc":int(sys.argv[4]),"retry_start_output":sys.argv[5],"test_output":sys.argv[6]}, ensure_ascii=False))' "$NB" "$NOTEBOOK_ID" "$CWD" "$retry_start_rc" "$retry_start_out" "$(cat "$tmp2" 2>/dev/null || true)"
  else
    if [[ "$retry_start_rc" -ne 0 ]]; then
      echo "Warning: exec-session start (retry) returned rc=$retry_start_rc (continuing):" >&2
      printf '%s\n' "$retry_start_out" >&2
    fi
    echo "Session ok (after retry): $NOTEBOOK_ID"
    echo "CWD: $CWD"
    cat "$tmp2"
  fi
  exit 0
fi

# Failure (include both attempts + session log tail)
attempts="--- attempt 1 (test) ---\n$(cat "$tmp1" 2>/dev/null || true)\n--- attempt 2 (test) ---\n$(cat "$tmp2" 2>/dev/null || true)"
diagnose_failure "test" 1 "$attempts"
exit 1
