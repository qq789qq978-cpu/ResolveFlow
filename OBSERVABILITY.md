# 3.8 关联日志与运行告警

本步新增JSON关联事件、管理员告警视图及独立只读monitor服务。告警只提示和记录，不重试工单、不更改审批、checkpoint或退款。主环境仍step36；本步仅在全新隔离demo环境验收，部署与完整交付按3.9执行。

## 如何看告警

管理员登录后“运行监控”显示当前告警、检查时间、处置说明及工单入口。运营/审批角色不能访问`GET /api/alerts`。API每次只读重新计算，浏览器每4秒刷新；读取失败显示“状态未知”，旧结果不能当成恢复。

Docker Compose新增常驻monitor，使用已有rf_readonly账号，无管理员/业务写权限、无模型密钥。它在浏览器关闭时仍每5秒检查；只把状态变化写到本地容器日志。未接邮件、Slack、Webhook或外部值班平台，不会向他人发消息。

|告警代码|默认条件|建议操作|
|---|---|---|
|worker_offline|20秒内新鲜心跳数少于预期Worker数量（默认1）|检查Worker容器、数据库连接和实际扩缩容数量；仅API健康不能证明Worker正常|
|task_stalled|任务处于running且持续至少120秒，队列任务仍未提交完成|用run_id/attempt_id查看执行阶段与SQL等待；保留原任务及审批，先定位阻塞|
|queue_delayed|任务可执行时间已过60秒，仍为queued/approval_queued/retrying|检查Worker容量、队列阻塞；未到available_at的退避任务不算延迟|
|consecutive_failures|最近3条已提交尝试（按记录id排序）全部失败，且均在近900秒|检查共同错误类型和依赖；跨工单统计，有成功插入即打断连续失败|
|job_failed|任务已耗尽重试、队列状态failed|修复原因后由管理员显式重试；先核对已有审批、checkpoint与退款结果|
|monitor_unavailable|独立monitor无法读取完整快照|状态未知；检查数据库/权限/监控日志。保留已有告警，不声称已恢复|

`task_stalled`是“持续运行过久”预警，**不是已证明死锁，也不是逐节点进度探针**。心跳独立更新，不能抑制该预警。基础设施断开导致的反复领取仍保留原running起点，避免无限重置计时隐藏异常；正常重试退避后重新执行会重新计时。等待人工审批/核查的已完成队列任务不触发运行或排队预警。3.7整体期限继续负责终止任务，本步不改变执行权限。

## 配置和启停

配置示例在`.env.example`，本步没有改现有`.env`。API和monitor共享阈值；修改后需在明确部署步骤重建对应容器：

|变量|默认值|允许范围|
|---|---:|---:|
|RF_EXPECTED_WORKERS|1|1–64|
|RF_ALERT_WORKER_OFFLINE_SECONDS|20|10–3600秒|
|RF_ALERT_RUNNING_SECONDS|120|2–3600秒|
|RF_ALERT_QUEUED_SECONDS|60|2–3600秒|
|RF_ALERT_FAILURE_COUNT|3|2–20次|
|RF_ALERT_FAILURE_WINDOW_SECONDS|900|10–86400秒|
|RF_ALERT_POLL_SECONDS|5|1–60秒|
|RF_ALERT_STARTUP_GRACE_SECONDS|30|0–300秒|

真实双Worker部署应设置RF_EXPECTED_WORKERS=2，扩缩容同时调整；旧Worker异常退出的心跳会在时限内短暂残留。实际发现延迟包含心跳过期、轮询间隔和数据库查询耗时。monitor启动宽限只抑制容量不足告警，API仍显示即时状态；超过宽限后的容器启动过程也可能合理触发短暂告警。

新版本Compose默认启用monitor。以下是部署后的查看命令，不是对当前主环境的自动部署：

```powershell
docker compose ps
docker compose logs --since 15m monitor
docker compose logs --since 15m resolveflow worker
docker compose exec monitor python monitor.py --once
```

`--once`只读输出快照：退出0表示本次无告警，2表示有告警，3表示无法观测。适合部署后的主动检查，不会修改数据。手动运行Python时需显式提供目标READONLY_DATABASE_URL，不自动借用管理员连接。

monitor健康检查同时检查最近循环时间和读取成功状态；数据库中断会标不健康，但进程继续尝试读取。进程退出由Compose的unless-stopped策略管理；宿主/Docker整体失效或monitor本身停止时，**不能依靠它给自己发告警**，仍需要外部运行平台观察，当前未实现外部通知投递。

## 如何关联日志

应用事件是单行JSON，包含UTC timestamp和event。只允许关联字段与固定状态字段，不记录工单文本、政策查询、审批理由、订单内容、HTTP头、模型响应、连接串或异常详情。Uvicorn访问日志关闭，避免URL查询内容进入访问日志；诊断异常只记录类型。QA以合成秘密标记检查实际服务日志。

```text
http_finished(request_id, run_id, route, http_status)
  ↕ 相同request_id
job_enqueued / job_requeued(request_id, run_id, kind, actor)
  ↕ 相同run_id
job_started(worker_id, run_id, attempt_id, attempt, kind)
  → task_started(task_token)
  → task_deadline（若达到期限）
  → task_cleaned
  → job_finished(success, error_type, elapsed_ms)
```

每个HTTP请求生成新的X-Request-ID响应头，忽略调用方提供的关联ID；经队列异步边界用持久run_id连接。每次领取生成新attempt_id，跨重试、审批继续、管理员重试均可区分；attempt计数本身可能因事务回滚重复，不能当唯一键。task_token对应该尝试的PostgreSQL application_name，供有权限的维护者查询pg_stat_activity；应用账号没有新增查看他人连接的权限。

job_finished仅在数据库事务提交成功后发出。连接/提交失败写job_interrupted；进程强杀可能只有job_started，没有完成日志。日志是诊断证据，不代替退款台账、审批记录和checkpoint，也不保证崩溃时事件绝不丢失。数据库提交成功但日志输出前崩溃，应以数据库核对结果，不能凭缺少结束日志再次退款。

API、Worker、monitor的Docker日志各保留最多3个10MiB文件，防止无限增长；不是长期审计备份。告警去重仅在monitor进程内存维持：同一code/run_id持续存在不重复触发，消失写resolved；进程重启会重新报告当前事件。读取失败保留上次活动告警；每类任务最多返回50条并提示truncated，截断时不把未列出的旧事件误判为恢复。当前无持久告警确认/静默管理。

## 排障与验证

先核对告警时间、run_id、attempt_id及错误类型，再检查目标任务是否已提交退款或保存审批。排障只能在明确授权的目标环境进行；不因告警自动杀数据库会话、删除卷或跳过规则。错误恢复后，任务状态、告警恢复事件和模拟退款唯一性应一起核对。

本步隔离脚本：

```powershell
python scripts/observability_qa.py --project resolveflow-qa-step38-new --image resolveflow:step38-release --report work/step38-new-report.json
```

必须使用新项目/报告，脚本仅注入该项目的Worker、数据库和合成退款阻塞；退出停止容器并保留所有卷。测试需要Docker和本地镜像，不调用付费模型。实际结果见[3.8验收报告](validation/step-3.8-2026-09-23/REPORT.md)。
