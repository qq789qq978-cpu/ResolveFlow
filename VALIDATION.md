# 验收记录 — 2026-09-18

补充记录（2026-09-21）：[步骤1.1运营角色浏览器验收通过](validation/step-1.1-2026-09-21/REPORT.md)；[步骤1.2审批角色浏览器验收通过](validation/step-1.2-2026-09-21/REPORT.md)；[步骤1.3管理员角色浏览器验收通过](validation/step-1.3-2026-09-21/REPORT.md)。审批同意/拒绝、核查结案、重复审批拦截、管理员监控、失败重试和角色权限已验证。三步使用同镜像、独立数据库的8004 demo环境。1.3有25项补充HTTP检查及数据库核对，无新增退款；截至1.3时，页面异常适配和实际Worker强杀尚未验收，后续结果见下文。

后续补充：[步骤1.4页面验收通过](validation/step-1.4-2026-09-21/REPORT.md)，覆盖320/375/768/1280像素视口、服务不可达后的自动重连、响应丢失、延迟响应期间切换角色/工单/筛选。修复布局重叠、旧响应覆盖、断线提示和历史结案建议；12项前端回归测试通过。本机主入口已更新，主环境六张业务表指纹不变，旧QA退款台账不变。CI配置加入Node测试，远程执行待阶段末推送。

后续补充：[步骤1.5调查中Worker强杀验收通过](validation/step-1.5-2026-09-21/REPORT.md)。8006独立demo环境中，真实MCP订单查询被测试锁阻塞，确认工单running、任务锁持有、checkpoint下一节点investigate后SIGKILL；退出137，任务锁释放且原checkpoint保留。显式启动Worker后3.297秒原工单完成模拟退款，无人工重试；后续同订单任务拦截重复退款。25项检查通过，2条工单仅1条退款，零模型调用。主环境13张业务/checkpoint/RAG表指纹不变，QA已停止保留卷。未修改业务代码，未推送远程CI。

后续补充：[步骤1.6审批保存后Worker强杀验收通过](validation/step-1.6-2026-09-21/REPORT.md)。8007独立demo环境，审批同意/拒绝经API返回202并独立读库确认后，阻塞Worker读取审批，确认已领取任务后强杀，均退出137。显式重启后分别2.843/2.813秒恢复为refunded/rejected；审批决定、审批人、理由、时间与原checkpoint保留，拒绝不退款，同意仅一条退款。Worker停止与恢复后共8次重复/反向审批请求均409；后续另一条已批准工单already_refunded。52项检查通过；共享脚本的1.5回归25项也通过。主库13张表指纹不变，所有QA停止保留卷，无模型调用。本次未改业务代码，未推送。

最新补充：[步骤1.7退款提交后重放验收通过](validation/step-1.7-2026-09-21/REPORT.md)。8008独立demo环境，RF-1701自动退款、RF-1702人工批准退款分别提交29900/15900分后，确认execute checkpoint和待保存输出均未落库，再SIGKILL Worker（137）并终止其被测试锁阻塞的checkpoint后端连接。重启后3.141/3.750秒原工单重放为already_refunded，原台账金额、工单归属、时间完全不变；后续同订单工单也未增加退款。59项检查及1.6的52项回归通过。首次校准曾发现PG在途写在客户端死亡后仍可完成，严格重放断言未通过，原始报告如实保留；最终脚本补充定向终止该连接以确保覆盖重放。主库13张表不变，临时测试触发器已删除，QA停止保留数据，零模型调用。未改业务代码，未开始1.8，未推送。

## 本次结果

|验证|结果|证据|
|---|---|---|
|容器内基础、MCP、Skills、规则和RAG测试|42 passed|validation/local-tests-2026-09-18.txt|
|容器PostgreSQL集成测试|8 passed|同上；随机独立schema，不清空业务库|
|离线业务流程|12/12|validation/2026-09-18-evaluation_v3.json|
|离线文档RAG|12/12，Recall@4=1，MRR@4≈0.944，无关查询空结果3/3|validation/2026-09-18-evaluation_rag.json|
|原V3容器重建|12张表内容一致，7条原工单保留，审批恢复|validation/persistence-before-rag-2026-09-18.json|
|RAG新版容器重建|14张表内容一致，8条原工单保留，审批恢复|validation/persistence-2026-09-18.json|
|Python/前端脚本语法、工作流YAML|通过|本地静态检查|

全部新增模型相关验证采用 demo/离线或假模型，未调用付费API。测试均使用合成订单和模拟台账。

## 第四步：持久化与恢复

新版验收完成时间（UTC）：2026-09-18T09:23:45.358411+00:00。

1. 原有8条工单全部可查询；新增一条RF-1004，停在人工审批状态。
2. 记录业务、LangGraph和知识库表的行数及完整内容MD5指纹。指纹用于检测变化，不是安全签名。
3. 执行不带 `-v` 的 `docker compose down`，再 `up -d --wait`。
4. 三个容器ID全部改变，健康状态均为healthy，命名卷名称完全相同。
5. 比较以下表的内容，全部一致。心跳表是动态数据，因此不做相等比较。
6. 对同一工单 `184868ad-27ff-4d68-9883-fa0700fa75af` 提交审批拒绝，成功resume至rejected，调查轨迹没有重复，checkpoint继续增加，退款台账保持不变。
7. 同时复验未登录401、运营访问管理员接口403、运营不能审批403、审批员成功202、重复审批409和Worker在线。

|表|重建前行数|重建后行数|内容指纹|
|---|---:|---:|---|
|rf_orders|4|4|一致|
|rf_runs|9|9|一致|
|rf_jobs|9|9|一致|
|rf_approvals|2|2|一致|
|rf_audit|11|11|一致|
|rf_refunds|1|1|一致|
|rf_reviews|0|0|一致|
|rf_job_attempts|11|11|一致|
|checkpoints|41|41|一致|
|checkpoint_writes|185|185|一致|
|checkpoint_blobs|98|98|一致|
|checkpoint_migrations|10|10|一致|
|rf_knowledge_documents|3|3|一致|
|rf_knowledge_chunks|7|7|一致|

这证明正常关闭并重建容器后仍能保留数据和恢复待审批流程。不代表磁盘损坏、真实支付幂等、任意强杀时点、数据库断网或云端容灾都已验收。备份恢复是独立待办。

## 第五步：GitHub与可复现交付

仓库为私有 `qq789qq978-cpu/ResolveFlow`。旧提交 `42369ce` 的Actions运行34594183726中，基础测试、PG集成、离线评测和容器业务流均成功，但清理容器步骤失败。原因是Compose所需POSTGRES_PASSWORD只在构建步骤设置，清理步骤也需要解析配置。

本次将测试密码/端口移至job环境，补充RAG评测和重建恢复步骤。README、运行说明、交接文档、RAG说明、路线图和验收报告均纳入Git。当前提交的远程验收请以 [Actions](https://github.com/qq789qq978-cpu/ResolveFlow/actions) 为准；本地测试不能替代远程运行。

CI会从仓库检出、安装锁定依赖、建立独立PG服务、构建容器并验收，因此用于核对新环境可复现性。私有仓库需要访问权限。实际密码、API Key、数据库和Docker磁盘未作为交付文件提交。

## 复验说明

```powershell
# 本机已安装requirements.lock，服务以demo启动
$env:MODE='demo'
docker compose up --build -d --wait
python verify_persistence.py --report validation/persistence.json
python evaluate_rag.py
```

RAG评测默认从随仓库提供的文档读取，不连接业务库。PG集成测试需在可访问的测试数据库上设置 `DATABASE_URL` 和 `RUN_PG_TESTS=1`，每项测试创建并清理自己的随机schema。

本次评测第一次在容器临时副本写报告时遇到文件归属权限问题；随后改用应用用户拥有的临时目录完成评测。该问题不影响已通过的42+8项测试，也没有修改生产目录权限。

## 尚未验收

实体手机与其他浏览器兼容性、真实模型在新RAG上的回答质量、PG短时断网、正式migration、独立库备份恢复和公网生产部署。1.1–1.4已验收三角色与页面，1.5–1.7已验收demo调查、审批保存及退款提交/checkpoint窗口的故障恢复。1.7终止单条QA checkpoint后端连接不代表数据库整体断网、模型请求中断、进程自动拉起或整机故障的证明。下一步详见 [ROADMAP.md](ROADMAP.md)。
