#!/usr/bin/env bash
set -euo pipefail

# gpu-notebook-init.sh
#
# Reuse-first GPU notebook picker/creator for Inspire.
#
# This is a bash implementation of the /gpu-notebook-init skill's *core* logic:
# - Given an explicit resource string like "1xH200", try to reuse an idle notebook
#   via `inspire notebook reusable -r <resource>` (JSON output is assumed).
# - If none are reusable, create a new notebook via `inspire notebook create ... --json --wait`.
# - Print the chosen notebook's name+id in a deterministic format.
#
# Notes:
# - This script does NOT attempt to infer GPU count/type from natural language.
#   It requires either --resource, or (--gpus and --type).
# - This script runs on the CPU machine (control plane). GPU notebooks are offline.

usage() {
  cat <<'EOF'
Usage:
  tools/gpu-notebook-init.sh --resource <NxTYPE> [--name <NAME>]
  tools/gpu-notebook-init.sh --gpus <N> --type <H100|H200|4090> [--name <NAME>]

Options:
  --resource <NxTYPE>   Resource string, e.g. 1xH200, 8xH100, 2x4090
  --gpus <N>            GPU count (integer). Used with --type.
  --type <TYPE>         GPU type: H100 | H200 | 4090. Used with --gpus.
  --name <NAME>         Notebook name to use *if creating* (optional).
                        Default: dev-<type-lower>-<Ng>-<MMDD-HHMMSS>
  --json                Output JSON instead of text.
  -h, --help            Show this help.

Output (text mode):
  GPU resource: <resource>
  Notebook name: <name>
  Notebook id: <id>

Output (json mode):
  {"resource":...,"name":...,"id":...,"mode":"reused"|"created","session":{"ok":true|false,"pwd":"...","raw_json":{...}}}
EOF
}

# Verify an exec-session is usable by running: inspire notebook exec --session <nb> "pwd" --json
# Prints JSON: {"ok": bool, "pwd": str, "raw_json": obj}
verify_session() {
  local nb_id="$1"
  local out
  local rc

  set +e
  out="$(inspire notebook exec --session "$nb_id" "pwd" --json 2>/dev/null)"
  rc=$?

  # One quick retry: first call may implicitly start exec-session / re-auth.
  if [[ $rc -ne 0 ]]; then
    out="$(inspire notebook exec --session "$nb_id" "pwd" --json 2>/dev/null)"
    rc=$?
  fi

  set -e

  python - "$rc" "$out" <<'PY'
import json,sys
rc=int(sys.argv[1])
raw_s=sys.argv[2]

ok=False
pwd=""
raw=None

try:
    raw=json.loads(raw_s) if raw_s else None
except Exception:
    raw=None

if rc==0 and isinstance(raw, dict):
    if raw.get('success') is True:
        data=raw.get('data') or {}
        exit_code=data.get('exit_code', None)
        out_text=data.get('output') or ""
        if exit_code == 0:
            ok=True
            pwd=(out_text.strip().splitlines()[-1] if out_text else "")

print(json.dumps({'ok': ok, 'pwd': pwd, 'raw_json': raw}, ensure_ascii=False))
PY
}

# Print final result payload (text/json) and enforce session health.
finalize_and_print() {
  local picked_json="$1"  # {resource,name,id,mode}

  # Extract id for session verification
  local nb_id
  nb_id="$(python - "$picked_json" <<'PY'
import json,sys
p=json.loads(sys.argv[1])
print(p.get('id',''))
PY
)"

  if [[ -z "$nb_id" ]]; then
    echo "Error: missing notebook id in result payload" >&2
    echo "$picked_json" >&2
    exit 3
  fi

  local session_json
  session_json="$(verify_session "$nb_id")"

  # Merge session into payload
  local merged
  merged="$(python - "$picked_json" "$session_json" <<'PY'
import json,sys
base=json.loads(sys.argv[1])
sess=json.loads(sys.argv[2])
base['session']=sess
print(json.dumps(base, ensure_ascii=False))
PY
)"

  # Fail hard if session is not OK (per user preference)
  local ok
  ok="$(python - "$session_json" <<'PY'
import json,sys
p=json.loads(sys.argv[1])
print('1' if p.get('ok') else '0')
PY
)"

  if [[ "$ok" != "1" ]]; then
    echo "Error: exec-session verification failed for notebook: $nb_id" >&2
    echo "$merged" >&2
    exit 4
  fi

  if [[ "$JSON_OUT" -eq 1 ]]; then
    echo "$merged"
  else
    python - "$merged" <<'PY'
import json,sys
p=json.loads(sys.argv[1])
print(f"GPU resource: {p.get('resource','')}")
print(f"Notebook name: {p.get('name','')}")
print(f"Notebook id: {p.get('id','')}")
sess=p.get('session') or {}
print(f"Session OK: {bool(sess.get('ok'))}")
print(f"Remote PWD: {sess.get('pwd','')}")
PY
  fi
}

RESOURCE=""
GPUS=""
TYPE=""
NAME=""
JSON_OUT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resource)
      RESOURCE="${2:-}"; shift 2 ;;
    --gpus)
      GPUS="${2:-}"; shift 2 ;;
    --type)
      TYPE="${2:-}"; shift 2 ;;
    --name)
      NAME="${2:-}"; shift 2 ;;
    --json)
      JSON_OUT=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$RESOURCE" ]]; then
  if [[ -z "$GPUS" || -z "$TYPE" ]]; then
    echo "Error: must provide --resource, or both --gpus and --type." >&2
    usage
    exit 2
  fi

  if ! [[ "$GPUS" =~ ^[0-9]+$ ]] || [[ "$GPUS" -le 0 ]]; then
    echo "Error: --gpus must be a positive integer." >&2
    exit 2
  fi

  case "$TYPE" in
    H100|H200|4090) ;;
    *)
      echo "Error: --type must be one of: H100, H200, 4090" >&2
      exit 2
      ;;
  esac

  RESOURCE="${GPUS}x${TYPE}"
fi

# Default name only matters for creation.
if [[ -z "$NAME" ]]; then
  # lower-case type token for display; keep 4090 as-is.
  type_lc="$(echo "$RESOURCE" | awk -F'x' '{print $2}' | tr '[:upper:]' '[:lower:]')"
  n="$(echo "$RESOURCE" | awk -F'x' '{print $1}')"
  ts="$(date +%m%d-%H%M%S)"
  NAME="dev-${type_lc}-${n}g-${ts}"
fi

# 1) Try reuse (assumes JSON output).
reuse_json="$(inspire notebook reusable -r "$RESOURCE")"
reuse_json="$(inspire notebook reusable -r "$RESOURCE")" # run twice to mitigate potential cache staleness (first call may trigger background refresh)

picked="$(python -c '
import json,sys
p=json.load(sys.stdin)
items=(p.get("data") or {}).get("items") or []
if items:
    nb=items[0] or {}
    rid = nb.get("resource") or ""
    name = nb.get("name") or ""
    _id = nb.get("id") or ""
    print(json.dumps({"resource": rid, "name": name, "id": _id, "mode": "reused"}, ensure_ascii=False))
' <<<"$reuse_json")"

if [[ -n "$picked" ]]; then
  picked_json="$picked"
  finalize_and_print "$picked_json"
  exit 0
fi

# 2) Create new notebook
create_json="$(inspire notebook create -r "$RESOURCE" -n "$NAME" --json --wait)"

created="$(python - "$create_json" <<'PY'
import json,sys
p=json.loads(sys.argv[1])
if not p.get('success', False):
    err=p.get('error') or {}
    msg = err.get('message') or 'unknown error'
    hint = err.get('hint') or ''
    raise SystemExit(f"Create failed: {err.get('type','Error')}: {msg}\n{hint}")

data=p.get('data') or {}
resource = data.get('resource') or ''
name = data.get('name') or ''
nbid = data.get('notebook_id') or data.get('id') or ''
print(json.dumps({'resource': resource, 'name': name, 'id': nbid, 'mode': 'created'}, ensure_ascii=False))
PY
)"

finalize_and_print "$created"
