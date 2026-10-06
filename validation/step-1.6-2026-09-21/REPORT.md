# 步骤 1.6 — 审批保存后强制终止真实 Worker 并恢复

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

日期：2026-09-21。结论：**通过，1.6的52项检查与1.5的25项回归全部通过**。基线提交 `4ef0c73`。扩展共用验收脚本和观察器，未发现需要修复的业务问题，未修改应用或重建镜像。

## 环境与准确的故障位置

新建独立Compose项目 `resolveflow-qa-step16`（`127.0.0.1:8007`），使用独立PostgreSQL与命名卷。API/Worker与8003主环境同镜像，真实命令 `python worker.py`：

`sha256:6412f843e00996f04c6b42a4dc357d9b0355e33db973351c0d21d42b3aa17a5a`

使用RF-1004合成订单，先由真实API提交退款诉求，等待真实Worker完成调查并进入LangGraph的approval interrupt。分别对拒绝、同意两种决定执行：

1. 短暂暂停健康Worker，经审批员API提交决定；收到202后，通过独立数据库连接核对已提交的审批人、理由、布尔决定、时间和approval队列任务。
2. 观察器锁住QA的 `rf_approvals` 表，再解除Worker暂停。真实Worker领取并锁定任务行后，阻塞在 `Store.get()` 的 `SELECT * FROM rf_approvals WHERE run_id=$1`。数据库活动记录的来源地址属于Worker容器。
3. 此时原graph仍停在approval interrupt，`rf_runs`为approval_queued；Worker尚未调用 `Engine.recover()`，因此尚未更新为running。发送SIGKILL给整个Worker容器，记录exited与137。
4. 确认任务行锁释放、审批任务仍queued、原审批记录及checkpoint不变，再解除表锁。Worker仍停止时测试同向重复和反向改口均409，然后显式启动Worker，观察原任务恢复。

表锁只控制时机，不修改审批或业务数据；30秒空闲事务超时和finally关闭连接限制其生命周期。锁持有时观察器通过持锁连接读取审批；API提交后、加锁前另有独立连接读取证据，避免把未提交数据当作已持久化。没有替换Engine、伪造异常或用测试进程代替Worker。

## 两次真实强杀的结果

|项|拒绝路径|同意路径|
|---|---|---|
|原工单|`d8c8a5e4-3f04-449b-8fd5-3599c1871492`|`48d0e666-6223-4d13-8375-e8a7c888842b`|
|审批决定|false|true|
|审批人|reviewer|reviewer|
|强杀退出码|137|137|
|Worker宿主PID变化|12863 → 13349|13349 → 13874|
|启动至API任务done|2.813秒|2.843秒|
|原工单终态|rejected|refunded|
|checkpoint数量|4 → 6，原记录保留|4 → 6，原记录保留|
|退款效果|没有产生退款|原工单产生RF-1004唯一台账，15900分|

两条工单的审批决定、审批人、理由和保存时间逐字段保持一致；原run_id/thread_id和历史checkpoint保留，approval interrupt消失，最终结果来源为human。每条工单审计只有create和对应的approve:no/yes，无管理员retry。调查成功attempt保留；强杀的未提交审批尝试没有计数，恢复后才有一条成功的approval attempt。

Worker停止与恢复后，各对两条工单提交一次同向重复审批和一次反向审批，合计8次请求均409。恢复后再次取完整快照与尝试前相同，无新审计、审批或退款，不能改写已保存决定。

第三条工单 `ba7489e1-13f0-4c9c-8d58-6acee949cfb6` 对同一RF-1004再次调查并批准，最终already_refunded，已有退款台账内容不变。它同时证明两次故障后Worker继续处理新任务。

最终8007 QA：3条工单、3条done任务、3条审批、6条成功attempt、6条审计、1条退款、18个checkpoint。三条工单的model_calls/input_tokens/output_tokens全部为0，退款是数据库模拟台账，无真实支付。

## 回归与主环境保护

脚本新增 `--step 1.6`，默认仍为1.5。共用观察器增加审批记录、interrupt内容和审批表查询观测；原调查场景提取为独立函数。为检查共享改动，在既有8006独立QA完整回归1.5，25项检查全部通过，强杀后恢复为already_refunded，原RF-1001退款不变。8006新增2条合成工单，共4条工单、4条done任务、1条退款，没有删除1.5历史数据。

两次验收各自比较主环境13张业务/checkpoint/RAG表的行数和内容SHA-256，均完全一致，不包含动态心跳。8003三服务仍healthy；8007、8006已正常停止并保留卷，无清理错误，8004旧QA保持停止。最终容器退出0是正常清理，不是强杀时的退出码；137与启动前后状态另存于报告。

## 证据与复验

- [1.6机器可读报告：52项检查、审批前后、强杀与恢复快照](approval-crash.json)
- [1.5共享脚本回归：25项检查](investigation-regression.json)
- [真实Worker日志](worker.log.txt)、[最终环境状态](environment-final.txt)
- [验收脚本](../../scripts/worker_crash_qa.py)、[容器观察器](../../scripts/worker_crash_probe.py)

主环境须以demo运行，Docker引擎已启动，已有与主环境相同的 `resolveflow:local` 镜像，8007端口可用。宿主Python只需标准库，镜像含观察器依赖。运行：

```powershell
python scripts/worker_crash_qa.py --step 1.6 --report validation/approval-crash-recheck.json
```

固定使用公开QA变量与空Compose环境文件，不加载主 `.env`，不构建镜像。脚本每次新增三条工单、三条审批，退出时停止QA并保留数据，报告必须使用新路径。RF-1004已有退款时，同意路径预期为already_refunded；拒绝路径验证已有台账完全不变。首次空库的本报告实际覆盖了拒绝不退款、同意首次退款及重复退款拦截。

## 边界

这是“审批事务已提交、Worker已领取任务、尚未读取决定和恢复图”的故障点；未测试审批保存事务中断，也未测试退款提交后checkpoint保存前的窗口。恢复从同一LangGraph thread继续，任务采用至少一次执行与退款唯一键，不能宣称exactly-once。

脚本显式启动Worker，不证明Docker自动拉起。仍不覆盖真实模型请求、PG断网、多Worker、整机故障或云端部署。远程CI待阶段1.10推送后核对。
