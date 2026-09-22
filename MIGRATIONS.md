# 数据库迁移与运行说明（3.2）

2026-09-22：应用 PostgreSQL DDL 已集中到独立迁移入口。API、Worker、知识库导入及向量构建不再建表；API/Worker 启动检查应用结构、版本和 LangGraph 版本。**先完成数据库准备，再启动应用。** `Store.setup()` 保留兼容方法名，但只做只读检查，不播种数据。

## 管理归属

|归属|对象|管理方式|
|---|---|---|
|core，15张|`rf_orders`、`rf_runs`、`rf_approvals`、`rf_refunds`、`rf_reviews`、`rf_jobs`、`rf_job_attempts`、`rf_worker_heartbeats`、`rf_audit`、`rf_knowledge_documents`、`rf_knowledge_chunks`、`rf_policy_releases`、`rf_policy_head`、`rf_policy_reviews`、`rf_policy_events`|Alembic rf_core_0001|
|vector，可选2张|`rf_vector_batches`、`rf_policy_vectors`（384维）|Alembic rf_vector_0001，依赖core|
|应用迁移元数据|`rf_schema_version`|仅Alembic写入|
|LangGraph，4张|`checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes`|迁移入口调用锁定的PostgresSaver.setup；API/Worker不调用setup|
|平台扩展|`public.vector`|维护人员显式安装，应用迁移不安装/卸载|

core新库为20张表，hybrid为22张，均含心跳、4张checkpoint表和应用版本表。历史“19表指纹”不包含心跳、checkpoint_migrations和后来新增的应用版本表；本次升级另核对20张原有非心跳表。

基线冻结自ba17859，不导入会变化的运行时代码。不启用autogenerate；新增修订须显式编写并审核归属。core/vector为独立版本线，vector通过depends_on依赖core；hybrid库仅记录rf_vector_0001是依赖满足后的正常结果。未来core修订不应依赖可选vector分支。当前入口支持本次基线接管和core→hybrid扩展；新增revision时必须同时更新就绪契约和测试，不能用未知版本启动旧应用。

## 新库：Docker Compose

按README配置.env，演示使用MODE=demo，然后运行：

```powershell
docker compose up --build -d --wait --wait-timeout 180
docker compose ps -a
```

顺序为DB healthy → migrate成功退出 → API healthy → Worker。migrate是一次性任务，**Exited (0)正常**，不要求它healthy；失败时Compose不启动依赖它的API。它执行`python db_migrate.py prepare`，根据RETRIEVAL_MODE选择core/hybrid。

新装demo初始化4条合成订单及内置政策发布；新装live不植入演示订单或政策。已有库的prepare/adopt均不自动重新播种，避免更改历史内容和审核状态。live数据导入和真实审核不在本步范围。

hybrid新库先使用带pgvector的PG17镜像启动DB，并显式安装扩展：

```powershell
docker compose up -d --wait db
docker compose exec -T db psql -U resolveflow -d resolveflow -c 'CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;'
docker compose up --build -d --wait --wait-timeout 180
docker compose exec -T resolveflow python scripts/build_vector_index.py --report /tmp/vector-build-001.json
```

先配置模型与encoder，见[混合检索说明](SEMANTIC_IMPLEMENTATION.md)。prepare创建向量表但不编码；没有有效批次时按原规则回退BM25。core不需要pgvector。

## 已有旧库：显式接管

适用于startup DDL建立、结构与冻结基线一致的库。先停接单、等待在途任务结束，保留备份及旧镜像，在维护窗口运行：

```powershell
docker compose build
docker compose stop resolveflow worker
docker compose run --rm --no-deps migrate python db_migrate.py adopt
docker compose up -d --wait --wait-timeout 180
docker compose exec -T resolveflow python db_migrate.py check
```

adopt锁住该schema的应用表，再对照列、默认值、约束、索引和序列，成功才在同一事务内stamp。业务/知识/政策/向量表不重建、不清空；checkpoint由库自身维护。旧core要求hybrid时，接管core后在同一应用事务内增加向量表；扩展须提前安装。

默认prepare拒绝无版本旧库。结构漂移、部分旧表、未知应用版本、部分checkpoint schema及不支持的checkpoint版本均拒绝。不能绕过错误手工stamp。更早、缺少governance等字段的库不属于本次基线，需另写显式迁移，不能依赖启动补列。

入口使用schema范围的advisory lock，第二个迁移进程立即拒绝。接管锁表等待最多5秒；忙库失败并保留应用事务原状，停写后重试。此锁不防护任意外部DDL，数据库权限分工留到3.6。

## 非Docker运行

使用Python 3.12安装requirements.lock，配置.env的本机DATABASE_URL。迁移命令读取.env，已有进程环境变量优先，不输出密钥：

```powershell
.venv\Scripts\python db_migrate.py prepare
# 若提示现有无版本库，停服务并备份后改用adopt：
.venv\Scripts\python db_migrate.py adopt
.venv\Scripts\python db_migrate.py check
./start-local.ps1
```

prepare/adopt按库的情况选一个，不是连续执行的固定流程。start-local/run_local只检查就绪，再启动API/Worker。3.2未更新或启动便携PostgreSQL，容器主库升级不代表所有历史库都已升级。

测试可设置RF_MIGRATION_SCHEMA为已有的独立schema；准备命令将后续连接限制在该schema。应用连接的search_path也须指向同一schema；就绪检查按current_schema读取。拒绝系统schema和不合法名称。

## 只读结构核对

```text
python scripts/schema_catalog.py --profile hybrid --baseline migrations/baselines/hybrid.json --report schema-report.json
python db_migrate.py check --profile hybrid
```

catalog工具要求连接已在环境变量DATABASE_URL中提供；core-only使用`--profile core --baseline migrations/baselines/core.json`。工具只读、不stamp。已版本化hybrid库可在BM25模式运行，但core-only不能当作hybrid-ready。

报告包含列顺序/类型/非空/默认/identity/generated、约束、索引、用户触发器、RLS开关、序列定义/归属、扩展、迁移版本与未知表名；不含业务行或连接地址。结构相符不代替数据验收；授权、RLS策略内容、数据语义、非表对象和扩展兼容性不属于完整审计范围。历史基线JSON不随新版本覆盖。

原始Alembic的history和离线SQL可供审阅。部署统一使用db_migrate.py，不绕过检查/互斥锁直接操作主库。基线downgrade明确拒绝删除数据。

## 验证与边界

见[3.1报告](validation/step-3.1-2026-09-22/REPORT.md)与[3.2报告](validation/step-3.2-2026-09-22/REPORT.md)。本步验收新装、旧库接管、数据保留、真实待审批恢复、版本持久化和本机服务更新。

应用Alembic事务与LangGraph自身的autocommit setup分开执行，后者包含并发索引；演示播种也是独立事务。正常重复prepare不重新播种旧数据。跨阶段中断后的恢复/继续、不可逆变更回退和失败演练留到**3.3**，不宣称所有初始化故障都能自动恢复。
