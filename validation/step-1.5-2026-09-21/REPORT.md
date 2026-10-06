# 步骤 1.5 — 调查中强制终止真实 Worker 并恢复

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

日期：2026-09-21。结论：**通过，25项检查全部通过**。代码基线 `a4c8d37`；未发现需要修复的业务问题，新增可复现故障验收脚本、证据和文档。

## 环境与故障位置

使用新独立Compose项目 `resolveflow-qa-step15`，入口 `127.0.0.1:8006`，独立PostgreSQL及命名卷。主环境8003和已有QA 8004的业务数据不参与故障注入。实际Worker命令仍为 `python worker.py`，API与Worker使用同主环境镜像：

`sha256:6412f843e00996f04c6b42a4dc357d9b0355e33db973351c0d21d42b3aa17a5a`

demo调查很快，脚本先等三个服务健康，短暂暂停Worker，让真实API接受工单，再用独立数据库连接对QA的 `rf_orders` 加表锁，随即解除Worker暂停。锁只用于确定故障时机，不修改订单。数据库连接设置30秒空闲事务超时，脚本结束也会关闭连接释放锁。

随后观测到来自Worker容器地址的真实MCP查询 `SELECT * FROM rf_orders WHERE id=$1 AND owner=$2` 正在等待该锁。此时目标工单running、任务行锁不能被另一连接领取、checkpoint下一节点为investigate，尚无proposal或result。脚本此时向整个Worker容器发送SIGKILL，没有替换Engine、伪造异常或仅杀测试锁进程。

## 实测结果

|观察点|证据与结果|
|---|---|
|故障工单|`5c6baa3e-665a-4b8a-b3a5-1a05afbab13d`，RF-1001，申请退款|
|强杀前|真实MCP订单查询等待锁；2个PostgreSQL checkpoint；下一节点investigate；尚无退款|
|进程终止|容器exited，退出码137；原Worker宿主PID 3013|
|强杀后、重启前|任务行锁释放，job仍queued、run仍running；原2个checkpoint及待执行节点保留；退款0条|
|恢复动作|释放测试表锁，显式启动同一个Worker容器；新宿主PID 3345|
|原工单恢复|从发起启动到API显示done约3.297秒；原run_id/thread_id最终refunded，checkpoint增至5个，原记录保留|
|任务与审计|只一条成功的已提交attempt；审计仅create，无管理员retry或重建原工单|
|退款|原工单产生RF-1001唯一台账，29900分，模拟执行|
|后续任务|`4bab4d5a-ea1a-499d-befc-9ae1672c72cc`，同订单申请退款，最终already_refunded；原台账内容不变|
|最终QA数据|2条工单、2条done任务、2条成功attempt、1条退款、10个checkpoint|
|模型费用|两条工单model_calls/input_tokens/output_tokens均为0|
|主环境保护|前后13张业务、checkpoint与RAG表的行数及SHA-256指纹完全一致；主环境三服务healthy|
|清理|8006三个QA服务已停止，无清理错误，保留全部卷和验收记录；8004 QA保持停止|

强杀发生在任务事务提交前，因此该次执行没有增加 `attempts`，也没有产生失败的 `rf_job_attempts` 记录。恢复后的成功执行才计数1；不能据此宣称Worker只执行过一次。SIGKILL已由容器退出137与重启前状态记录证明。最终清理的正常退出0不覆盖这段历史。

## 交付与复验

- [机器可读报告：25项检查、各时点状态与主库指纹](worker-crash.json)
- [真实Worker日志](worker.log.txt)
- [收尾容器状态](environment-final.txt)
- [宿主验收脚本](../../scripts/worker_crash_qa.py)
- [容器内数据库与checkpoint观察器](../../scripts/worker_crash_probe.py)

主环境需运行在demo模式，Docker引擎已启动，已有同版本 `resolveflow:local` 镜像，8006可用。宿主Python只使用标准库，观察器依赖镜像中已有的Python包。从项目目录运行：

```powershell
python scripts/worker_crash_qa.py --report validation/worker-crash-recheck.json
```

脚本不加载主环境 `.env`，使用单独的空Compose环境文件和明确的公开QA变量，不调用模型或支付。它会启动固定的8006 QA、执行故障注入、在finally中关闭观察器并停止QA；不删除卷或历史行，不改重启策略，不构建镜像。报告路径必须是新文件，每次复验新增两条合成工单。已有RF-1001退款时，原工单恢复预期自动调整为already_refunded，不删除台账来制造首次退款。

本次初始QA为空，实测覆盖了恢复后首次模拟退款以及后续重复退款拦截。报告中的初始退款行、恢复结果与计数可区分首次运行和以后复验。

## 验收边界

这证明了单个真实Worker在demo的MCP调查查询期间死亡后，PostgreSQL释放任务锁，原checkpoint可供重启后的Worker恢复。恢复从未完成节点重新执行，不是从查询内部的某一行继续，也不是exactly-once执行。

本次显式调用容器启动，未把Docker对手动kill的行为当作自动拉起证明。没有测试真实模型请求中断、审批保存后的故障、退款提交后checkpoint保存前的窗口、PostgreSQL断网、多Worker或整机断电。计划内其余场景按后续编号推进。数据库指纹核对不包括动态心跳，也不是备份恢复演练。

阶段1.10统一推送GitHub并验证对应提交CI；本次没有远程CI成功声明。
