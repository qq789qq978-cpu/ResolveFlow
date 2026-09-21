# ResolveFlow V3 项目交接

更新日期：2026-09-18。主目录 `D:/AgentProjects/ResolveFlow`，私有仓库 `qq789qq978-cpu/ResolveFlow`。本文替代此前“没有 Git / Docker 尚未实跑”的过期交接说明。验证结果见 [VALIDATION.md](VALIDATION.md)，后续计划见 [ROADMAP.md](ROADMAP.md)。

执行进度（2026-09-21）：1.1运营、1.2审批与1.3管理员浏览器验收完成，详见 [1.1报告](validation/step-1.1-2026-09-21/REPORT.md)、[1.2报告](validation/step-1.2-2026-09-21/REPORT.md)、[1.3报告](validation/step-1.3-2026-09-21/REPORT.md)。用户要求每次对话只完成一步，每步本地提交、阶段末统一推送并确认CI；完整清单见 [EXECUTION_PLAN.md](EXECUTION_PLAN.md)。下一步1.4尚未执行。独立QA环境位于8004，与主环境使用同一镜像、独立卷；用 `scripts/operator-qa.ps1 -Action Start` 恢复。目前共7条工单、2条审批、1条核查、2条模拟退款，均为demo。1.3新增工单三次受控失败后由管理员页面重试，现已自动拒绝；历史失败尝试保留，当前没有失败工单。后续需待审批/失败样例时应新增合成工单，不删除原验收数据。

## 当前架构

1.3收尾状态：主环境8003三服务healthy，独立QA容器已停止并保留卷。非阻断UI待复验：底部提交提示可能滞后于终态，核查结案仍保留原调查建议；最终状态与人工结论正确，详见1.2和1.3报告。1.3使用受控调查异常准备失败样例，不等于完成1.5起的实际Worker强杀/数据库断连测试。

- 正式入口 `operations:app` + `worker.py`，前端是 `frontend/dist` 原生 HTML/CSS/JS。
- Compose 管理 PostgreSQL 17、FastAPI 和 Worker；入口 `127.0.0.1:8003`，数据库仅在容器网络内开放。
- Docker 程序位于 `D:/Programs/DockerDesktop`，Docker 数据在 `D:/DockerData`。便携 PostgreSQL 不与容器数据库混用。
- 当前以 demo 运行。本次升级没有付费模型调用。live 通过兼容接口调用 DeepSeek。
- PostgreSQL 保存业务、队列、LangGraph PostgresSaver checkpoint 和 RAG 索引。Engine 在 PG 模式仍初始化遗留 SQLite 文件，但不以它们作为主数据或 checkpoint；独立测试使用 SQLite。
- 运营、审批、管理员三角色由后端校验，仍采用共享角色码，没有个人账号或租户隔离。

## Agent 与恢复

图流程为 investigate → validate → execute / approval / END。模型负责调查建议，确定性规则作最终分流。退款规则 refund-v2 在事实完整、已签收、证据可用且无冲突时检查“签收不超过7天”和“商品未使用”：两项符合自动模拟退款，零项符合自动拒绝，一项符合人工审批；未知事实优先人工核查。

run_id 同时是 thread_id。API 在同一事务保存工单与任务；人工决定先落库再入队。Worker 以 FOR UPDATE SKIP LOCKED 锁任务行执行，从 checkpoint 继续原工单。退款表订单唯一键防止检查点提交窗口中的重复执行。保证是任务至少一次执行加业务幂等，不是分布式 exactly-once。

普通异常最多自动重试3次，耗尽后管理员可重试。强制退出会释放事务锁，但未提交尝试不一定计入 attempts。正常容器重建不覆盖所有强杀和断网场景。

## 文档 RAG

rag.py 从 knowledge/*.md 读取 id/title/version 和正文，按段落分块，长段落最多500字符、重叠60字符，保留文件名、行号、版本、SHA-256。中文 bigram / 英文词的 BM25 在小语料上排序，默认返回4个片段。

生产索引在 rf_knowledge_documents / rf_knowledge_chunks。首次启动种入空索引，已有索引不被重启覆盖。python rag.py 在一个事务中替换整个受审核集合，移除旧片段；非法输入保留旧索引。查询使用一致快照；数据库错误不回退到本地样例。

MCP search_policy 调用检索，lookup_order 被宿主绑定当前订单与 owner；没有退款写工具。页面“政策知识库”和 /api/knowledge 可独立查询。工单保留当时证据，后续导入不修改旧工单。当前没有 embedding、向量库、reranker 或任意用户上传。详见 RAG.md。

## 数据与验证

业务表包括 rf_orders、rf_runs、rf_jobs、rf_approvals、rf_audit、rf_refunds、rf_reviews、rf_job_attempts、rf_worker_heartbeats；另有 LangGraph checkpoint 表和两张 RAG 表。

verify_persistence.py 比较重建前后行数和完整内容指纹，检查容器ID已变、卷名未变，查询旧工单并恢复待审批工单。动态心跳表不要求逐行一致。该脚本新增合成工单，仅允许demo。报告存放在 validation/。

旧 async_live_report.json 是历史真实模型报告，不代表当前容器使用live。工作流文件存在不代表CI通过；以对应提交的远程结果为准。

## 本次修复

- “没有使用过”等否定短语不再误命中“使用过”；字段缺失不再在冲突检查中直接抛错。仍是有限短语规则。
- 审批或自动执行完成时同步最终说明，避免UI继续显示等待审批。
- CI 的测试数据库密码和容器端口移至 job 环境，清理阶段也可解析Compose。
- 增加文档导入/检索/回滚、PG人工同意路径、知识查询权限和容器重建验证。

## 已知边界

1. 三角色基础浏览器验收1.1–1.3已完成；断网重连、快速切换角色/工单和移动端仍待1.4验收，API测试不替代UI验收。
2. 长事务跨越模型请求；SQL事务失败后的错误记录、checkpointer连接断开后的重建、实际Worker强杀仍需专项验证。
3. 心跳在线不等于任务有进展；token汇总不覆盖所有失败重试，不是账单。
4. 启动时建表不是正式migration；需要版本化升级和独立库备份恢复。
5. 文档版本和程序退款规则必须共同审核；RAG对近义表达有限，需扩大标注集后评估语义检索。
6. 退款使用调查时快照；真实支付前需执行前重查、独立授权、幂等键、对账和状态机。
7. 合成订单、共享角色码和本机服务尚不是公网生产系统。

## 维护约束

不要覆盖.env，不提交密钥、数据库、work/或data/。不要运行docker compose down -v或删除数据库。不要重跑work/下的旧升级脚本。保留订单唯一退款约束、持久化审批、同一thread_id恢复和后端角色权限。

## 关键文件

operations.py/auth.py：API权限；engine.py/refund_policy.py/conflicts.py：Agent与规则；jobs.py/storage.py/worker.py：队列存储；rag.py/knowledge/：文档RAG；mcp_gateway.py/mcp_server.py：工具；frontend/dist/：页面；verify_persistence.py：重建验收；test_*.py/evaluate_*.py：测试评测；compose.yaml/.github/workflows/ci.yml：部署CI。
