# Inspire CLI 生产环境端到端评测与诊断报告（H 卡，仅接口调用）

> 生成时间：2026-03-12
>
> 目标：在**不进入仓库目录**、直接以生产用户方式调用已安装的 `inspire` CLI 的前提下，对 Inspire CLI 进行端到端评测。
>
> 约束（按需求执行）：
> - **只评测 H 卡（H100/H200）相关路径**，不使用 CPU/4090
> - **不评测 sync**（`inspire sync` 跳过）
> - **不评测 image 子命令**（`inspire image ...` 跳过；但 notebook/job 运行仍需指定可用镜像）
> - **已知在该生产环境不可用/不稳定的命令不再复测**：
>   - `inspire notebook ssh` / `inspire tunnel ...` / `inspire bridge ...`（rtunnel/SSH 链路不可用）
>   - `inspire job status` / `inspire job update`（本次环境出现连接错误）


## 1. 测试环境与假设

- CLI：`inspire`（已安装在系统 PATH 中）
- 鉴权：用户确认账号密码已配置为**全局配置**（测试过程中也观测到可自动 re-auth）
- Base URL：`https://qz.sii.edu.cn`
- Workspace：按全局配置（GPU workspace 等）
- 项目：测试过程中主要使用 **“公共兜底”**（平台自动选择/默认项目）
- 共享目录（用于 job 日志落盘）：
  - `INSPIRE_TARGET_DIR=/inspire/hdd/global_user/wanjiaxin-253108030048`
- 镜像（关键前置）：
  - 用户指定：必须使用 personal-visible 的 镜像，例如：`base-wjx:v3`
  - 评测中确认对 job 需使用全路径：`inspire-studio/base-wjx:v3`


## 2. 测试范围（覆盖矩阵）

| 模块 | 命令族 | 目标 | 结果 |
|---|---|---|---|
| 配置/鉴权 | `config` | 验证全局配置与登录可用 | ✅ 通过 |
| 资源 | `resources` | 查询 H 卡资源供给 | ✅ 通过 |
| 项目 | `project` | 列出可用项目（含 quota 信息） | ✅ 通过 |
| Notebook | `notebook create/list/status/exec/stop/start` | H100 notebook 全生命周期与命令执行 | ✅ 通过 |
| Notebook 复用探测 | `notebook reusable` | 查找可复用（空闲）GPU notebook | ✅ 通过（本次返回空列表） |
| Notebook Exec Session | `notebook exec-session` / `notebook exec --session` | 持久会话加速 exec | ✅ 通过 |
| Notebook SSH | `notebook ssh` | 建立 rtunnel + SSH 连接，支持 `--save-as` 形成 bridge profile | ❌ 失败 |
| Tunnel | `tunnel list/status/test/ssh-config` | 验证 tunnel profile 生成与连通性 | ⚠️ 部分通过（配置生成✅，连通性❌） |
| Bridge | `bridge exec/ssh/scp` | 通过 tunnel 执行命令/传输文件 | ❌ 未通过（依赖 SSH 隧道） |
| Job | `job create/list` | H100 job 提交与本地缓存 | ✅ 通过 |
| Job 日志 | `job logs` | 拉取/跟随 job 日志 | ❌ 失败（依赖 tunnel/SSH） |
| Job 状态/刷新 | `job status/update` | 轮询 API 更新状态 | ⚠️ 已知失败，本次按约束不再复测 |
| Run | `run` | 快速提交（H100） | ✅ 通过（成功创建 job） |

> 说明：本报告记录的是“生产模拟”实际跑出来的结果；失败项包含可复现步骤与诊断线索。


## 3. 详细评测过程与结果

### 3.1 配置与鉴权（✅ 通过）

**验证点**
- 能通过全局配置完成认证并调用平台接口。

**表现**
- 多个命令在 session 过期时会打印 `Session expired, re-authenticating...` 并继续成功执行（说明自动 re-auth 逻辑可工作）。


### 3.2 资源与项目信息（✅ 通过）

- `inspire resources list`：可获取 H100/H200 可用情况
- `inspire project list`：可列出项目及 quota/预算字段


### 3.3 Notebook（✅ 通过）

#### 3.3.1 已有 H100 notebook 的基本能力验证（✅）

对一个运行中的 notebook（8xH100）执行：

- `inspire notebook status <id> --json` ✅
- `inspire notebook exec <id> "nvidia-smi ..."` ✅（返回 8 张 H100 结果）
- `inspire notebook exec <id> "python -c 'import sys; print(sys.version)'"` ✅

结论：`notebook exec`（基于 Jupyter 的命令执行通道）在 H100 上稳定可用。

#### 3.3.2 notebook reusable（✅）

用于在创建新 notebook 前，尝试复用“正在运行且资源严格匹配”的 GPU notebook（GPU 场景下会通过 `nvidia-smi` 采样判断是否空闲）：

- `inspire notebook reusable -r 1xH100` ✅
  - 本次返回：空列表（未发现可复用 notebook）

#### 3.3.3 notebook exec-session（✅）

验证持久 exec session（本地 daemon 复用 Playwright + terminal 连接，用于多次快速执行命令）：

- `inspire notebook exec-session start <notebook> --cwd <dir> --env KEY=VAL` ✅
- `inspire notebook exec --session <notebook> "<cmd>"` ✅（复用会话执行）
- `inspire notebook exec-session list` ✅
- `inspire notebook exec-session stop <notebook>` ✅

注意（shell 变量展开坑）：如果要在远端命令里打印环境变量，例如 `echo E2E=$E2E`，本地 shell 会先把 `$E2E` 展开。请用 `echo E2E=\$E2E`（或用单引号）来验证远端 env 是否生效。

#### 3.3.4 新建 H100 notebook（✅）

创建使用用户指定镜像：

- `inspire notebook create -r 1xH100 -n e2e-h100-rtunnel -i base-wjx:v3 --no-auto --wait` ✅
  - 成功创建并等待 RUNNING
- `inspire notebook exec <id> "nvidia-smi -L"` ✅（确认 1 张 H100）

结论：`notebook create/exec` 在 H100 + `base-wjx:v3` 镜像下可用。


### 3.4 notebook ssh / tunnel / bridge（❌ 失败，核心诊断点）

> 这是本次评测的主要失败面：`notebook ssh` 无法建立 rtunnel server，导致 31337 不可用，从而 SSH preflight 失败。

#### 3.4.1 失败复现（notebook ssh）

对 H100 notebook（包含 `base-wjx:v3` 镜像创建的实例）执行：

```bash
inspire notebook ssh <notebook-id> \
  --command "echo ssh_ok; hostname; nvidia-smi -L | head -n 1" \
  --timeout 300 \
  --save-as e2e-h100-rtunnel
```

**稳定失败症状**（多次一致）：
- proxy readiness 检测阶段报：
  - `500 connect ECONNREFUSED 0.0.0.0:31337`
  - 或轮询 jupyter/vscode proxy URL 返回 500
- 最终报错：
  - `Error: Tunnel setup completed, but SSH preflight failed.`

这表示：CLI 已尝试完成 tunnel 初始化，但最终无法连上 SSH 端口（依赖 rtunnel）。

#### 3.4.2 已确认的关键事实

1) **notebook 内部 31337 并没有被监听**

通过 `inspire notebook exec` 在 notebook 内检查：
- `connect 127.0.0.1:31337` → `Connection refused`

结论：不是“端口转发规则没配”，而是 **notebook 内部没有 rtunnel server 在监听 31337**。

2) **CLI 输出提示 “Failed to upload rtunnel binary via Jupyter Contents API.”**

`notebook ssh` 的流程中明确出现：
- `WARNING: Failed to upload rtunnel binary via Jupyter Contents API.`

这会导致离线环境无法把 rtunnel 二进制落到 notebook。

3) **共享盘缓存出现 0 字节的 `inspire_rtunnel_bin` 文件**

在 notebook 共享目录中发现：
- `/inspire/hdd/project/publiclow/wanjiaxin-253108030048/inspire_rtunnel_bin` 存在但大小为 **0 字节**（empty）

这与“上传失败”强相关：缓存文件为空会导致后续即使脚本尝试执行也无法启动 rtunnel。

4) 即使用户已在 VSCode Web 端手动添加端口 31337，仍失败

原因是：端口转发规则只会转发“已存在且被监听的端口”；但当前 notebook 内并无进程监听 31337，所以 proxy 层仍然拒绝连接。

#### 3.4.3 tunnel 子命令评测结论

- `inspire tunnel list --no-check` ✅
  - 能看到 notebook 自动生成/写入的 bridge profile
- `inspire tunnel ssh-config` ✅
  - 能输出可用于 `~/.ssh/config` 的 Host 片段
- `inspire tunnel status -b <bridge>` ❌
  - 明确提示 `SSH: Not responding`
- `inspire tunnel test -b <bridge>` ❌
  - 超时（30s）

#### 3.4.4 bridge 子命令评测结论

- `inspire bridge exec/ssh/scp`：本次未能完成成功路径验证
  - 原因：依赖 tunnel/SSH 链路，当前链路不可用


### 3.5 Job / Run（✅ 创建成功；日志与状态查询在本环境不可用/不稳定）

#### 3.5.1 Job create（✅ 通过）

在设置 `INSPIRE_TARGET_DIR` 且使用正确镜像全路径后，成功创建 H100 job：

```bash
INSPIRE_TARGET_DIR=/inspire/hdd/global_user/wanjiaxin-253108030048 \
  inspire job create \
    -n e2e-h100-job-smoke \
    -r 1xH100 \
    -c "echo job_ok; nvidia-smi -L | head -n 1; sleep 5" \
    --no-auto \
    --image inspire-studio/base-wjx:v3 \
    --priority 1
```

结果：✅ 返回 `OK Job created: <job-id>`，并给出 Log file 路径（落在 `INSPIRE_TARGET_DIR` 下）。

> 注意：
> - 仅 `base-wjx:v3` 会报 image not found；必须用 `inspire-studio/base-wjx:v3`
> - `job create` 子命令不支持 `--json`（会报 No such option）

#### 3.5.2 Job list（✅ 通过）

- `inspire job list` ✅
  - 能从本地 cache 列出已创建 jobs（状态 PENDING）

#### 3.5.3 Job logs（❌ 失败：本环境依赖 tunnel/SSH）

- `inspire job logs <job-id> --tail 20` ❌

错误形态（与本环境“SSH 不可用”一致）：
- `Error: SSH tunnel not available for bridge '<bridge-name>'.`

结论：在当前生产环境配置下，`job logs` 走 tunnel/SSH fast-path，导致在 SSH 链路不可用时无法拉取日志。

#### 3.5.4 Job status / update（❌ 本次环境连接错误；按约束不再复测）

- `inspire job status <job-id>` ❌
- `inspire job update` ❌

错误形态：
- `Connection error, retrying in 1.0s...` 重试 3 次后失败
- `Error: Authentication request failed: Connection error after 3 retries`

说明：更像是到状态查询 API 的网络/服务临时不可达（而不是镜像/权限问题）；因为同一会话内 `job create` 和 `run` 能成功创建。

#### 3.5.5 inspire run（✅ 通过）

```bash
INSPIRE_TARGET_DIR=/inspire/hdd/global_user/wanjiaxin-253108030048 \
  inspire run "echo run_ok; nvidia-smi -L | head -n 1; sleep 3" \
    --gpus 1 --type h100 \
    --image inspire-studio/base-wjx:v3 \
    --priority 1 \
    --name e2e-run-smoke
```

结果：✅ 成功创建 job（返回 job id），并能在 `job list` 中看到。


## 4. 关键问题与诊断结论

### 4.1 P0：H 卡环境下 `notebook ssh` 无法启动 rtunnel（导致 tunnel/bridge 全链路不可用）

**直接证据**
- CLI 明确提示：`Failed to upload rtunnel binary via Jupyter Contents API.`
- notebook 内部：`127.0.0.1:31337` 连接拒绝（无监听）
- 共享盘：`inspire_rtunnel_bin` 为 0 字节 empty 文件
- proxy：稳定报 `ECONNREFUSED 0.0.0.0:31337`

**推断根因（按现象最小解释）**
- 在无网 H 卡环境中，CLI 必须通过 Jupyter Contents API 或共享盘路径将 rtunnel 二进制放入 notebook。
- 当前上传路径失败后，落到共享盘的缓存文件为空，导致 setup script 无法启动 rtunnel server。

**影响范围**
- 直接影响：`inspire notebook ssh` 失败
- 级联影响：`inspire tunnel test/status` 失败；`inspire bridge exec/ssh/scp` 无法验证成功路径


### 4.2 P1：Job logs 在本环境依赖 tunnel/SSH（导致不可用）

- `inspire job logs ...` 在本环境报 `SSH tunnel not available ...`。
- 需要确认是否存在不依赖 tunnel 的日志拉取路径（例如纯 API 拉取），或在 README 中明确该命令在无 SSH 链路时的不可用性。

### 4.3 P2：Job status/update 在本次环境出现连接错误

- 创建 job 成功，但 status/update 请求出现连接错误（重试 3 次后失败）。
- 需要进一步确认该 API endpoint 是否存在间歇性网络问题或需要额外的鉴权/代理设置。


## 5. 建议的后续行动（按优先级）

### 5.1 让 rtunnel 在 notebook 内可用（最关键）

建议从以下方向排查（不涉及 sync / image 子命令）：

1) **确认 rtunnel 二进制的“离线投放”机制**
   - 生产流程是否要求预先把 `rtunnel` 放到全局共享盘（例如 `/inspire/hdd/global_user/.../rtunnel`）？
   - CLI 是否支持/需要显式指定 `--rtunnel-bin <path>`（如果有该参数，应在 H 卡离线环境优先使用）。

2) **修复/避免产生 0 字节的 `inspire_rtunnel_bin`**
   - 当前 0 字节文件会让后续所有尝试都失败（即使端口被添加到 VSCode ports）。

3) **确认 notebook ssh 的 setup script 是否真的被执行**
   - CLI 有“Created terminal via REST API / xterm not yet visible”提示，可能存在终端通道不可用导致脚本未实际执行。

### 5.2 Job 状态轮询连接错误

- 在同一时间窗口内对 `job status` 进行多次尝试（手动/脚本化）确认是否稳定失败。
- 若稳定失败，需要抓取更详细的 endpoint/错误上下文（CLI debug 日志）定位是网络问题还是鉴权问题。


## 6. 本次评测的结论摘要

- **Notebook 核心能力（create/status/exec/stop/start）在 H100 上可用，且 `base-wjx:v3` 镜像可创建 notebook。**
- **推荐的 notebook 远程执行方式**（不依赖 SSH）：
  - `inspire notebook terminal`（交互式）
  - `inspire notebook exec`（非交互式）
  - `inspire notebook exec-session ...`（持久会话加速；注意在远端命令里打印环境变量要用 `\$VAR` 避免本地 shell 先展开）
- **Job/Run 可成功创建 H100 任务，但需要：**
  - 设置 `INSPIRE_TARGET_DIR`
  - `--image` 使用全路径 `inspire-studio/base-wjx:v3`
- **notebook ssh/tunnel/bridge 在 H100 上当前不可用**（关键阻塞点：rtunnel 二进制无法部署 → 31337 无监听 → proxy 连接拒绝）。
- **job logs 在本环境不可用**（依赖 tunnel/SSH，报 `SSH tunnel not available ...`）。
- **job status/update 在本次环境出现连接错误**（按需求不再复测）。


## 附录 A：本次涉及的关键 ID（便于复查）

> 注意：不包含 token/敏感 URL（已由 CLI 自动 redacted 或本报告省略）。

- Notebook（新建，base-wjx:v3，1xH100）：`823eec01-cc3c-4f92-a80d-a995d4d6bd56`
- Job（job create）：`job-99b475a4-2bb6-4f7e-a9ff-10f48ce31421`
- Job（run）：`job-5cb6affa-572d-402c-a024-ad019ba8c7a2`

