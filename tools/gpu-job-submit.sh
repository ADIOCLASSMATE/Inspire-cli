#!/usr/bin/env bash
set -euo pipefail

# gpu-job-submit.sh
#
# Submit a scheduled GPU job (non-interactive) and guarantee durable logs on the shared filesystem.
#
# This script is designed for environments where:
# - CPU machine has internet
# - GPU compute is offline
# - "job logs" streaming via tunnel/SSH may be unavailable
#
# Strategy:
# - Create a shared log file under $INSPIRE_TARGET_DIR/logs/<exp>/...
# - Submit via `inspire job create` with the remote command wrapped in:
#     (<cmd>) 2>&1 | tee -a '<log_file>'
# - Also tee the *submission* CLI output to a submit log for audit/debug.

usage() {
  cat <<'EOF'
Usage:
  tools/gpu-job-submit.sh --name <experiment_name> --cmd <command> (--resource <NxTYPE> | --gpus <N> --type <H100|H200|4090>) [--image <IMG>] [--priority <P>] [--auto|--no-auto] [--json]

Required:
  --name <experiment_name>   Experiment/job name.
  --cmd <command>            Command to run on GPU (pass as ONE shell argument; quote it).

Resource (choose one form):
  --resource <NxTYPE>        e.g. 1xH200, 8xH100, 2x4090
  --gpus <N> --type <TYPE>   Alternative to --resource.

Optional:
  --image <IMG>              Image for job. If IMG has no '/', it will be normalized to 'inspire-studio/IMG'.
  --priority <P>             Integer priority (default: 1)
  --auto                     Enable auto mode (passes --auto) [default]
  --no-auto                  Disable auto mode (passes --no-auto)
  --json                     Emit a single JSON object.
  -h, --help                 Show help.

Precondition:
  INSPIRE_TARGET_DIR must be set to a shared path.

Exit codes:
  0 success
  2 invalid arguments / missing INSPIRE_TARGET_DIR
  3 submission failed (no job id)
EOF
}

EXP=""
CMD=""
RESOURCE=""
GPUS=""
TYPE=""
IMAGE=""
PRIORITY="1"
AUTO_FLAG="--auto"
JSON_OUT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage; exit 0 ;;
    --name)
      EXP="${2:-}"; shift 2 ;;
    --cmd)
      CMD="${2:-}"; shift 2 ;;
    --resource)
      RESOURCE="${2:-}"; shift 2 ;;
    --gpus)
      GPUS="${2:-}"; shift 2 ;;
    --type)
      TYPE="${2:-}"; shift 2 ;;
    --image)
      IMAGE="${2:-}"; shift 2 ;;
    --priority)
      PRIORITY="${2:-}"; shift 2 ;;
    --auto)
      AUTO_FLAG="--auto"; shift ;;
    --no-auto)
      AUTO_FLAG="--no-auto"; shift ;;
    --json)
      JSON_OUT=1; shift ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$EXP" || -z "$CMD" ]]; then
  echo "Error: --name and --cmd are required." >&2
  usage >&2
  exit 2
fi

: "${INSPIRE_TARGET_DIR:?Error: INSPIRE_TARGET_DIR must be set to a shared path}"

if [[ -z "$RESOURCE" ]]; then
  if [[ -z "$GPUS" || -z "$TYPE" ]]; then
    echo "Error: must provide --resource, or both --gpus and --type." >&2
    usage >&2
    exit 2
  fi
  if ! [[ "$GPUS" =~ ^[0-9]+$ ]] || [[ "$GPUS" -le 0 ]]; then
    echo "Error: --gpus must be a positive integer." >&2
    exit 2
  fi
  case "$TYPE" in
    H100|H200|4090|h100|h200)
      TYPE="${TYPE^^}" ;;
    *)
      echo "Error: --type must be one of: H100, H200, 4090" >&2
      exit 2
      ;;
  esac
  RESOURCE="${GPUS}x${TYPE}"
fi

if ! [[ "$PRIORITY" =~ ^[0-9]+$ ]]; then
  echo "Error: --priority must be an integer." >&2
  exit 2
fi

# Normalize image for job/run: if it has no '/', prefix with inspire-studio/
if [[ -n "$IMAGE" ]]; then
  case "$IMAGE" in
    */*) ;;
    *) IMAGE="inspire-studio/${IMAGE}" ;;
  esac
fi

# Prepare log paths
TS="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="${INSPIRE_TARGET_DIR}/logs/${EXP}"
REMOTE_LOG_FILE="${LOG_DIR}/${EXP}_${TS}.log"
SUBMIT_LOG_FILE="${LOG_DIR}/${EXP}_submit_${TS}.log"
mkdir -p "$LOG_DIR"

# Wrap remote command to guarantee durable logs
REMOTE_CMD="(${CMD}) 2>&1 | tee -a '${REMOTE_LOG_FILE}'"

# Build job create args
job_args=(job create -n "$EXP" -r "$RESOURCE" -c "$REMOTE_CMD" "$AUTO_FLAG" --priority "$PRIORITY")
if [[ -n "$IMAGE" ]]; then
  job_args+=(--image "$IMAGE")
fi

# Submit and capture output (also tee to submit log)
set +e
cli_out="$({ INSPIRE_TARGET_DIR="$INSPIRE_TARGET_DIR" inspire "${job_args[@]}"; } 2>&1 | tee -a "$SUBMIT_LOG_FILE")"
cli_rc=$?
set -e

# Extract job id from output
job_id="$(
  python - "$cli_out" <<'PY'
import re,sys
s=sys.argv[1]
# Typical: "OK Job created: job-..."
m=re.search(r"\bjob-[0-9a-fA-F\-]{8,}\b", s)
print(m.group(0) if m else "")
PY
)"

success=0
if [[ $cli_rc -eq 0 && -n "$job_id" ]]; then
  success=1
fi

if [[ "$JSON_OUT" -eq 1 ]]; then
  python - "$success" "$job_id" "$EXP" "$RESOURCE" "$IMAGE" "$PRIORITY" "$REMOTE_LOG_FILE" "$SUBMIT_LOG_FILE" "$cli_rc" "$cli_out" <<'PY'
import json,sys
success = sys.argv[1] == '1'
job_id = sys.argv[2]
exp = sys.argv[3]
resource = sys.argv[4]
image = sys.argv[5] or None
priority = int(sys.argv[6])
remote_log_file = sys.argv[7]
submit_log_file = sys.argv[8]
cli_rc = int(sys.argv[9])
cli_output = sys.argv[10]
print(json.dumps({
  'success': success,
  'job_id': job_id or None,
  'name': exp,
  'resource': resource,
  'image': image,
  'priority': priority,
  'remote_log_file': remote_log_file,
  'submit_log_file': submit_log_file,
  'cli_rc': cli_rc,
  'cli_output': cli_output,
}, ensure_ascii=False))
PY
else
  if [[ "$success" -eq 1 ]]; then
    echo "Job submitted."
    echo "- Job ID: $job_id"
    echo "- Name: $EXP"
    echo "- Resource: $RESOURCE"
    if [[ -n "$IMAGE" ]]; then
      echo "- Image: $IMAGE"
    fi
    echo "- Remote log (tee target): $REMOTE_LOG_FILE"
    echo "- Submit log: $SUBMIT_LOG_FILE"
  else
    echo "Error: job submission failed (cli_rc=$cli_rc)." >&2
    echo "Submit log: $SUBMIT_LOG_FILE" >&2
    printf '%s\n' "$cli_out" >&2
    exit 3
  fi
fi

# Exit based on submission success
if [[ "$success" -ne 1 ]]; then
  exit 3
fi
