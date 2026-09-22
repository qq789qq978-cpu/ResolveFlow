# ResolveFlow V3 — 售后 AI Agent 工作台

基于 LangGraph、DeepSeek、MCP 和 PostgreSQL，支持异步 Worker、可恢复人工审批、政策文档 RAG、三角色权限和模拟退款幂等。浏览器界面由 FastAPI 同源提供。

[![ResolveFlow checks](https://github.com/qq789qq978-cpu/ResolveFlow/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/qq789qq978-cpu/ResolveFlow/actions/workflows/ci.yml?query=branch%3Amain)

当前完成 [2.11真实生成模型评测](validation/step-2.11-2026-09-22/REPORT.md)：80条RAG真实评测（80/80合法结构化输出）及12条合成业务执行完成；保留集有据引用覆盖16/20（80.0%），业务授权边界12/12符合预期、模拟退款0条。280次DeepSeek调用，按高峰/输入无缓存计费的保守估算3.103362元（上限10元，非账单）。引用匹配不等于回答语义正确，人工复核仍待办。8003保持本地E5+BM25/RRF、demo，4服务健康、19表及镜像配置不变；PDF解析和reranker仍暂缓。下一步2.12阶段收尾、统一推送并核对CI，上方徽章只代表远程main历史状态。第一阶段见 [1.10报告](validation/step-1.10-2026-09-21/REPORT.md)。

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

打开 http://127.0.0.1:8003/，输入角色授权码。默认BM25的三个服务应均为 healthy；显式启用semantic profile时还有embedding服务。API 和 Worker 使用 `db:5432` 的容器数据库；Compose 不使用 `.env` 中供本机 Python 使用的 `DATABASE_URL`。

本机源码在 `D:/AgentProjects/ResolveFlow`，Docker 程序在 `D:/Programs/DockerDesktop`，Docker 数据在 `D:/DockerData`；其他机器无需沿用这些路径。本机 Python/便携 PostgreSQL 运行方式见 [运行说明](ENTERPRISE_V3.md)。

## demo 与 live

- demo：真实 PostgreSQL、MCP、文档检索、队列和审批，以规则生成演示建议，不调用生成模型、不消耗 DeepSeek token；hybrid模式会执行本地embedding推理。
- live：额外调用模型调查和生成建议，需配置有效的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`MODEL_NAME`。这几个兼容接口变量在本项目中用于 DeepSeek，不是 Codex 登录配置。
- 内置政策审核来源为demo_fixture，live模式不可直接采用；需维护人员完成实际审核、声明来源与有效期并显式导入，见 [政策治理](POLICY_GOVERNANCE.md)。
- 两种模式都只使用合成订单和模拟退款，不调用真实支付。

确认模型配置后，可设置 `$env:MODE='live'` 再运行 `docker compose up -d --wait`。API 与 Worker 必须同模式；切换前先处理完旧模式的排队任务。主环境容器验收使用demo；2.11已在隔离QA中单独完成付费生成评测，见 [协议](LIVE_MODEL_EVALUATION.md) 和 [运行说明](LIVE_MODEL_EVALUATION_RUNBOOK.md)，未切换主环境到live。

## 演示功能

- RF-1001：两项条件均符合，自动模拟退款；再次处理拦截重复退款。
- RF-1002：两项条件均不满足，自动拒绝。
- RF-1004：部分条件满足，审批员同意或拒绝后恢复原工单。
- 缺失事实、政策证据不足或事实冲突：转人工核查。
- 页面“政策知识库”可查询政策；工单显示来源文件、版本、行号和原文。
- 管理员可查看心跳、耗时、失败情况，并重试耗尽自动重试次数的任务。

RAG 采用 Markdown 导入、分块和 PostgreSQL 存储；默认BM25可独立运行，可选本地embedding混合检索，故障时回退BM25。详见 [RAG.md](RAG.md)。

## 持久化与验证

已实际运行 Docker Compose，并完成保留命名卷的容器重建、数据库内容核对与待审批工单恢复。复验脚本会新增一条合成工单，重建容器并比较业务表、checkpoint 和知识库索引，最后拒绝该工单的例外退款：

```powershell
# 需本机 Python 和 requirements.lock 依赖；服务须先以 demo 启动
python verify_persistence.py --report validation/persistence.json
```

**不要执行 `docker compose down -v`，它会删除数据卷。** 停止服务用 `docker compose down`，再启动用 `docker compose up -d --wait`。命名卷不等于备份，正常重建验收不等于强制断电或云端容灾验收。

测试与证据见 [VALIDATION.md](VALIDATION.md)。前端异步响应与断线回归可用 Node 22+ 执行 `node --test test_frontend.cjs`，无须安装 npm 依赖；运行容器应用本身不需要 Node。GitHub Actions 配置包含前端测试、基础测试、PG 集成、业务/RAG 评测、容器业务流和重建恢复；当前远程结果以 [Actions](https://github.com/qq789qq978-cpu/ResolveFlow/actions) 对应提交为准。

调查中真实Worker强杀与恢复已完成 [步骤1.5验收](validation/step-1.5-2026-09-21/REPORT.md)。复验时先确保主环境以demo运行、现有 `resolveflow:local` 镜像与主环境一致，8006端口可用，再执行：

```powershell
python scripts/worker_crash_qa.py --report validation/worker-crash-recheck.json
```

宿主Python只需标准库。脚本使用固定独立Compose项目 `resolveflow-qa-step15` 和公开测试凭据，不读取主环境 `.env`，不构建镜像；在真实MCP调查查询中强杀测试Worker，显式启动后检查原任务恢复及重复退款拦截。主环境只读取数据指纹。结束时停止QA并保留卷；复验新增两条合成工单，已有退款会按幂等路径验证，报告路径须未存在。1.10已将1.5–1.9串行故障验收加入CI，并上传各场景JSON证据。

审批保存后强杀与恢复已完成 [步骤1.6验收](validation/step-1.6-2026-09-21/REPORT.md)。相同前置条件，8007可用时运行：

```powershell
python scripts/worker_crash_qa.py --step 1.6 --report validation/approval-crash-recheck.json
```

此选项固定使用 `resolveflow-qa-step16` 独立环境，依次验证拒绝/同意已保存、Worker领取任务后强杀、按原决定恢复、重复/反向审批409及后续工单重复退款拦截。每次新增三条合成工单和审批，结束后停止QA保留卷。

退款提交后、checkpoint保存前的重放已完成 [步骤1.7验收](validation/step-1.7-2026-09-21/REPORT.md)。相同前置条件，8008可用时运行：

```powershell
python scripts/worker_crash_qa.py --step 1.7 --report validation/refund-replay-recheck.json
```

固定使用 `resolveflow-qa-step17`，每次新增两张合成订单、四条工单和两笔模拟退款。测试库临时触发器控制提交窗口；强杀Worker后，还会终止被该测试锁阻塞的checkpoint写连接，确保它不能在解锁后补写完成。恢复前删除测试触发器与函数，重启后验证自动/人工批准两条路径重放和退款幂等。原工单显示already_refunded表示重放的再次插入被拦截，首次退款仍属于原工单。主环境只读指纹，结束后QA停止、卷及历史保留。

PostgreSQL短时停机后的恢复已完成 [步骤1.8验收](validation/step-1.8-2026-09-21/REPORT.md)。相同前置条件，8009可用时运行：

```powershell
python scripts/database_outage_qa.py --report validation/database-outage-recheck.json
```

固定使用 `resolveflow-qa-step18`，分别在调查和审批任务已领取时停止/启动其PostgreSQL，保持API和Worker进程不变。每次新增三条工单、两条审批；已有RF-1004退款时验证台账不变。数据库不可用期间接口返回503；Worker重建失效checkpoint连接，数据库连接故障不消耗工单的业务重试次数，普通业务异常仍最多重试3次。结束后停止QA保留卷；服务重启造成的连接断开不等于静默丢包或任意网络故障已全部覆盖。

## 多 Worker 与超时边界

步骤1.9的双Worker故障验收脚本使用独立 `resolveflow-qa-step19`（8010），主环境仅做只读数据指纹比较：

```powershell
python scripts/multi_worker_qa.py --report validation/multi-worker-recheck.json
```

需要已有 `resolveflow:local` 镜像和运行中的demo主环境。脚本新增两张合成订单、五条工单，验证一个Worker停滞时另一个继续处理、同订单并发退款唯一、MCP数据库查询超时、三次失败后的管理员恢复。两个真实Worker使用Compose的 `--scale worker=2`；结束时删除测试触发器并停止QA，保留全部卷和历史。不要与其他使用同一QA数据库的测试并行运行。

管理员重试遇到任务锁立即返回409；普通失败从失败时刻起等待2秒、4秒再重试。MCP子进程的单条SQL限制10秒，早于MCP读取15秒和会话30秒限制，避免子进程退出后数据库查询继续等锁。这些是局部超时，不是整张工单的执行期限；退款写入、checkpoint等连接目前没有统一SQL超时。长事务持锁期间心跳和健康检查仍可正常，不能据此判断任务有进展。整体超时和停滞告警继续安排在3.7、3.8，实测边界见 [1.9报告](validation/step-1.9-2026-09-21/REPORT.md)。

## 第一阶段回归入口

主环境以demo运行、`resolveflow:local`镜像与其一致、8006–8010端口可用时，可一次串行复验1.5–1.9：

```powershell
python scripts/stage1_qa.py --reports validation/stage1-recheck
```

使用新的报告目录；不要与使用相同QA项目的脚本同时运行。各场景仍使用独立数据库，正常结束后停止QA、保留卷和历史。入口不重演浏览器操作；浏览器证据保留在1.1–1.4，前端延迟/断线回归由 `node --test test_frontend.cjs` 执行。

CI按顺序运行前端、基础/MCP、PG集成、离线业务/RAG、全新镜像构建、HTTP权限/业务流、容器重建恢复及上述真实故障验收。Actions中的 `evaluation` 附件包含业务/RAG报告、重建证据及五个故障场景结果。42项基础测试、14项PG集成、12项前端测试与故障断言属于不同验证层次，不代表生产准确率或容量保证；故障断言数会因是否已有历史退款而略有区别。

## 交接与计划

- [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md)：架构、恢复设计和已知问题。
- [ROADMAP.md](ROADMAP.md)：可靠性、RAG 质量、迁移备份与求职展示。
- [ENTERPRISE_V3.md](ENTERPRISE_V3.md)：权限、监控及运行细节。

当前是本机可部署的求职演示项目。共享角色码不是个人账号体系；未接真实商户订单、真实支付，也未完成公网生产部署。
