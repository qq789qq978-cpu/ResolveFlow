# ResolveFlow 文档导航

ResolveFlow 已交付可本地运行的售后智能工单系统，覆盖调查、规则决策、审批、模拟退款和故障恢复。首次阅读从首页与图文演示开始；运行项目使用个人账号部署。

|阅读目的|入口|
|---|---|
|功能概览与快速启动|[README](README.md)|
|流程截图与业务结果|[图文演示](docs/demo/README.md)、[Word 演示](docs/demo/ResolveFlow-demo.docx)|
|设计与代码入口|[系统架构](ARCHITECTURE.md)|
|指标与测试依据|[验证成果](docs/RESULTS.md)、[验收索引](VALIDATION.md)|
|部署与日常运维|[本地部署](LOCAL_DEPLOYMENT.md)、[运维手册](docs/deployment/HANDOVER.md)|
|账号与权限|[个人账号](ACCOUNTS.md)、[数据库角色](DATABASE_ROLES.md)|
|订单接入与容量|[合成订单同步](ORDER_SYNC.md)、[额度与限流](CAPACITY.md)|
|政策知识库|[RAG](RAG.md)、[PDF 导入](PDF_IMPORT.md)、[政策发布](POLICY_RELEASES.md)|
|超时与故障定位|[运行期限](RUNTIME_LIMITS.md)、[日志与告警](OBSERVABILITY.md)|
|备份与演练|[联合备份](LOCAL_DEPLOYMENT.md)、[恢复演练](TRIAL.md)|
|交付范围|[需求与验收](docs/deployment/REQUIREMENTS.md)、[版本里程碑](EXECUTION_PLAN.md)|
|维护与扩展|[维护指南](PROJECT_HANDOFF.md)、[演进方向](ROADMAP.md)|

本地演示版基线 `66b2aa4` 已通过[完整 CI](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/37424944514)与附件核验。当前分支状态查看 [Actions](https://github.com/qq789qq978-cpu/ResolveFlow/actions/workflows/ci.yml)。

截图采集于 2026-09-23，展示当时的业务流程与共享角色码界面；当前个人账号、双工作区、同步和容量能力由后续验收报告覆盖。历史截图和报告保留采集日期。

默认运行使用合成订单、demo/BM25 与模拟退款。外部支付沙箱、公网部署、真实资金、OCR 和长期在线 SLA 不属于本地演示版的交付范围。
