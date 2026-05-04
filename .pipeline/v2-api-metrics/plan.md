# Plan: Inspire-cli v2 API + Metrics + 增强功能

## Context

Inspire-cli 是 SII 启智平台的 Python CLI 工具，目前仅使用 v1 Browser API + Playwright SSO Cookie 认证。holos-inspire 是同一平台的 TypeScript Synergy 插件，使用 v2 API + Keycloak Bearer Token 认证，功能更丰富。

目标：为 Inspire-cli 接入 v2 API 认证体系，添加 GPU 指标监控，并移植 holos-inspire 中的高价值功能。

## 设计原则

1. **向后兼容**: 不修改任何 v1 API 函数签名，v2 代码独立在 `v2_api/` 包中
2. **双认证策略**: v2 Token 优先，失败时回退到 v1 Cookie 认证
3. **渐进增强**: 现有命令通过 `--v2` 标志选择 v2 API，默认自动探测
4. **无新依赖**: 仅使用已有的 `requests` 库，不引入 `httpx`/`aiohttp` 等

## 实现计划（6 个 Phase，按依赖顺序）

### Phase 1: v2 API 认证基础设施

**新建文件：**

1. `inspire/platform/web/v2_api/__init__.py` — 包入口，导出公共符号
2. `inspire/platform/web/v2_api/auth.py` — Token 获取/缓存/刷新
   - `V2TokenSet` (frozen dataclass): access_token, access_expires_at
   - `get_token(base_url, username, password) -> str` — POST `{base_url}/auth/token`，参考 `test_all_endpoints.py:33-40`
   - `load_cached_token() -> V2TokenSet | None` — 从 `~/.cache/inspire-cli/v2_token.json` 读取
   - `save_token(token_set) -> None` — 写入缓存文件 (chmod 0o600)
   - `clear_token() -> None`
   - `ensure_token(config) -> str` — 缓存有效则返回，否则重新获取
3. `inspire/platform/web/v2_api/client.py` — 通用 v2 HTTP 客户端
   - `V2_HEADERS`: Content-Type, Accept, x-inspire-client-source
   - `post_v2(service, action, body, token, base_url, timeout=30) -> dict` — POST `/api/v2/{service}?Action={action}`
   - 错误处理：401/403/ResponseMetadata.Error → `V2ApiError`
4. `inspire/platform/web/v2_api/models.py` — 共享数据模型
   - `V2JobInfo`, `V2InferenceInfo`, `MetricTimeSeries`, `MetricSummary`, `IdleWindow`, `StatusFamily`, `TimelineAnalysis`

**修改文件：**

5. `inspire/config/models.py` — 添加 `v2_enabled: bool = True` 字段
6. `inspire/config/options/api.py` — 添加 `INSPIRE_V2_ENABLED` 环境变量映射
7. `inspire/platform/web/session/__init__.py` — 添加 `get_v2_token(config) -> str | None` 辅助函数

**验证**: 单元测试 `POST /auth/token` 调用，token 缓存读写

---

### Phase 2: v2 API 服务层

**新建文件：**

1. `inspire/platform/web/v2_api/train.py` — v2 训练作业 API
   - `list_jobs(token, base_url, workspace_id, ...) -> tuple[list[V2JobInfo], int]`
   - `get_job_detail(token, base_url, job_id) -> dict`
   - `stop_job(token, base_url, job_id) -> None`
   - `get_job_logs(token, base_url, job_id, instance_count, ...) -> tuple[list[dict], int]`
   - `get_task_metrics(token, base_url, compute_group_id, task_id, metric_types, ...) -> list[dict]`
2. `inspire/platform/web/v2_api/workspace.py` — v2 工作空间 API
   - `get_basic_info(token, base_url, workspace_id) -> dict`
   - `list_node_dimension(token, base_url, workspace_id, ...) -> list[dict]`
   - `list_resource_specs(token, base_url, workspace_id, compute_group_id, ...) -> list[dict]`
3. `inspire/platform/web/v2_api/inference.py` — v2 推理服务 API
   - `list_inference(token, base_url, workspace_id, ...) -> tuple[list[V2InferenceInfo], int]`
   - `get_inference_detail(token, base_url, serving_id) -> dict`
   - `create_inference(token, base_url, config) -> dict`
   - `stop_inference(token, base_url, serving_id) -> None`

**修改文件：**

4. `inspire/config/models.py` — 确认 Config 中有 `base_url` 字段供 v2 client 使用

**验证**: 单元测试各 API 函数的响应解析和错误处理

---

### Phase 3: 状态归一化 + 增强作业列表/详情

**新建文件：**

1. `inspire/cli/utils/status_normalizer.py` — 状态归一化（参考 holos `normalize.ts`）
   - `normalize_status(raw) -> StatusFamily` — v2 数字码(1-8)/v1 字符串 → running/waiting/succeeded/failed/stopped/unknown
   - `analyze_timeline(timeline) -> TimelineAnalysis` — 排队时间/运行时间/是否从未启动
   - `format_duration(ms) -> str`

**修改文件：**

2. `inspire/cli/commands/job/job_commands.py`
   - `list_jobs`: 添加 `--v2` 标志，使用 v2 `ListJobs` API 获取实时数据
   - 新增 `detail` 子命令: 富文本作业详情 + 失败诊断（从未启动/镜像拉取失败/spec_id 不匹配/命令错误/NCCL 问题/网络操作检测/排队过久）
3. `inspire/cli/formatters/human_formatter.py` — 添加 `format_job_detail()`, `format_timeline()`, `format_diagnostics()`
4. `inspire/cli/formatters/json_formatter.py` — 确认支持 detail 输出的 JSON 序列化

**验证**: CliRunner 测试 `job list --v2` 和 `job detail <id>`

---

### Phase 4: GPU 指标监控

**新建文件：**

1. `inspire/cli/commands/metrics/__init__.py` — Click group `metrics`
2. `inspire/cli/commands/metrics/metrics_show.py` — `metrics show <job-id>` 命令
   - `--time-range`: 5m/15m/30m/1h/3h/6h
   - `--mode`: summary(统计摘要)/raw(原始时序数据)
   - `--interval`: 采样间隔(秒)
   - 查询 4 类指标: `gpu_usage_rate`, `gpu_memory_usage_rate`, `cpu_usage_rate`, `memory_usage_rate`
   - Summary 输出: avg, p50, p90, min, max, stddev, trend, idle_windows
3. `inspire/cli/commands/metrics/metrics_health.py` — `metrics health <job-id>` 命令
   - 健康评估（参考 holos `metrics.ts:assessHealth`）:
     - "卡住" (stuck): GPU avg < 0.1, 无上升趋势
     - "预热中" (preheating): GPU avg < 0.1, 上升趋势, P90 > 0.3
     - "CPU瓶颈": CPU avg > 0.7, GPU 低
     - "显存紧张": GPU mem P90 > 0.9
     - "内存紧张": RAM avg > 0.9
     - "正常": GPU 0.3-1.0, 稳定
     - "无数据": 指标为空
   - 趋势分析: rising/falling/stable + 幅度
   - 空闲窗口检测 (GPU < 5%)
4. `inspire/cli/utils/metrics_utils.py` — 统计计算
   - `compute_summary(values) -> MetricSummary`
   - `detect_idle_windows(values, threshold) -> list[IdleWindow]`
   - `compute_trend(values) -> tuple[str, float]`
   - `assess_health(gpu, gpu_mem, cpu, mem) -> tuple[str, list[str]]`

**修改文件：**

5. `inspire/cli/formatters/human_formatter.py` — 添加 `format_metric_summary()`, `format_health_assessment()`
6. `inspire/cli/main.py` — 注册 `metrics` 命令组
7. `inspire/cli/commands/__init__.py` — 导出 `metrics`

**验证**: CliRunner 测试 `metrics show` 和 `metrics health`，单元测试统计函数

---

### Phase 5: 推理服务管理

**新建文件：**

1. `inspire/cli/commands/inference/__init__.py` — Click group `inference`
2. `inspire/cli/commands/inference/inference_commands.py`
   - `create`: --name, --image, --model-id, --model-version, --command, --port, --replicas, --nodes-per-replica, --spec-id, --workspace, --project, --priority
   - `stop <serving-id>`
   - `list`: --workspace, --limit
   - `detail <serving-id>`

**修改文件：**

3. `inspire/cli/main.py` — 注册 `inference` 命令组
4. `inspire/cli/commands/__init__.py` — 导出 `inference`
5. `inspire/cli/formatters/human_formatter.py` — 添加 `format_inference_list()`, `format_inference_detail()`

**验证**: CliRunner 测试 CRUD 命令

---

### Phase 6: 批量停止 + 其他增强

**修改文件：**

1. `inspire/cli/commands/job/job_commands.py`
   - `stop` 命令增强:
     - 单作业停止: `stop <job-id>` (现有行为不变)
     - 批量停止: `stop --workspace <ws> --status <running|waiting|all> --project <p>` 
     - `--dry-run` 预览模式
     - 按 `classifyJobId` 前缀自动路由到正确的停止 API (train/HPC/inference)
2. `inspire/cli/commands/job/job_logs.py`
   - 添加 `--v2` 标志使用 `GetJobLog` API (支持时间范围过滤)
3. `inspire/cli/formatters/human_formatter.py` — 添加 `format_batch_stop_results()`
4. `inspire/cli/commands/resources/resources_nodes.py`
   - 可选：使用 v2 `ListNodeDimension` 获取更丰富的节点数据

**验证**: CliRunner 测试批量停止、dry-run 模式、部分失败报告

---

## 新增 CLI 命令总览

```
inspire
  metrics                           # NEW
    show <job-id> [--time-range] [--mode] [--interval]
    health <job-id> [--time-range]
  inference                         # NEW
    create --name --image --model-id [opts]
    stop <serving-id>
    list [--workspace] [--limit]
    detail <serving-id>
  job
    list [--v2] [--workspace]       # enhanced
    detail <job-id>                 # NEW
    stop [job-id] [--workspace --status --dry-run]  # enhanced
    logs [--v2]                     # enhanced
```

## 新增文件清单 (共 16 个)

| 文件 | Phase |
|------|-------|
| `inspire/platform/web/v2_api/__init__.py` | 1 |
| `inspire/platform/web/v2_api/auth.py` | 1 |
| `inspire/platform/web/v2_api/client.py` | 1 |
| `inspire/platform/web/v2_api/models.py` | 1 |
| `inspire/platform/web/v2_api/train.py` | 2 |
| `inspire/platform/web/v2_api/workspace.py` | 2 |
| `inspire/platform/web/v2_api/inference.py` | 2 |
| `inspire/cli/utils/status_normalizer.py` | 3 |
| `inspire/cli/utils/metrics_utils.py` | 4 |
| `inspire/cli/commands/metrics/__init__.py` | 4 |
| `inspire/cli/commands/metrics/metrics_show.py` | 4 |
| `inspire/cli/commands/metrics/metrics_health.py` | 4 |
| `inspire/cli/commands/inference/__init__.py` | 5 |
| `inspire/cli/commands/inference/inference_commands.py` | 5 |
| `tests/test_v2_auth.py` | 1 |
| `tests/test_metrics_utils.py` | 4 |

## 修改文件清单 (共 9 个)

| 文件 | Phase |
|------|-------|
| `inspire/config/models.py` | 1 |
| `inspire/config/options/api.py` | 1 |
| `inspire/platform/web/session/__init__.py` | 1 |
| `inspire/cli/main.py` | 4, 5 |
| `inspire/cli/commands/__init__.py` | 4, 5 |
| `inspire/cli/commands/job/job_commands.py` | 3, 6 |
| `inspire/cli/commands/job/job_logs.py` | 6 |
| `inspire/cli/formatters/human_formatter.py` | 3, 4, 5, 6 |

## 验证方式

1. **单元测试**: 每个新模块的纯函数测试 (auth, client, normalizer, metrics_utils)
2. **CLI 集成测试**: 使用 `click.testing.CliRunner` + `monkeypatch` mock v2 API 调用
3. **JSON 输出验证**: 所有新命令支持 `--json`，验证输出结构
4. **向后兼容**: 现有测试套件必须全部通过，确保 v1 功能不受影响
5. **手动端到端**: 在真实集群上测试 `inspire metrics show <real-job-id>` 和 `inspire inference list`
