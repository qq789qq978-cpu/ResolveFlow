# 运行时连接与执行期限（3.7）

本步使用独立 demo 环境验收，不增加付费模型调用或真实退款。主环境仍为 step36；本地 step37 镜像及代码的验证不表示主环境已升级。实测结果见 [3.7报告](validation/step-3.7-2026-09-23/REPORT.md)。

## 默认限制

|配置|默认值|范围/用途|
|---|---|---|
|RF_DB_POOL_MAX|8|2–32，单个API进程的业务连接池|
|RF_DB_POOL_MAX_WAITING|16|1–128，每个池等待队列上限|
|RF_DB_POOL_TIMEOUT_MS|2000|100–10000，池借用等待期限|
|RF_DB_STATEMENT_TIMEOUT_MS|10000|100–120000，非MCP运行时单条SQL期限|
|RF_DB_LOCK_TIMEOUT_MS|5000|100–120000，非MCP锁等待期限|
|RF_DB_IDLE_TRANSACTION_TIMEOUT_MS|240000|1000–900000，闲置事务会话期限|
|RF_TASK_TIMEOUT_SECONDS|180|1–600，每次任务尝试的子进程执行期限，含导入、Engine初始化、MCP、模型、checkpoint及退款|

连接建立使用5秒期限；清理预留连接建立2秒、清理SQL 2.5秒。MCP保持10秒SQL/锁等待、客户端15秒读取/30秒会话期限，不随业务SQL配置放宽。语义检索仍有5秒整体预算和每进程4个并发槽，忙时按既有逻辑回退BM25。原DSN的search_path等options保留，运行时期限追加覆盖同名配置。迁移DDL仍遵守独立迁移约定。

每个Worker主池固定最多4连接、独立心跳池1连接；每次仅运行一个任务子进程，子进程业务池最多4、checkpoint专用1，MCP是串行短连接；额外预留业务/只读各1条清理连接。池按需从0增长，空闲30秒后收缩；归还事务提交/回滚后DISCARD ALL，借出先检查死连接。重置异步执行，刚归还的一瞬间仍可能收到503，后续读取可重试。池满超过等待期限、等待队列满、连接拒绝或SQL期限到达时，API返回脱敏503及Retry-After: 2。写请求不要自动重发；先查询原工单状态。

配置按进程生效，没有全局连接池。容量规划保守预留API上限+4个语义槽+每Worker约16个连接（包含启动/健康检查临时连接）以及维护/备份和PostgreSQL保留槽。默认单API、双Worker约44个运行时连接预算；不能据此声称任意副本数通过容量测试。增加API进程或Worker副本前重新计算并在隔离环境压测。

## 事务、取消与重试

队列保留跨执行过程的FOR UPDATE SKIP LOCKED长事务，以原行锁防止重复领取。RF_DB_IDLE_TRANSACTION_TIMEOUT_MS必须至少为任务期限+30秒，否则Worker启动失败。任务期限不是全工单的截止日期：每次自动尝试分别计时，失败最多3次、从失败时刻起退避2秒/4秒；排队、等待人工审批、管理员重试不在该180秒内。领取前后SQL、预留清理连接和结果入库有各自期限，所以端到端耗时包含这些开销，不保证恰好180秒返回。

TaskRunner在启动子进程前预留受限角色清理连接；不足时不启动业务执行。到期先强制结束并等待子进程，再通过对应角色终止仅本次随机application_name的数据库会话，确认消失后才把期限异常返回队列。正常退出/崩溃也清理。Linux使用父进程死亡信号保护任务与另起会话的MCP子进程，Compose Worker启用init回收孤儿；Windows使用taskkill进程树，但本步正式验收环境是Linux容器。

SQL取消、锁超时和任务期限计入业务尝试；真正数据库断开/闲置事务被服务器终止按基础设施失败处理，回滚领取后重试而不耗尽业务次数。心跳使用独立连接，仍可能在任务停滞时正常；告警属于3.8，本步不实现。

网络分区或数据库整体不可达时无法保证立即完成服务端取消；清理失败以基础设施异常处理，不报告清理成功，服务器SQL/闲置事务期限为后备。父Worker被SIGKILL也无法执行清理代码。尚未验证静默丢包/网络分区、Windows父进程异常死亡、真实模型中断及真实支付撤销。已经提交的模拟退款不能因超时“撤销”，依靠订单唯一键和原checkpoint恢复；人工决定、后端权限和退款幂等保留。

## 验证与运行

只在新隔离项目执行，脚本拒绝复用工作目录/报告，结束stop并保留卷：

```powershell
docker build -t resolveflow:step37 .
python scripts/runtime_limits_qa.py --image resolveflow:step37 --project resolveflow-qa-runtime-recheck --report work/runtime-recheck.json
```

默认端口8020，可用--port更改。业务验收含双Worker、非MCP退款SQL超时、保留审批恢复、任务整体期限三次耗尽、锁释放、无残留查询、原checkpoint恢复和重复退款拒绝。故障门仅装在全新QA数据库。不要将故障脚本连接主库，不删除历史卷。

PG专项只允许RUN_PG_TESTS=1并指向独立测试集群：`test_runtime_limits_pg.py`会短时耗尽该集群所有连接，不能在业务库或与其他PG测试并行执行。按requirements.lock安装依赖后运行pytest；或用scripts/runtime_test_image.Dockerfile构建含测试源码的隔离镜像，测试镜像不包含.env、work、data等私有目录。基础测试运行时不设置DATABASE_URL。CI入口已加入这些检查，远程执行留到3.9统一推送后确认。

历史1.5–1.9的故障脚本故意持锁超过35秒，因此测试配置显式提高非MCP SQL/锁期限至120秒；这不是默认运行配置，也不代替本步的默认期限测试。
