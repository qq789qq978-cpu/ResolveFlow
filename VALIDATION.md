# 验收索引

本地演示版基线 `66b2aa4` 于 2026-10-06 通过[完整 CI](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/37424944514)和附件核验：409 项基础、166 项 PostgreSQL、27 项前端测试，以及 198 项故障检查。指标口径见[验证成果](docs/RESULTS.md)。

## 业务与恢复

|场景|报告|
|---|---|
|运营、审批、管理员业务截图与证据|[图文演示](docs/demo/README.md)、[截图验收](validation/step-4.2-2026-09-23/REPORT.md)|
|调查时强杀 Worker|[1.5](validation/step-1.5-2026-09-21/REPORT.md)|
|审批保存后强杀 Worker|[1.6](validation/step-1.6-2026-09-21/REPORT.md)|
|退款提交后中断与重放|[1.7](validation/step-1.7-2026-09-21/REPORT.md)|
|数据库短暂中断|[1.8](validation/step-1.8-2026-09-21/REPORT.md)|
|多 Worker、锁竞争与超时|[1.9](validation/step-1.9-2026-09-21/REPORT.md)、[最终夹具修复](validation/step-5.8-2026-10-05/REPORT.md)|
|迁移、权限、备份与独立恢复|[第三阶段回归](validation/step-3.9-2026-09-23/REPORT.md)|
|连接池、非 MCP SQL、长事务与整体任务期限|[3.7](validation/step-3.7-2026-09-23/REPORT.md)|
|关联日志、Worker 离线、任务停滞与连续失败告警|[3.8](validation/step-3.8-2026-09-23/REPORT.md)|

## 检索与本地部署

|范围|报告|
|---|---|
|BM25、本地向量混合检索与生成评测|[第二阶段回归](validation/step-2.12-2026-09-22/REPORT.md)|
|标签来源、reranker 与文本 PDF|[RAG 补充](validation/rag-supplement-2026-09-23/REPORT.md)|
|个人账号、撤销、审计及工作区隔离|[5.2](validation/step-5.2-2026-10-03/REPORT.md)|
|身份与双业务库联合恢复|[5.3](validation/step-5.3-2026-10-03/REPORT.md)|
|订单同步与退款前复核|[5.4](validation/step-5.4-2026-10-03/REPORT.md)|
|本地支付 mock 契约|[5.5](validation/step-5.5-2026-10-05/REPORT.md)|
|额度、限流、执行槽及性能样本|[5.6](validation/step-5.6-2026-10-05/REPORT.md)|
|单日试运行、恢复与回退|[5.7](validation/step-5.7-2026-10-05/REPORT.md)|
|发布核对及 CI 修复|[5.8](validation/step-5.8-2026-10-05/REPORT.md)|

## 复验

完整 CI 定义在 [.github/workflows/ci.yml](.github/workflows/ci.yml)。前端可独立运行 `node --test test_frontend.cjs`。真实故障使用 `scripts/stage1_qa.py`，环境、端口、镜像及保留数据要求见[运行手册](OPERATIONS.md)；不要向已有业务库注入故障。

历史报告按日期保留原始结论，含失败及修复证据；其中“下一步”“未推送”等是当时状态。当前分支以对应提交的 CI 为准，运行环境健康需实时检查。支付 mock 不替代服务商沙箱，检索命中率不替代人工回答质量评测，单日会话不替代长期 SLA。
