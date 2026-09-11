# ResolveFlow — 售后 AI Agent 运营工作台

当前唯一版本：3.0。基于 DeepSeek、LangGraph、MCP、Skills 和 PostgreSQL，提供浏览器运营界面、异步任务与人工审批。正式入口是 `operations:app` 与独立 `worker.py`；旧同步 SQLite API 已移除。

## 快速使用

项目路径：`D:\AgentProjects\ResolveFlow`。完整操作、架构、权限与部署说明见 [ENTERPRISE_V3.md](ENTERPRISE_V3.md)。

```powershell
cd D:\AgentProjects\ResolveFlow
.\start-local.ps1
```

打开 http://127.0.0.1:8003/ 。授权码从本机 `.env` 读取：APP_API_KEY 为运营，REVIEWER_API_KEY 为审批，ADMIN_API_KEY 为管理员。使用“切换角色”登录其他角色。不要把 `.env` 提交到 Git。

首次在其他机器运行：安装 Python 3.12，创建 `.venv`，安装 `requirements.lock`，根据 `.env.example` 配置模型、数据库和三个不同的授权码。便携 PostgreSQL 和本机虚拟环境不包含在源码包中。可用 Docker 环境下运行 `docker compose up --build -d --wait` 启动 PostgreSQL、API、Worker。

## 核心功能

- 真实模型调查，MCP 只读查询政策/订单，按需加载应用 Skills。
- PostgreSQL 保存工单、检查点、任务、审批、审计和模拟退款台账。
- 条件全满足自动模拟退款，全不满足自动拒绝，部分满足转人工；事实冲突/缺失优先人工核查。
- 独立 Worker 执行持久化队列，失败退避重试、管理员重试、进程中断恢复、退款幂等。
- 运营/审批/管理员三种角色的后端权限校验。
- 工单工作台、自动刷新、异常重试、任务耗时/失败/心跳监控，API 提供 token 汇总。

## 验证状态

- 基础测试覆盖规则、Graph、MCP、Skills 和 SQLite 隔离测试路径。
- PostgreSQL 集成测试覆盖权限、队列、重试、进程终止恢复和退款提交后重放；发布前应在可用 PostgreSQL 环境重新执行。
- 12 条离线合成案例的历史结果见 `evaluation_v3.json`；不是生产准确率。
- 3 条真实 DeepSeek 异步工单的历史结果见 `async_live_report.json`。
- Compose 配置已通过静态校验；容器构建、运行和命名卷持久化仍需在 Docker Engine 可用的环境验收。
- GitHub Actions 工作流会运行基础测试、PostgreSQL 集成测试、离线评测和容器冒烟验证。

所有订单为合成数据，退款仅记入模拟台账。角色授权码不是完整的个人账号体系；尚未接入实际电商订单、支付退款和云端生产运维。

详细架构、运行方式与限制见 [ENTERPRISE_V3.md](ENTERPRISE_V3.md)。
