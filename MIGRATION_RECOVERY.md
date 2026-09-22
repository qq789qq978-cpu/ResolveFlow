# 迁移失败与恢复手册（3.3）

只在维护窗口恢复数据库。先停API/Worker接单，保留失败输出、当前镜像与数据库备份；不要删除数据卷、清空版本表或手工补版本号。任何SQL都应确认连接的数据库/schema；本文中的恢复实验只在独立QA库执行。

## 先判断失败边界

迁移CLI失败退出1，输出不含连接密钥。`phase`表示发生错误的阶段：

|阶段/症状|已经提交的内容|下一步|
|application_transaction：SQL错误、进程被结束|本次应用DDL/stamp仍在同一事务，连接断开后回滚；既有业务数据保留|排除SQL、权限、扩展或锁冲突，再执行原prepare/adopt|
|checkpoint_setup：库自身setup中断|应用事务可能已提交；LangGraph每条DDL和版本记录可能分开提交|核对已记录的连续版本与实际结构后prepare继续，不回退应用版本来假装全部回滚|
|demo_seed：演示播种失败|应用和checkpoint结构已提交；该次订单/政策/发布事务全部回滚|prepare补齐结构；确认原目标是空demo库，再显式bootstrap-demo|
|readiness：最后检查失败|前面的独立事务可能已经提交|check并核对结构，不把非零退出当作“数据库从未改变”|
|preflight：锁/配置/版本不匹配|尚未进入本次应用迁移，或check检查失败|核对同一DB/schema、版本与维护窗口；未知版本或结构需调查/恢复|
|demo_recovery：显式补齐命令失败|它的单个事务会回滚；若进程在提交后才断开，数据可能已完整提交|先check和核对内容；已有数据不能重复播种|

所有应用数据库准备、显式demo恢复共用schema级advisory lock；进程死亡后由PostgreSQL释放，释放可能有短暂TCP检测延迟。忙库锁表等待最多5秒。锁只协调这些入口，不替代3.6的权限隔离或防止人工外部DDL。

## 继续迁移

以下命令使用Compose的migrate服务，连接容器数据库，不使用宿主便携库的DATABASE_URL：

```powershell
docker compose stop resolveflow worker
docker compose run --rm --no-deps migrate python db_migrate.py check
docker compose run --rm --no-deps migrate python db_migrate.py prepare
```

若应用事务失败后仍是无版本旧基线库，第三行改用`adopt`。不能对未知结构直接stamp。CLI的check仅表示结构/版本就绪，不保证业务数据已经完整播种。

LangGraph固定使用langgraph-checkpoint-postgres 3.1.2及其10条迁移；`checkpoint_state.py`验证包版本、迁移SQL指纹和冻结的11种结构状态。允许连续已记录步骤，以及“下一条DDL提交、对应版本未记录”的单步窗口，随后仍由PostgresSaver.setup完成DDL。不会改写checkpoint_migrations，缺历史、非连续/未来版本、与版本不符的表结构都拒绝。升级库依赖时须重新审阅迁移并建立新契约，不能只改依赖版本绕过检查。

### 并发索引被取消

PostgreSQL取消CREATE INDEX CONCURRENTLY可能留下同名但无效的索引；再次IF NOT EXISTS会跳过它。迁移和应用启动都会检查`indisvalid/indisready`，不会把它当作成功。

先停止API/Worker，并只读检查具体索引：

```sql
SELECT n.nspname, t.relname AS table_name, i.relname AS index_name,
       x.indisvalid, x.indisready
FROM pg_index x
JOIN pg_class i ON i.oid=x.indexrelid
JOIN pg_class t ON t.oid=x.indrelid
JOIN pg_namespace n ON n.oid=t.relnamespace
WHERE n.nspname='public'
  AND t.relname IN ('checkpoints','checkpoint_blobs','checkpoint_writes');
```

确认是库自身创建的索引、定义与冻结契约一致、没有仍在运行的索引构建后，维护窗口内对**实际受损的那个索引**执行REINDEX。以下只是本次QA验证过的名称，非所有环境通用命令：

```sql
REINDEX INDEX public.checkpoints_thread_id_idx;
```

再次检查有效性，再prepare/check。REINDEX可阻塞业务，不能在正常接单时盲目执行。本工具不会自动删索引、删checkpoint或修改系统catalog。

## 新装demo的播种恢复

3.2的prepare刻意不重播种已有库，因此应用事务已提交后再执行prepare不会猜测原来的demo意图。确认原计划就是新装demo，且服务已停止后：

```powershell
docker compose run --rm --no-deps migrate python db_migrate.py prepare
docker compose run --rm --no-deps -e MODE=demo migrate python db_migrate.py bootstrap-demo
docker compose run --rm --no-deps migrate python db_migrate.py check
docker compose up -d --wait --wait-timeout 180
```

bootstrap-demo要求MODE=demo、结构就绪，锁住应用与checkpoint表后验证：除版本记录及初始空policy_head外没有订单、任务、审批、审计、政策、向量、心跳或checkpoint数据。检查与订单/知识/发布播种在同一事务内。发现任何既有业务数据就拒绝；连续调用不会覆盖已有演示库。live库不自动补齐演示数据，显式改为demo表示操作者改变了空库用途，不能由恢复程序代为推断。

## 回滚已提交的可逆修订

事务自动回滚与已提交版本downgrade是两回事。仅当该修订有经过演练的逆操作、没有依赖新字段的写入、应用兼容性已核对时，才在备份后的维护窗口执行精确目标downgrade。随后核对结构、数据、序列、版本和旧镜像，再恢复服务。

本步在临时迁移目录加入测试修订：新增一个nullable列，升级后当前应用因未知结构拒绝启动，精确降回rf_core_0001后原数据和就绪检查恢复。**这个修订仅用于QA，未加入生产迁移版本线。** 当前core/vector基线没有安全删除式downgrade，执行降到base会拒绝并回滚。没有一条适用于所有主库的通用downgrade命令。

## 不可逆变更：恢复备份或向前修复

删列、缩窄类型、覆写内容等已经丢失的信息，不能靠重新加列恢复。不要把“表结构恢复”当作“业务数据恢复”。本步用合成订单实测删除used列后以FALSE重建，原使用状态丢失；恢复备份到独立库后，20张表的数据指纹全部一致，原checkpoint可读。

恢复步骤：

1. 立即停止写入，保留受损库和事故时间，不直接覆盖唯一副本。
2. 选择变更前备份，核对数据库名、PG主版本、扩展、镜像/代码版本、时间与SHA256；明确备份后新增数据的可能损失窗口。
3. 使用匹配版本，将备份恢复到**新的空数据库**，遇到错误立即停止。不得把restore叠加到受损库来掩盖差异。
4. 只读核对应用/库自身迁移版本、结构、表行数/指纹、序列、关键工单/退款/审批及checkpoint。使用与备份版本匹配的镜像；不让新镜像擅自升级恢复库。
5. 若需保留备份后的新增业务，先制定增量重放/对账方案或经过审阅的向前修复；不能盲目覆盖退款记录。
6. 独立验收通过后才切换连接，保留原库和旧配置作为回退依据，再逐步恢复接单。

本步演练没有生产连接切换、PITR/WAL增量恢复、自动备份保留策略或完整待审批灾备恢复；这些不会因一次合成恢复而被标记完成。3.4已建立[备份工具与清单](BACKUPS.md)；3.5已完成[独立库恢复及待审批业务验收](RESTORE.md)，仍未执行生产连接切换。

## 本步证据

[3.3报告](validation/step-3.3-2026-09-22/REPORT.md)记录SQL失败回滚、真实进程强杀、20种库迁移中断状态、无效索引修复、演示数据恢复、可逆测试修订降级及不可逆模拟恢复。主库不进行任何破坏性故障注入，无付费模型调用。
