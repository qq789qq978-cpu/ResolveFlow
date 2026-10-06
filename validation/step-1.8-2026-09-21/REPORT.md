# 步骤 1.8 — PostgreSQL 短暂断开后的连接、事务与任务恢复

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

日期：2026-09-21。结论：**通过，已复现并修复故障**。34项真实容器故障检查、42项基础测试、11项PostgreSQL集成测试通过。修复部署至8003主入口，主库数据不变。

## 原问题及修复

代码基线 `0471029`，原镜像 `sha256:6412f843e00996f04c6b42a4dc357d9b0355e33db973351c0d21d42b3aa17a5a`。在独立8009 QA中，真实Worker调查时停止PostgreSQL，再启动数据库，工单 `f006b4ba-391e-4068-a632-920b7903c9c1` 因同一个失效PostgresSaver连接连续出现3次OperationalError，耗尽业务重试并变成failed。数据库停机时health、工单查询、创建接口均500。旧版失败报告保留，不计为验收通过。

已修改：

- `worker.py`：启动准备和Engine创建进入可重试循环；基础设施异常后关闭旧Engine并重建PostgresSaver，间隔3秒重试，保持Worker进程和worker_id；停机时心跳清理失败仅记录异常类型。
- `engine.py`：PG连接建立设置5秒超时，初始化失败关闭已创建的资源，避免不断重连时遗留资源。
- `jobs.py`：数据库OperationalError/InterfaceError向Worker传播，回滚任务事务、释放领取锁，不消耗业务重试预算。成功处理和状态写入放进嵌套事务保存点；普通SQL错误先回滚保存点，随后可用同一外层事务记录重试，继续持有队列行锁。普通业务异常仍最多自动重试3次。
- `operations.py`：数据库暂不可用返回503和固定中文提示，日志只记异常类型，不返回连接串或数据库报错正文。写请求失败仍须先查询状态再重试，不自动重复提交。
- 新增可复现的停库验收脚本，以及真实SQL约束错误、失效checkpoint连接、不泄露错误详情的503三个PG回归用例。

新镜像：`sha256:e90cbe09ef2a60cd74b762373c71dcb6b93c2780a61ae0e610f1e328d975d30a`。业务规则、退款唯一约束和审批决定语义保持原有设计。

## 故障注入方式

固定独立Compose项目 `resolveflow-qa-step18`（`127.0.0.1:8009`），独立PG和卷，使用demo、公开测试变量。API/Worker运行同一新镜像。只停止/启动该项目的PG，**故障验收期间不重启API或Worker**。

调查场景先用测试订单表锁确认真实MCP查询已执行、任务行锁持有、checkpoint待investigate。审批场景先由真实API保存同意决定，再用审批表锁确认Worker已领取approval任务、checkpoint仍待approval。随后停止PG；服务退出终止所有数据库连接并释放测试锁，不使用假异常或只终止测试进程来替代停库。

PG确认exited后，实际请求health、工单查询和新建工单，均返回503；Worker进程仍存活。两次从发起停止至发起启动分别11.984/12.000秒（包含停止命令及观测开销）。启动PG后重建测试观察器的连接；业务API/Worker自行恢复，比较客户端PID与启动时间证明它们未重启。

## 修复后的结果

|观察|调查中断|审批任务中断|
|---|---|---|
|工单|`387e9a70-ade0-47fe-8bbe-25c4b11bbf57`|`24e20ea6-c936-4133-8ef0-6d8687fe8f3c`|
|合成订单|RF-1002|RF-1004|
|PG启动至观测原任务done|8.031秒|8.187秒|
|结果|auto_rejected|refunded|
|本轮任务已提交attempts|1|1条approval成功尝试，原调查尝试另保留|
|checkpoint|原thread历史保留，图完成|原thread历史保留，审批恢复完成|
|审批|无新增审批|决定、审批人、理由、时间不变|
|退款|无退款|仅1条15900分台账|
|人工重试|无|无|
|API/Worker进程|原PID、启动时间均不变|原PID、启动时间均不变|

数据库故障没有新增业务失败尝试，任务最终last_error为空；原工单审计只有create，以及人工路径的approve:yes，无管理员retry或重建工单。停机时两次创建请求均没有落库，正式验收前后只增加3条工单与3条任务，无孤立记录。

后续工单 `22b22641-103b-4430-b050-eeeea4fca417` 再对RF-1004提出申请并审批同意，结果already_refunded，原退款行完全不变，证明恢复后能继续处理新任务。所有业务执行均为demo、零模型调用和token，无真实支付。

## 回归、历史失败工单与主环境更新

- 基础测试：**42 passed**，覆盖Engine、MCP、退款规则、冲突及RAG。
- PG集成：**11 passed**。新增用例实际触发NOT NULL SQL错误，确认保存点回滚后可以记录失败并再次成功；终止真实checkpoint连接，确认任务仍queued且attempts为0，换新Engine后完成；模拟连接异常验证三个API均503且不泄露异常中的连接信息。已有审批、幂等退款、耗尽后管理员重试、并发锁与重放用例全部通过。输出中的1条依赖弃用警告不影响测试结果。
- 旧版本复现工单已经failed，不能混入“新版本自动恢复”的证明。修复验收后，另用管理员接口对这条历史QA工单重试一次，成功auto_rejected；保留旧3条失败尝试及管理员retry审计，单独保存补救报告。没有改写原失败证据或删除工单。

最终QA有4条工单、4条done任务、2条审批、1条模拟退款。`recovery-verified.json` 的QA汇总记录在历史失败工单补救之前，因此当时包括那条failed任务；最终状态结合 `baseline-remediation.json` 查看。

8003在全部测试通过后更新到同一个新镜像。主库13张业务/checkpoint/RAG表的行数及内容SHA-256，在旧版复现前、新版验收前后、主环境更新前后完全相同；不比较动态心跳。主环境三服务healthy。QA及其他历史验收容器停止并保留卷。主环境更新会正常重建API/Worker容器，与故障验收期间不重启QA客户端是两个不同操作。

## 证据与复验

- [旧版故障复现](baseline-outage.json)、[旧版Worker日志](baseline-worker.log.txt)
- [修复后34项真实故障检查](recovery-verified.json)、[新Worker重建连接日志](fixed-worker.log.txt)
- [42项基础测试](unit-tests.txt)、[11项PG集成测试](postgres-tests.txt)
- [历史失败工单单独补救](baseline-remediation.json)
- [主环境更新前](main-update-before.json)、[更新后](main-update-after.json)、[部署镜像与健康状态](main-deployment.json)
- [收尾环境](environment-final.txt)、[数据、部署源码与敏感信息核对](verification-summary.json)
- [验收脚本](../../scripts/database_outage_qa.py)、[数据库观察器](../../scripts/worker_crash_probe.py)

主环境需以demo运行，Docker已启动，已构建包含当前修复的 `resolveflow:local` 镜像，8009可用。宿主Python只需标准库：

```powershell
python scripts/database_outage_qa.py --report validation/database-outage-recheck.json
```

脚本允许测试候选镜像与主入口镜像不同，但要求QA API/Worker同镜像；不读取主 `.env`，不构建镜像，主环境只读数据指纹。每次新增3条工单、2条审批；已有RF-1004退款时走already_refunded并核对原台账。结束时停止QA保留数据，报告路径必须新建。

本次覆盖PG服务主动关闭连接和连接拒绝后的恢复；没有验证静默丢包、网络分区、任意长时间不可用、多Worker或真实模型请求。5秒连接建立超时不等于所有SQL/网络操作都有5秒截止时间。远程CI待1.10统一推送核对。
