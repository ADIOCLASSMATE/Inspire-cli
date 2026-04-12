# Inspire CLI 端到端测试报告

**测试日期**: 2026-04-12
**测试环境**: macOS Darwin 25.3.0, Python 3.14
**测试目录**: ~ (Home 目录，非项目目录)
**CLI 版本**: inspire-cli 0.2.4

---

## 测试总结

| 类别 | 通过 | 失败 | 备注 |
|------|------|------|------|
| 基础命令 | 3/3 | 0 | |
| 配置 | 1/1 | 0 | |
| 资源查询 | 2/2 | 0 | |
| Job 管理 | 6/6 | 0 | |
| Notebook 管理 | 5/5 | 0 | |
| 日志功能 | 2/2 | 0 | |
| 已移除功能 | 3/3 | 0 | 全部正确拦截 |
| **总计** | **22/22** | **0** | |

---

## 详细测试结果

### 1. 基础命令

| 命令 | 结果 | 输出 |
|------|------|------|
| `inspire --help` | PASS | 正常显示帮助，无 bridge/tunnel 命令 |
| `inspire --version` | PASS | 显示版本号 |
| `inspire <invalid>` | PASS | 正确报错 |

### 2. 配置

| 命令 | 结果 | 说明 |
|------|------|------|
| `inspire config show` | PASS | 正确读取 ~/.config/inspire/config.toml，显示认证信息、API地址、路径等 |

### 3. 资源查询

| 命令 | 结果 | 说明 |
|------|------|------|
| `inspire resources list` | PASS | 显示实时 GPU 可用性（H100/H200），含可用数、已用数、低优先级数 |
| `inspire project list` | PASS | 显示项目列表，含优先级和预算余额 |

### 4. Job 管理

| 命令 | 结果 | 说明 |
|------|------|------|
| `inspire job list` | PASS | 从本地缓存列出作业，含状态和时间 |
| `inspire job status <job_id>` | PASS | 查询 API 获取实时作业状态，含创建/完成时间 |
| `inspire job command <job_id>` | PASS | 显示作业的完整执行命令 |
| `inspire job wait <job_id>` | PASS | 等待作业完成，已完成的作业立即返回 |
| `inspire job update` | PASS | 轮询 API 更新缓存中的作业状态 |
| `inspire run -n e2e-after-cleanup --type H100 -g 1 -p 公共兜底 --priority 1 "echo hello"` | PASS | 成功创建作业，返回 job ID 和日志路径 |

### 5. Notebook 管理

| 命令 | 结果 | 说明 |
|------|------|------|
| `inspire notebook list` | PASS | 列出所有 notebook 实例，含状态/项目/资源/ID |
| `inspire notebook list --json` | PASS | JSON 格式输出，数据完整 |
| `inspire notebook exec dev-h200 "hostname"` | PASS | 返回主机名 `dev-h200--13dd92348f3a-cuwmkbay7w` |
| `inspire notebook exec dev-h200 "nvidia-smi --query-gpu=name,memory.used --format=csv,noheader"` | PASS | 返回 8x H200 GPU 信息，每张显存 45024 MiB |
| `inspire notebook exec dev-h200 "ls /workspace" --json` | PASS | JSON 格式输出，含 exit_code 字段 |

### 6. 日志功能

| 命令 | 结果 | 说明 |
|------|------|------|
| `inspire job logs <job_id> --path` | PASS | 显示远程日志路径 |
| `inspire job logs <job_id> --tail 5 --notebook dev-h200` | PASS | 通过 notebook exec 获取日志内容，正确显示尾部行 |

### 7. 已移除功能（正确拦截）

| 命令 | 结果 | 说明 |
|------|------|------|
| `inspire notebook ssh <notebook>` | PASS | 显示 "has been removed"，建议使用 notebook exec/terminal |
| `inspire notebook top` | PASS | 显示 "has been removed"，建议使用 nvidia-smi |
| `inspire sync` | PASS | 显示 "has been removed"，建议使用 rsync/scp |

---

## 已知问题

1. **连接重试提示**: `inspire job logs --path` 首次连接时偶尔出现 "Connection error, retrying"，重试后成功。可能与 API 服务冷启动有关。

2. **短镜像名不支持**: `--image dev-wjx:v1.1` 无法识别，需要完整 URL `docker.sii.shaipower.online/inspire-studio/dev-wjx:v1.1`。这是平台 API 限制而非 CLI bug。

---

## 本次修复

### Playwright 异步清理异常 — 已修复

**问题**: 使用 Playwright 的命令（notebook list、resources list）完成后抛出 `AttributeError: 'NoneType' object has no attribute 'switch'`，这是 Playwright Python SDK 的已知 bug — `sync_playwright().stop()` 后 asyncio event loop 仍有 pending callback 试图 switch 到已销毁的 greenlet。

**修复**: 在 `inspire/cli/main.py` 的 `cli()` 入口函数中，退出前设置自定义 asyncio exception handler 抑制此已知错误；同时在 `inspire/platform/web/session/browser_client.py` 的 `close()` 方法中也做了同样处理。修复后所有 Playwright 命令输出干净无异常。

---

## 本次重构变更验证

删除 SSH/tunnel/rtunnel/forge 代码后，以下功能确认正常：

- notebook exec（WebSocket 执行）: 正常
- notebook terminal: 命令注册正常
- job create/run: 正常
- job logs（通过 notebook exec 读取）: 正常
- job status/list/wait/update: 正常
- 所有已移除命令（ssh/top/sync）: 正确拦截并提示替代方案
