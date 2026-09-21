# ResolveFlow V3 — 售后 AI Agent 工作台

基于 LangGraph、DeepSeek、MCP 和 PostgreSQL，支持异步 Worker、可恢复人工审批、政策文档 RAG、三角色权限和模拟退款幂等。浏览器界面由 FastAPI 同源提供。

## Docker 快速启动

前置条件：Git、已启动的 Docker Engine / Docker Desktop，以及 Compose。首次获取私有仓库：

```powershell
git clone https://github.com/qq789qq978-cpu/ResolveFlow.git
cd ResolveFlow
Copy-Item .env.example .env
```

编辑 `.env`，设置 `POSTGRES_PASSWORD` 和三个不同的随机角色授权码：`APP_API_KEY`、`REVIEWER_API_KEY`、`ADMIN_API_KEY`。数据库密码建议用 URL-safe 字符。已有 `.env` 时不要覆盖。

```powershell
$env:MODE='demo'
docker compose up --build -d --wait
docker compose ps
```

打开 http://127.0.0.1:8003/，输入角色授权码。三个服务应均为 healthy。API 和 Worker 使用 `db:5432` 的容器数据库；Compose 不使用 `.env` 中供本机 Python 使用的 `DATABASE_URL`。

本机源码在 `D:/AgentProjects/ResolveFlow`，Docker 程序在 `D:/Programs/DockerDesktop`，Docker 数据在 `D:/DockerData`；其他机器无需沿用这些路径。本机 Python/便携 PostgreSQL 运行方式见 [运行说明](ENTERPRISE_V3.md)。

## demo 与 live

- demo：真实 PostgreSQL、MCP、文档检索、队列和审批，以规则生成演示建议，不调用模型、不消耗 DeepSeek token。
- live：额外调用模型调查和生成建议，需配置有效的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`MODEL_NAME`。这几个兼容接口变量在本项目中用于 DeepSeek，不是 Codex 登录配置。
- 两种模式都只使用合成订单和模拟退款，不调用真实支付。

确认模型配置后，可设置 `$env:MODE='live'` 再运行 `docker compose up -d --wait`。API 与 Worker 必须同模式；切换前先处理完旧模式的排队任务。本次容器验收使用 demo，没有付费模型请求。

## 演示功能

- RF-1001：两项条件均符合，自动模拟退款；再次处理拦截重复退款。
- RF-1002：两项条件均不满足，自动拒绝。
- RF-1004：部分条件满足，审批员同意或拒绝后恢复原工单。
- 缺失事实、政策证据不足或事实冲突：转人工核查。
- 页面“政策知识库”可查询政策；工单显示来源文件、版本、行号和原文。
- 管理员可查看心跳、耗时、失败情况，并重试耗尽自动重试次数的任务。

RAG 采用 Markdown 导入、分块、PostgreSQL 存储和 BM25 文本检索，不依赖 embedding 服务。详见 [RAG.md](RAG.md)。

## 持久化与验证

已实际运行 Docker Compose，并完成保留命名卷的容器重建、数据库内容核对与待审批工单恢复。复验脚本会新增一条合成工单，重建容器并比较业务表、checkpoint 和知识库索引，最后拒绝该工单的例外退款：

```powershell
# 需本机 Python 和 requirements.lock 依赖；服务须先以 demo 启动
python verify_persistence.py --report validation/persistence.json
```

**不要执行 `docker compose down -v`，它会删除数据卷。** 停止服务用 `docker compose down`，再启动用 `docker compose up -d --wait`。命名卷不等于备份，正常重建验收不等于强制断电或云端容灾验收。

测试与证据见 [VALIDATION.md](VALIDATION.md)。前端异步响应与断线回归可用 Node 22+ 执行 `node --test test_frontend.cjs`，无须安装 npm 依赖；运行容器应用本身不需要 Node。GitHub Actions 配置包含前端测试、基础测试、PG 集成、业务/RAG 评测、容器业务流和重建恢复；当前远程结果以 [Actions](https://github.com/qq789qq978-cpu/ResolveFlow/actions) 对应提交为准。

## 交接与计划

- [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md)：架构、恢复设计和已知问题。
- [ROADMAP.md](ROADMAP.md)：可靠性、RAG 质量、迁移备份与求职展示。
- [ENTERPRISE_V3.md](ENTERPRISE_V3.md)：权限、监控及运行细节。

当前是本机可部署的求职演示项目。共享角色码不是个人账号体系；未接真实商户订单、真实支付，也未完成公网生产部署。
