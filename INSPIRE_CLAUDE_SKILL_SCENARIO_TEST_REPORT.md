# Inspire GPU offline workflow — Skill-based scenario test report

Date: 2026-03-12
Owner: wanjiaxin

This report validates the **skill-shaped workflows** under `~/.claude/skills/` for an environment where:
- CPU machine has internet
- GPU notebooks/jobs (H100/H200) have no internet
- Filesystem is shared
- SSH/tunnel/bridge/sync are not usable
- `inspire notebook terminal` is human-only and must not be used

It is a **scenario-based validation** (dry-run / tabletop test). It checks that each scenario has:
- a clear entry skill
- correct routing (exec vs run)
- correct quoting/logging rules
- correct fallback/reset behaviors

## Skills under test

- `inspire-gpu-notebook`
- `inspire-gpu`
- `inspire-gpu-logged`
- `inspire-run-logged`
- `inspire-gpu-session-reset`

## Global invariants (must hold in all scenarios)

1) Never use:
   - `inspire notebook terminal`
   - `inspire notebook ssh`
   - `inspire tunnel ...`
   - `inspire bridge ...`
   - `inspire sync`

2) Session-first for rapid iteration:
   - `inspire notebook exec-session start ... --cwd "$(pwd)"`
   - `inspire notebook exec --session ... "<cmd>"`
   - If session fails: stop/start once, retry once, then fallback to non-session exec.

3) Durable logging for long runs:
   - Shared log path: `${INSPIRE_TARGET_DIR}/logs/<exp>/<exp>_<ts>.log`
   - Always: `(<cmd>) 2>&1 | tee -a '<log_file>'`
   - For `inspire run`, also pass `--log-file <log_file>` when supported.

4) Shell quoting safety:
   - If command needs to print env vars, use `\$VAR` or single quotes to avoid local expansion.

---

## Scenario 01 — Quick GPU sanity check (CUDA available)

**User intent:** “帮我在 GPU 上确认 torch 能用，看看 cuda 是否可用。”

**Entry skill:** `inspire-gpu`

**Expected commands:**
- `inspire notebook exec-session start <nb> --cwd "$(pwd)"`
- `inspire notebook exec --session <nb> "python -c 'import torch; print(torch.cuda.is_available())'"`

**Pass criteria:**
- Uses session-first path
- No terminal/ssh usage

---

## Scenario 02 — Need a notebook first (reuse-first)

**User intent:** “给我一台 1xH200 notebook 用来调试。”

**Entry skill:** `inspire-gpu-notebook`

**Expected flow:**
1) `inspire notebook reusable -r 1xH200 [--json if supported]`
2) If none: `inspire notebook create -r 1xH200 ... --no-auto --wait`

**Pass criteria:**
- Always tries reusable first
- Only creates if reusable list empty

---

## Scenario 03 — Long-running eval on notebook with durable logs

**User intent:** “在 notebook 上跑一轮验证，要把输出保存下来，明天我看日志。”

**Entry skill:** `inspire-gpu-logged`

**Expected commands:**
- precondition: `INSPIRE_TARGET_DIR` required
- log path under `${INSPIRE_TARGET_DIR}/logs/<exp>/...`
- `inspire notebook exec --session <nb> "(<cmd>) 2>&1 | tee -a '<log_file>'"`

**Pass criteria:**
- Tee to shared log
- Still streams output

---

## Scenario 04 — Session stuck / page closed / not reachable

**User intent:** “exec --session 一直失败/卡住了，帮我修一下。”

**Entry skill:** `inspire-gpu-session-reset` (or `inspire-gpu` failure handler)

**Expected commands:**
- `inspire notebook exec-session stop <nb>` (ignore errors)
- `inspire notebook exec-session start <nb> --cwd "$(pwd)"`
- optional: `inspire notebook exec --session <nb> "pwd"`

**Pass criteria:**
- Performs reset once
- Returns to session-first execution

---

## Scenario 05 — Session still failing after reset → fallback to non-session

**User intent:** “reset 了还是不行，先跑起来就行。”

**Entry skill:** `inspire-gpu` (failure handler)

**Expected fallback:**
- `inspire notebook exec <nb> "<cmd>"`

**Pass criteria:**
- Fallback only after one reset+retry

---

## Scenario 06 — Background training: use run, not notebook exec

**User intent:** “提交一个训练任务跑一晚上，明天只看结果和日志。”

**Entry skill:** `inspire-run-logged`

**Expected:**
- requires user to provide `--gpus N` (no default)
- defaults type to h200 if not specified
- ensures shared log dir exists
- `inspire run "(<cmd>) 2>&1 | tee -a '<log_file>'" ... --log-file "<log_file>"`

**Pass criteria:**
- Uses run (scheduled), not notebook exec
- Guarantees shared log

---

## Scenario 07 — Image short name normalization for run

**User intent:** “用镜像 base-wjx:v3 提交 run。”

**Entry skill:** `inspire-run-logged`

**Expected normalization:**
- if no '/', rewrite to `inspire-studio/base-wjx:v3`

**Pass criteria:**
- run/job image always normalized

---

## Scenario 08 — Notebook exec needs env var printed (avoid local expansion)

**User intent:** “在 GPU 上 echo 一下 E2E 环境变量。”

**Entry skill:** `inspire-gpu`

**Expected safe command:**
- `inspire notebook exec --session <nb> "echo E2E=\$E2E"`

**Fail mode to avoid:**
- local shell expands `$E2E` before sending to remote

**Pass criteria:**
- Uses `\$` or single quotes

---

## Scenario 09 — Notebook not RUNNING

**User intent:** “exec 报 notebook not RUNNING。”

**Entry skill:** `inspire-gpu`

**Expected behavior:**
- attempt: `inspire notebook start <nb>`
- then rerun exec

**Pass criteria:**
- Uses start rather than switching to forbidden ssh/terminal

---

## Scenario 10 — Multi-step debug loop (many short commands)

**User intent:** “我会频繁改代码+跑单测+打印中间变量。”

**Entry skill:** `inspire-gpu` (session-first)

**Expected:**
- reuse one exec-session
- repeated `inspire notebook exec --session ...` calls

**Pass criteria:**
- Avoids creating new notebooks repeatedly
- Avoids run for short iteration

---

## Scenario 11 — Kill runaway process (safe pattern)

**User intent:** “刚才跑的训练卡死了，想停掉。”

**Entry skill:** `inspire-gpu` (pattern-guided)

**Expected safe approach:**
- `inspire notebook exec --session <nb> "pkill -f '<very-specific-pattern>'"`

**Pass criteria:**
- Uses targeted pattern
- Does not use broad `pkill python` unless user explicitly accepts blast radius

---

## Scenario 12 — Dataset/model fetch must happen on CPU (GPU offline)

**User intent:** “下载模型/数据，然后在 GPU 上训练。”

**Routing:**
- CPU: use normal Bash locally to download to shared path
- GPU: use `inspire-gpu` or `inspire-run-logged` to consume shared artifacts

**Pass criteria:**
- No attempt to `pip install` from internet on GPU
- Uses shared filesystem as handoff

---

## Scenario 13 — uv environment management

**User intent:** “装依赖、创建虚拟环境。”

**Expected:**
- Recommend `uv venv` + `uv pip install ...` or `uv sync`
- Avoid suggesting plain `pip install ...` unless user asks

**Pass criteria:**
- Consistent with global rule

---

## Scenario 14 — Missing INSPIRE_TARGET_DIR

**User intent:** “跑 /gpu-logged 或 /run-logged，但我没设置 INSPIRE_TARGET_DIR。”

**Expected:**
- Precondition fails fast with actionable message:
  - `Please export INSPIRE_TARGET_DIR to a shared path`

**Pass criteria:**
- Does not silently write logs to non-shared local paths

---

## Scenario 15 — User requests RTX 4090 explicitly

**User intent:** “我要 1x4090 notebook。”

**Entry skill:** `inspire-gpu-notebook`

**Expected:**
- respects explicit request (do not override to H200)

**Pass criteria:**
- 4090 only when explicitly requested; otherwise prefer H100/H200

---

## Overall assessment

### Coverage
- Notebook acquisition: ✅ (reuse-first, create-only-if-needed)
- Fast iteration: ✅ (exec-session)
- Recovery: ✅ (session reset + fallback)
- Long runs: ✅ (run-logged + tee)
- Logging: ✅ (shared log is source of truth)
- Offline constraints: ✅ (no terminal/ssh/tunnel/bridge/sync)
- Quoting gotchas: ✅ (\$VAR guidance)

### Known limitations / follow-ups
1) `inspire notebook reusable --json` support may vary; skill text already notes fallback without `--json`.
2) GPU count for `inspire run` cannot be defaulted safely; user must specify `--gpus`.
3) For notebook creation, image selection policy is environment-specific; may be added later if you want a default image mapping.
