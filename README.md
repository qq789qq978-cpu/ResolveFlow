# ResolveFlow · 基于 LangGraph 的售后智能工单系统

[![ResolveFlow checks](https://github.com/qq789qq978-cpu/ResolveFlow/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/qq789qq978-cpu/ResolveFlow/actions/workflows/ci.yml?query=branch%3Amain)

面向电商售后的可恢复 Agent 工作台，串联订单调查、政策检索、规则决策、人工审批与模拟退款。运营提交诉求后，后台异步执行；需要审批的工单保存现场，审批后继续原流程，处理依据和操作记录全程可追溯。

**本地演示版已完成交付。** 提供个人账号、运营／审批／管理员三类业务角色、双工作区隔离，以及可复现的部署、故障验收和备份恢复流程。默认使用合成订单与规则演示模式，无需模型 API 密钥。

**技术栈：** Python · FastAPI · LangGraph · PostgreSQL · MCP · BM25 · pgvector · Docker Compose · GitHub Actions

[图文演示](docs/demo/README.md) · [系统架构](ARCHITECTURE.md) · [验证成果](docs/RESULTS.md) · [部署手册](LOCAL_DEPLOYMENT.md) · [文档导航](DELIVERY.md)

## 业务演示

![人工审批后恢复原工单并完成模拟退款](docs/demo/screenshots/05-resumed.png)

截图展示审批恢复流程，采集于 2026-09-23；完整演示包含 [9 个业务场景与真实页面截图](docs/demo/README.md)。个人账号、订单同步和容量验证见[验收索引](VALIDATION.md)。

|场景|处理结果|
|---|---|
|签收 7 天内且未使用|自动完成模拟退款|
|两项条件均不满足|规则拒绝并保留原因|
|仅满足部分条件|等待人工审批，审批后恢复原工单|
|事实缺失、冲突或政策证据不足|转人工核查|
|同订单重复申请或中断后重放|保留原退款台账，阻止重复记账|

## 核心设计

- **模型建议与执行权限分离。** LangGraph 编排调查、校验、审批和执行；MCP 只提供订单与政策查询，后端确定性规则、当前订单事实与审批记录共同约束退款。
- **异步执行与持久化恢复。** PostgreSQL 队列以 `SKIP LOCKED` 支持多 Worker，checkpoint 保存图状态。审批落库后恢复同一 `run_id`，订单唯一约束保证模拟退款台账幂等。
- **可追溯的政策检索。** 支持 Markdown 和文本型 PDF 维护导入、版本发布与回滚、片段引用和页码定位；默认 BM25，可选本地 E5＋pgvector＋RRF 混合检索。
- **账号与数据隔离。** 个人账号、可撤销会话、后端角色鉴权；两个工作区使用独立 PostgreSQL。数据库区分迁移、业务和只读角色。
- **运行保障。** 连接池、SQL／锁等待超时、任务执行期限、限流与日额度；关联日志定位工单，独立监控观察 Worker 离线、任务停滞与连续失败。
- **可验证的恢复流程。** 身份库与双业务库联合备份，恢复到独立环境后核对结构、数据及原审批流程；CI 注入真实 Worker 强杀、数据库中断和退款提交后重放。

## 验证成果

|验证项|结果与口径|
|---|---|
|自动化回归|409 项基础、166 项 PostgreSQL、27 项前端测试通过；另有 198 项故障检查|
|RAG 检索|80 条评测样例；冻结验收集的 20 条可答／部分可答问题，必要证据完整命中率由 15/20 提升至 17/20（75% → 85%）|
|本地容量|最多 5 个有效账号、100 条新工单／日、3 个执行槽（alpha 2＋beta 1）|
|本地响应|100 次入队、100 次读取、30 条自动流程；p95 分别为 0.562 秒、0.328 秒、12.157 秒|
|恢复演练|单日会话内完成备份、独立恢复与回退；事故点备份年龄约 312 秒，恢复约 90 秒，回退约 96 秒|

回归数字来自提交 `66b2aa4` 的[成功 CI 及验收附件](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/37424944514)。容量和恢复数字来自本地隔离实测；RAG 为固定数据集的检索指标，标签为模型生成并经自动核查，不等同于人工盲测的回答准确率。样本、环境与证据见[验证成果](docs/RESULTS.md)。

## 快速启动

需要 Git、Python 3.12、Docker Engine／Docker Desktop（Linux 容器）及 Docker Compose v2。以下 PowerShell 命令创建一套全新的个人账号演示环境，启用合成订单同步和容量保护。首次构建需下载依赖；正常 demo/BM25 运行不调用生成模型。

```powershell
git clone https://github.com/qq789qq978-cpu/ResolveFlow.git
cd ResolveFlow
docker build -t resolveflow:showcase .
python scripts/local_stack.py --project resolveflow-showcase --image resolveflow:showcase --port 8058 --orders --capacity
$rfWork = 'work/resolveflow-showcase'
$rfCompose = @('compose', '--env-file', "$rfWork/empty.env", '-p', 'resolveflow-showcase', '-f', "$rfWork/compose.json")
docker @rfCompose run --rm --no-deps -T identity-init
docker @rfCompose up -d --wait --wait-timeout 180
docker @rfCompose ps
```

打开 <http://127.0.0.1:8058>。使用维护员 `maintainer` 登录，初始随机密码保存在本机 `work/resolveflow-showcase/bootstrap.txt`。先创建业务账号并分配工作区与角色，再使用业务账号进入工作台。维护员负责账号管理，不隐含业务数据访问权限；最多 5 个有效账号包含维护员。

生成器会拒绝已有项目配置或资源；已有部署按[运维手册](docs/deployment/HANDOVER.md)恢复，不重复初始化。私有配置和密码保存在被 Git 忽略的 `work/`，无需复制或覆盖 `.env`。停止服务保留数据：

```powershell
docker @rfCompose stop
python scripts/local_backup.py resume --work $rfWork
```

账号操作见 [ACCOUNTS.md](ACCOUNTS.md)，完整安装与联合备份见 [LOCAL_DEPLOYMENT.md](LOCAL_DEPLOYMENT.md)。保留命名卷，不使用 `down -v` 清理演示。共享角色码的兼容部署另见 [OPERATIONS.md](OPERATIONS.md)。

## 运行模式与范围

默认 `demo` 使用规则生成建议，仍实际执行数据库查询、MCP、队列、审批和 checkpoint；`live` 可配置 DeepSeek 兼容接口进行模型调查。混合检索使用本地 embedding，与生成模型调用独立配置，见 [RAG.md](RAG.md)。

本版本定位为本地可运行的售后流程演示，使用合成订单与模拟退款。外部支付服务商、公网部署和真实资金处理不在交付范围；独立支付实验仅验证本地 mock 契约。PDF 支持文本层解析，不含 OCR；reranker 经实验未达到启用门槛，保持关闭。单日恢复演练不代表长期在线 SLA。
