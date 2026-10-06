# 4.1 最终架构图

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

2026-09-23，在原目录接续已完成的3.9（代码提交`526e166cfbec1b55796bf8ef8f27462fd00bc951`）。本步仅完成架构图与解释、来源和图面核对；不涉及应用代码变更、主环境部署、数据写入或故障注入。

交付入口：[ARCHITECTURE.md](../../ARCHITECTURE.md)。主图提供[SVG](../../docs/architecture/resolveflow.svg)、[PNG](../../docs/architecture/resolveflow.png)和[Mermaid源文件](../../docs/architecture/resolveflow.mmd)，附[重绘说明](../../docs/architecture/README.md)。

核对范围：浏览器三角色与API入队；PostgreSQL持久队列；Worker、每次任务子进程与stdio MCP的进程边界；受限数据库角色；LangGraph人工中断/同工单恢复；独立事务下的模拟退款重放幂等；Markdown/文本PDF维护、BM25/可选本地E5与pgvector/RRF；独立monitor、日志、迁移和备份恢复。主图以可读的调用/访问为主，数据库表和运维细节在说明中展开。

已逐项对照代码入口，不把模型建议当退款授权，不把API当Worker，不把180秒当人工等待截止日期，不把checkpoint与退款描述为同一事务。图中明确代码基线与本机step36部署差异；保留人工语义审核、OCR、reranker不启用、外部告警及生产能力的边界。

Mermaid CLI 11.12.0渲染SVG/PNG成功，使用本机Chrome和中文字体。迭代时发现ELK布局将浏览器到API的箭头退化为零长度路径，改用Dagre并重新渲染；又调整了可选embedding边，避免标签被数据库节点遮住。最终逐项检查中文、箭头、标签、进程边界和代码基线，没有采用有问题的中间导出。源文件/导出哈希、路径完整性、有效箭头及源码未变的核对见[verification.json](verification.json)。

本次为文档变更，没有重新执行264基础/149PG/198故障测试，也没有启动新的CI；这些数值明确引用[526e166的成功CI](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/35832707932)，不冒称4.1提交的测试结果。临时绘图依赖保存在忽略的work目录，应用依赖和Docker镜像保持原样。

本轮停止，4.2演示脚本与数据尚未开始。保留业务数据、历史卷、审批/checkpoint、退款幂等和后端权限；无付费模型、真实退款或.env覆盖。
