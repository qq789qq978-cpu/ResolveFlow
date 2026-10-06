# 步骤 1.7 — 退款台账提交后、checkpoint 保存前中断重放

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

日期：2026-09-21。结论：**通过，59项检查及1.6的52项回归均通过**。基线 `31e6313`，未修改应用业务代码或重建镜像。

## 结论与覆盖范围

自动退款和人工批准退款两条路径均验证了：独立连接已能读到首次提交的退款台账，而execute的checkpoint和待保存输出尚未持久化；强杀真实Worker并中断那条未完成的checkpoint数据库写入后，重启Worker，原工单从execute节点重放，以already_refunded完成，原台账内容不变。该状态表示重放时第二次插入被唯一约束拦截，首次退款仍属于原工单。

环境为 `resolveflow-qa-step17`（8008），独立数据库、卷、demo模式。API/Worker与主环境使用同一镜像，Worker命令仍为 `python worker.py`：

`sha256:6412f843e00996f04c6b42a4dc357d9b0355e33db973351c0d21d42b3aa17a5a`

## 真实窗口与校准发现

首次运行只强杀Worker、然后解除checkpoint测试锁。强杀后当时看到的checkpoint确实未完成，但PostgreSQL中已发出的写入语句没有随客户端死亡立即取消；解锁后该语句提交了execute的待保存输出，重启的Worker采用该输出，以refunded完成，没有重新执行退款节点。

因此首次报告的严格重放断言失败，**不计为1.7通过，也不解释为业务重复退款故障**。原报告与RF-1700工单、唯一退款全部保留。它说明仅杀客户端不足以保证服务器端在途事务一定回滚，也不能把已缓存结果的恢复误称为执行重放。

最终脚本采用以下故障注入：

1. 每次复制RF-1001/1004的合成字段，分配两张未使用订单，保证每次复验都真正执行首次退款INSERT，而不是沿用已退款订单。
2. 用真实API创建工单；人工路径经真实审批员API同意。测试库临时触发器先阻塞首次退款INSERT，等先前的execute入口checkpoint写完。
3. 仅放行退款写入；独立连接确认台账已提交。另一个测试锁使该run后续的 `checkpoint_writes`、`checkpoint_blobs`、`checkpoints` 写入等待。比较完整图状态、checkpoint ID以及待保存输出行摘要，确认没有execute完成结果可供跳过重放。
4. SIGKILL整个Worker容器，记录exited与137，确认队列事务回滚、任务锁释放、已提交退款仍存在。
5. **额外终止被本次测试锁阻塞的checkpoint写后端连接**。仅选择该观察器锁住的checkpoint INSERT，不停止数据库或无关连接。再次确认execute待执行、旧输出未保存、退款仍在。
6. 释放测试锁，删除4个测试触发器与测试函数，确认残留数0，再显式启动原Worker容器。恢复期间运行正常业务代码，不保留测试钩子。

数据库门控连接有60秒空闲超时，finally也负责关闭和删除测试对象。此方法没有替换Engine或伪造退款结果；退款提交、checkpoint读写、队列锁、审批、唯一约束及重放都是实际容器执行。

## 正式验收结果

|观察|自动路径|人工批准路径|
|---|---|---|
|新合成订单|RF-1701，复制RF-1001|RF-1702，复制RF-1004|
|原工单|`1c3a777f-9aae-4e9f-91ab-e3b569de4ab1`|`4485b8ea-6284-4a31-b6f9-7058ecba40bc`|
|强杀前已提交退款|29900分，归属原工单|15900分，归属原工单|
|强杀退出码|137|137|
|中止的QA checkpoint后端PID|96|175|
|启动至API任务done|3.141秒|3.750秒|
|恢复结果|already_refunded|already_refunded|
|checkpoint数量|4 → 5，旧记录保留|5 → 6，旧记录保留|
|决策来源|automatic|human，原审批记录不变|
|台账比较|金额、run_id、时间、行数全相同|金额、run_id、时间、行数全相同|

原工单均由同一run_id/thread_id恢复，完成execute节点，无管理员retry、重建原工单或额外审批审计。强杀前未提交的任务尝试没有计数，恢复后才记录一次成功；任务至少一次执行，业务通过唯一约束保持幂等，不是exactly-once执行。

各订单又提交一条独立后续工单：自动路径 `7b0a93e7-956e-4a59-bbb7-9908ad7a202a`；人工批准路径 `e7efec27-8a27-44bc-b627-df9b9598092e`。后者再次走实际审批。两者均already_refunded，仍引用原唯一退款，证明故障后继续工作且后续请求也不能新增退款。

正式验收新增2张订单、4条工单、2条退款；最终QA累计5条工单、5条done任务、2条审批、3条退款、7条成功attempt、26个checkpoint。额外的一张RF-1700、工单 `717ff9f1-40e7-4be1-af24-9e870b0df467` 和29900分台账来自首次窗口校准，未删除或重复使用。所有工单均demo、零模型调用与token，无真实支付。

## 回归与主环境

共用脚本新增 `--step 1.7` 和checkpoint写入观察，新增测试专用 `refund_replay_probe.py`；观察器异常退出现在会令清理检查失败。使用既有8007环境回归1.6，52项全部通过，原RF-1004退款不变。8007增加3条工单和审批，累计6条工单、6条done任务、6条审批、1条退款。8006与8004保持停止，旧数据保留。

主环境13张业务/checkpoint/RAG表的行数和SHA-256，在首次校准前、正式验收前后及回归前后完全一致，不含动态心跳。主环境三服务healthy，测试触发器仅在8008存在过；收尾时所有QA停止并保留卷，临时函数/触发器已删除，无清理错误。

## 证据与复验

- [正式验收：59项检查、退款/checkpoint快照及连接中断证据](refund-replay-verified.json)
- [首次窗口校准：严格重放断言未通过，保留原始记录](refund-replay.json)
- [1.6回归：52项检查](approval-regression.json)
- [真实Worker日志](worker.log.txt)、[最终环境状态](environment-final.txt)、[交付核对摘要](verification-summary.json)
- [验收脚本](../../scripts/worker_crash_qa.py)、[观察器](../../scripts/worker_crash_probe.py)、[仅测试库使用的窗口控制](../../scripts/refund_replay_probe.py)

Docker已启动，主环境以demo运行，已有与主环境一致的 `resolveflow:local` 镜像，8008可用；宿主Python只需标准库：

```powershell
python scripts/worker_crash_qa.py --step 1.7 --report validation/refund-replay-recheck.json
```

固定使用公开QA变量与独立Compose项目，不加载主 `.env`，不构建镜像、不删除卷或业务历史。每次新增两张合成订单、四条工单和两笔模拟退款；报告需用新路径。临时函数和触发器仅为实验装置，不是迁移或生产功能。

本步的定向checkpoint连接终止不等于PostgreSQL服务短时断网，也未验证进程自动拉起、真实支付、真实模型请求、多Worker或整机断电。
