# Plan: 完全解耦并删除 OpenAPI 模块

## Context

Job CRUD 迁移到 Browser API 已完成（Phase 1），但：
- `run.py` 调用 `find_best_compute_group_location` 签名错误（会 **TypeError 崩溃**）
- `job_logs.py` 3 处仍用 `AuthManager.get_api()` 做状态轮询
- `config/check.py` 仍用 `AuthManager.get_api()` 做认证校验
- 整个 `inspire/platform/openapi/` 包有 10 个文件，但只有 4 个纯工具符号需要搬迁
- 多处 `except Exception: pass` 静默吞错
- 存在代码重复和死代码

## 总览：受影响文件

| 类别 | 文件数 | 操作 |
|------|--------|------|
| 搬迁纯工具 | 2 新建 + 2 修改 | GPUType, parse_resource_request, _validate_job_id_format 搬家 |
| 修复残留调用 | 4 修改 | run.py, job_logs.py, config/check.py, job_commands.py |
| 删除 AuthManager | 2 删除 + 1 修改 | auth.py 删除, utils/__init__.py 清理 |
| 删除 openapi 包 | 10 删除 | 整个 `inspire/platform/openapi/` 目录 |
| 删除测试文件 | 5 删除 | test_openapi_*, test_api_v1_endpoints, test_api_compatibility |
| 更新测试 | 1 修改 | test_cli_commands.py |
| 代码重复修复 | 4 修改 | project_commands.py, job_submit.py, job_cli.py, job_commands.py |
| 静默吞错修复 | 2 修改 | job_commands.py, job_submit.py |

## 实施步骤

### Phase 1: 搬迁纯工具符号（无 API 依赖的文件搬家）

**Step 1.1**: 创建 `inspire/platform/web/models.py`，移入 `GPUType` enum
- 从 `openapi/models.py` 复制 `GPUType` 类
- 更新 `inspire/platform/web/resources.py:15` 的 import 路径
- 验证: `uv run python -c "from inspire.platform.web.models import GPUType"`

**Step 1.2**: 创建 `inspire/cli/utils/resource_parser.py`，移入 `parse_resource_request`
- 从 `openapi/resources.py` 复制 `parse_resource_request` 函数
- 更新 `job_create.py:25` 和 `run.py` 的 import 路径
- 验证: `uv run python -c "from inspire.cli.utils.resource_parser import parse_resource_request"`

**Step 1.3**: 创建 `inspire/cli/utils/id_format.py`，移入 `_validate_job_id_format`
- 从 `openapi/errors.py` 复制函数
- 更新 `cli/utils/job_cli.py:8` 的 import 路径
- 验证: `uv run python -c "from inspire.cli.utils.id_format import _validate_job_id_format"`

### Phase 2: 修复 CRITICAL Bug — run.py

**Step 2.1**: 修复 `find_best_compute_group_location()` 调用（line 64-70）
- 移除 `api` 位置参数
- 添加 `config_compute_groups=config.compute_groups`
- 解包 4 个返回值（含 `selected_compute_group_id`）
- 导入 `parse_resource_request` 以解析 GPU type/count
- 传递 `gpu_type`, `gpu_count`, `compute_group_id` 给 `submit_training_job()`

**Step 2.2**: 移除 `AuthManager` 依赖（line 28, 138, 292）
- 删除 `api = AuthManager.get_api(config)` 调用
- 删除 `AuthManager` import
- 将 `AuthenticationError` catch 替换为 `SessionExpiredError`

**Step 2.3**: 验证
- `uv run python -c "from inspire.cli.commands.run import run"` 导入无误
- `cd /tmp && inspire run --help` 正常

### Phase 3: 修复 job_logs.py — 3 处 AuthManager 残留

**Step 3.1**: 删除 `_resolve_notebook_for_job` 中的死代码（lines 388-401）
- `api.list_notebooks()` 方法不存在于 `InspireAPI`，整个 try 块是死代码
- 简化为: 有显式 `notebook` 参数就返回，否则返回 None
- 函数重命名为 `_get_explicit_notebook_id` 以反映实际行为

**Step 3.2**: 替换 `_follow_logs_via_api` 状态检查（lines 534-568）
- `AuthManager.get_api(config)` + `api.get_job_detail(job_id)` → `browser_api_module.get_job_detail(job_id)`
- 移除 `except Exception: pass`，改为 `logger.warning(...)` 记录失败

**Step 3.3**: 替换 `_follow_logs_via_notebook` 状态检查（lines 597-599, 677）
- 同上：`api.get_job_detail(job_id)` → `browser_api_module.get_job_detail(job_id)`

**Step 3.4**: 验证
- `uv run python -c "from inspire.cli.commands.job.job_logs import logs"` 导入无误
- `cd /tmp && inspire job logs --help` 正常
- `cd /tmp && inspire job logs <id> --follow` 可正常工作（用已完成 job 测试快速退出）

### Phase 4: 修复 config/check.py

**Step 4.1**: 替换 `AuthManager.get_api(cfg)` → `get_web_session()` 认证校验
- `AuthManager.get_api(cfg)` 在 line 236 用于验证配置是否可认证
- 替换为: 尝试获取 web session，捕获 `SessionExpiredError`/`ConfigError`
- 移除 `AuthManager`, `AuthenticationError` import

**Step 4.2**: 验证
- `cd /tmp && inspire config check` 输出正确

### Phase 5: 修复 job_commands.py 和 job_create.py — AuthenticationError 引用

**Step 5.1**: `job_commands.py`
- 移除 `from inspire.cli.utils.auth import AuthenticationError` (line 26)
- 所有 `except AuthenticationError` → `except (SessionExpiredError, ConfigError)`
- 提取 `exclude_statuses` 为模块常量 `_ACTIVE_EXCLUDE_STATUSES`
- 将 `except Exception: pass` (line 141-142) → `logger.debug(...)`

**Step 5.2**: `job_create.py`
- 移除 `from inspire.cli.utils.auth import AuthenticationError` (line 19)
- `except AuthenticationError` → `except SessionExpiredError`

### Phase 6: 删除 AuthManager 和 openapi 包

**Step 6.1**: 清理 `cli/utils/__init__.py`
- 移除 `AuthManager` 和 `AuthenticationError` 的 import 和 `__all__` 条目

**Step 6.2**: 删除 `cli/utils/auth.py`

**Step 6.3**: 删除整个 `inspire/platform/openapi/` 目录（10 个文件）

**Step 6.4**: 验证
- `uv run python -c "import inspire"` 无 import error
- `grep -r "openapi" inspire/` 无残留引用
- `grep -r "AuthManager" inspire/` 无残留引用

### Phase 7: 删除旧测试文件

**Step 7.1**: 删除以下文件
- `tests/test_openapi_resource_manager.py`
- `tests/test_openapi_client_config.py`
- `tests/test_openapi_jobs.py`
- `test_api_v1_endpoints.py` (root)
- `test_api_compatibility.py` (root)

**Step 7.2**: 更新 `tests/test_cli_commands.py`
- 移除 `from inspire.platform.openapi import ResourceManager`
- 移除 `from inspire.cli.utils.auth import AuthenticationError`
- 将所有 `auth_module.AuthManager.get_api` monkeypatch → `get_web_session` mock
- 移除/重构 `DummyAPI` 类

**Step 7.3**: 验证
- `uv run pytest tests/ -x --tb=short 2>&1 | tail -20`

### Phase 8: 代码重复修复 + 静默吞错修复

**Step 8.1**: `project_commands.py` — 删除 `_project_info_to_dict`，统一用 `_project_to_dict`

**Step 8.2**: `job_submit.py` — 提取共享的项目解析逻辑
- 将 `select_project_for_workspace` 和 `select_project_for_job` 中重复的 alias 解析部分提为 `_resolve_project_requested_value()`
- 从 `__all__` 中移除 `select_project_for_workspace`（无外部调用方）

**Step 8.3**: `job_cli.py` — 提取 `_strip_job_prefix()` 工具函数消除 `uuid_part` 重复

**Step 8.4**: `job_submit.py` — 为 3 处 `except Exception: pass` 添加 `logger.debug/warning`

### Phase 9: 端到端验证

从 `/tmp` 执行所有命令（除 image create/delete）:
1. `inspire --help` / `inspire config show` / `inspire config check`
2. `inspire resources list` / `inspire resources allocate`
3. `inspire project list` / `inspire project list --json`
4. `inspire job list` / `inspire job status <id>` / `inspire job command <id>` / `inspire job update` / `inspire job wait <id> --timeout 5`
5. `inspire job logs <id> --path` / `inspire job logs <id>`
6. `inspire notebook list` / `inspire notebook list --json`
7. `inspire notebook status <id> --json`
8. `inspire notebook exec <id> "echo test"`
9. `inspire notebook terminal <id>` (验证连接)
10. `inspire notebook reusable -r 1xH200`
11. `inspire image list --source all`
12. `inspire image detail <id>`
13. `inspire run --help` (确保命令可用)
14. `uv run pytest tests/ -x --tb=short` (通过测试)
