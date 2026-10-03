# ResolveFlow 交付入口

本项目用于展示可恢复的售后 Agent 工作流：运营提交诉求、政策与订单调查、规则核验、人工审批、后台执行和模拟退款幂等。它使用合成订单，不接真实支付，不代表已上线的生产系统。

2026-10-03 按用户调整先执行 4.5，**4.4 简历、项目说明与面试问答暂缓**，待后续另行安排。4.1–4.3 材料保留；4.5 的本地验收与远程交付条件见 [本步报告](validation/step-4.5-2026-10-03/REPORT.md)。第五阶段是条件计划，不自动启动。

## 阅读与演示

|目的|入口|范围|
|---|---|---|
|了解项目与启动方式|[README](README.md)|功能、配置、demo/live 区别|
|理解架构|[架构说明](ARCHITECTURE.md)、[架构图 PNG](docs/architecture/resolveflow.png)|API、Worker、队列、checkpoint、MCP、RAG、权限和幂等|
|直接演示|[11 页 Word](docs/demo/ResolveFlow-demo.docx)、[图文脚本](docs/demo/README.md)|9 张真实截图；主线建议 2 分 40 秒，未真人计时，无视频|
|准备讲解|[翻页与讲解指南](docs/demo/PRESENTING.md)|页码、工单、证据对应关系与能力边界|
|重新运行演示|[隔离复现说明](docs/demo/REPLAY.md)|全新项目与空闲端口，零付费模型调用，停止后保留卷|
|运行与维护|[操作手册](OPERATIONS.md)|新装、升级、备份、独立恢复、告警与排障|
|导入知识库 PDF|[PDF 导入说明](PDF_IMPORT.md)|文本层 PDF、物理页码、维护 CLI、审核发布及回滚；不含 OCR 或网页上传|
|查看验收范围|[验收索引](VALIDATION.md)、[执行清单](EXECUTION_PLAN.md)|历史证据与当前进度；未验证项不视为通过|
|继续项目|[交接](PROJECT_HANDOFF.md)、[路线图](ROADMAP.md)|最新断点、下一操作和仍有效的约定|

## 本机环境与版本

4.5 已恢复 Docker Desktop 的 Linux 引擎。主环境端口 8003，仍保留 step36 镜像与原有 DB/API/Worker/embedding，未用新版重建或执行升级。数据、结构与历史快照的对比见 [Docker 恢复记录](validation/step-4.5-2026-10-03/docker-recovery.json)。

最新交付代码用专用镜像 `resolveflow:step45-20261003` 在独立 QA 验证；它不替换 `resolveflow:local`，也不意味着主环境已部署 PDF、期限和告警等后续功能。本次验证不调用付费模型、不执行真实退款。QA 结束停止服务并保留卷，不能清空历史库来重演首次退款。

截图仍是 2026-09-23 的记录，4.3 在 2026-10-03 完成离线材料复核；4.5 新增运行验收以本步报告为准，不修改旧截图日期或旧报告结论。

## 远程交付如何确认

本地测试成功后统一推送本阶段提交，再执行：

```powershell
python scripts/verify_delivery.py --status
python scripts/verify_delivery.py
```

只有本地 HEAD 与远程 main 相同、该 SHA 的 push 工作流成功，且测试和故障恢复附件核验通过，4.5 的远程交付才完成。实际 SHA、运行链接与核验结果保存在忽略文件 `validation/github-actions-local.json`；附件保存在忽略目录 `work/delivery`。回执必须 `passed=true` 且 `sha` 匹配当前提交，旧 CI 或徽章不能替代。

回执不提交 Git，避免为记录自身提交 SHA 再产生一个未经 CI 的提交。任何代码或材料修复都需新提交、重新推送并确认该提交的 CI。收到失败邮件时按邮件中的 SHA 和运行链接判断，不把旧失败等同于最新提交失败。

## 保留的边界

人工标签复核和通用引用语义支持性尚未完成；真实 reranker 实验未达到启用门槛，保持关闭。告警仅管理员页面与本地日志，无外部通知；共享角色码不等于个人账号或租户隔离。少量合成样例的耗时、检索命中和引用匹配不能写成生产准确率或容量承诺。

4.4 暂缓不影响本次已完成材料与代码的发布，但不能据此宣称第四阶段全部完成。真正上线的 5.1–5.8 仍需业务目标与明确授权。
