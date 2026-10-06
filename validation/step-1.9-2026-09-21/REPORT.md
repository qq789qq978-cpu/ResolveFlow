# 1.9 多Worker、停滞、超时与长事务边界

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

日期：2026-09-21。结论：本步完成；最终28项真实场景检查、42项基础测试、14项PostgreSQL集成测试通过。修复已更新本机8003，三服务healthy，主库13张表数据指纹不变。

## 环境与隔离

- 独立项目 `resolveflow-qa-step19`，端口8010，PostgreSQL17、API和两个实际 `python worker.py` 容器；Compose使用 `--scale worker=2`。
- 全部demo，公开测试凭据，零模型调用、零真实支付。主环境仅做只读指纹比较，最终正常更新已测试镜像。
- 测试在QA库通过定向退款触发器/advisory lock和订单表读锁制造阻塞，不替换业务Engine或模拟Worker领取任务。
- 每次完整复验新增两张合成订单、五条工单、两条模拟退款；保留历史和卷。临时触发器及函数已删除。测试锁有180秒失联释放上限。
- 最终镜像：`sha256:77aed6dc7dafa9cbef5b53bc24fa54748adad342d5bceadcfec8bdd9b7b864f9`。

## 实测结果

|场景|证据与结果|
|---|---|
|停滞隔离|RF-1904，工单 `dc29e285-8eb7-4fd4-8e2d-1530dff802f0` 退款写入受阻35.016秒，原任务行锁和execute checkpoint保持，另一Worker完成RF-1002自动拒绝。解除测试阻塞后原任务一次完成退款。|
|心跳与长事务|停滞期间两个Worker均healthy，指标workers_online=2；队列连接保持idle in transaction超过30秒，未提交尝试仍为0。健康检查只能证明心跳活着，不能证明业务有进展。|
|管理员重试|对受锁保护的运行中任务重试，修复前请求3秒仍未返回；修复后0.016秒返回409，无额外审计、状态重置或重复执行。失败任务正常重试仍返回202。|
|两Worker并发|RF-1905的两条工单 `10ba45e1-ec75-41e2-9a41-020625654c02`、`1a9c5f7d-2bfb-41ae-b5c3-3f2fd2351e9b` 同时阻塞于退款插入。数据库客户端地址分别对应两个真实Worker，两个工单行均被锁定。解锁后分别refunded/already_refunded，各一次成功尝试，只形成同一条29900分台账。|
|SQL超时与重试|工单 `c977b06e-86dc-4fc6-94fc-4b66f613a364` 的真实MCP订单查询受阻。三次尝试分别10.883、10.970、10.927秒退出；失败后的间隔2.045、4.070秒。首次观察阻塞后38.297秒进入failed，任务锁释放，PG中无残留受阻订单查询。|
|超时后恢复|移除阻塞后一次管理员重试，原工单恢复为auto_rejected；保留三次失败及一次成功历史，审计为create/retry，未重新提交工单。|
|运行稳定性|两个Worker在全部最终场景中的PID和启动时间不变；五条工单均零模型调用。主环境13张业务/checkpoint/RAG表从基线、验收到部署后内容一致。|

28项断言、每次事务活动、Worker身份、工单状态、台账、checkpoint和完整快照见 [final-verified.json](final-verified.json)。测试是特定双副本场景下的至少一次执行和幂等验证，不是分布式exactly-once或任意并发规模的证明。

## 发现与修复

1. **管理员请求无限等任务锁**：`jobs.retry` 改为 `FOR UPDATE NOWAIT`，锁占用转为现有409状态冲突；没有取消任务或抢走执行权。新增queued/failed两种受锁状态的API回归。
2. **MCP退出后数据库仍受阻**：初版每次约18秒客户端超时，三次失败后仍留下三个PG查询，最长已等待53秒。客户端退出并不保证PG立刻取消在途SQL。现在仅给MCP子进程连接增加单条SQL 10秒 `statement_timeout`、连接5秒限制，保留原search_path等options；数据库先取消查询，MCP返回普通工具失败，队列完成有界重试。未改全局数据库超时。
3. **长事务吞掉重试间隔**：原 `now()` 是事务开始时间，18秒失败后预定2/4秒已过去，实测只隔0.029/0.027秒就再次尝试。改用失败时的 `clock_timestamp()`，并增加慢失败回归；最终实际间隔符合预期。

修复代码为 `jobs.py`、`mcp_gateway.py`，没有调整业务退款规则、审批权限或支付行为。

## 保留的失败与校准记录

- [baseline.json](baseline.json)：首次旧版复现已证实管理员等锁；解锁后的HTTP终态断言失败，原记录没有保存该响应，不能仅凭这份记录判断业务异常。脚本后来增加稳定终态等待并保存响应，再做独立数据库核对。
- [baseline-verified.json](baseline-verified.json)：同一旧镜像重跑，12项检查通过，管理员请求3秒超时、任务随后正常完成；主数据不变。这是旧问题的复现通过，不代表旧版已修复。
- [timeout-leak-before-fix.json](timeout-leak-before-fix.json)：仅修复NOWAIT后，双Worker与幂等已通过；第20项发现三个受阻PG查询仍存活，完整测试未通过。此记录也证明旧重试间隔失效。
- 该次历史失败工单 `8d230849-823f-4e65-8cde-1c6b154e557b` 在最终修复后单独经管理员重试恢复为auto_rejected，见 [historical-remediation.json](historical-remediation.json)。不把这次历史处置算作最终28项场景的自动恢复。
- [postgres-tests-initial.txt](postgres-tests-initial.txt)：初次把PG集成与故障脚本共用数据库并行执行，故障脚本收尾停库时集成尚未结束，出现1失败、6错误。改为故障脚本全部完成后单独启动QA数据库跑集成，最终14项通过。最初42项基础测试也保留于unit-tests-initial.txt。

全部原始失败记录保留，最终通过数只采用最终镜像及相应完成的测试结果。

## 回归、部署和复验

- [unit-tests.txt](unit-tests.txt)：42项通过，21.93秒。
- [postgres-tests.txt](postgres-tests.txt)：14项通过，21.38秒；仅一个依赖弃用警告。随机schema隔离；新增锁冲突和慢失败间隔测试纳入现有CI的test_jobs.py入口，但本步未运行远程CI。
- [main-deployment.json](main-deployment.json)：8003 API、Worker升级为最终镜像，DB容器保留，三个服务healthy；部署前后及整个步骤主库13表指纹一致；8010四个容器均exited、数据卷保留。
- 历史处置后QA public业务数据共14条工单、14条任务、6条模拟退款。历史测试数据不删除；最终独立验收只对应其中新增的5条工单。

在仓库根目录运行（宿主Python仅需标准库；现有镜像、主环境demo运行，8010可用）：

```powershell
python scripts/multi_worker_qa.py --report validation/multi-worker-recheck.json
```

报告路径必须未存在；不要与其他使用同一QA数据库的测试并行运行。`--baseline`只用于旧镜像问题复现，当前修复镜像应该无法满足“请求等锁超时”的旧问题断言。

## 仍然存在的边界与后续

- **无整张工单执行期限**：MCP的10秒单条SQL、15秒读取、30秒会话是不同层次的局部限制；单次模型请求也不代表整个Agent的总时限。本步无付费模型或静默网络分区验收。
- **非MCP数据库操作仍可能长期等待**：退款、checkpoint、队列及API的其他SQL没有统一statement/lock timeout。35秒退款阻塞依赖解除外部测试锁才恢复，没有证明无限阻塞自动自愈。长期事务会占用连接、任务锁，并可能影响数据库维护。
- **心跳不是进度**：当前没有任务进展探测或停滞告警。不能只看到healthy就认定所有工单正常，也不应把尚在执行的任务强制标记failed后重新投递。
- **扩容有范围**：只验收两个副本，未做连接池/耗尽或吞吐压测；Compose仍共享遗留SQLite目录，PG模式不以其为业务或checkpoint主存储，不能扩展为SQLite多进程能力保证。启动建表/索引仍不是正式migration。
- 上述超时、长事务和容量工作留在3.7，进展与连续失败告警留在3.8，正式migration留在3.1–3.3。
