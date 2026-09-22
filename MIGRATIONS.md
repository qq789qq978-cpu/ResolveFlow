# 数据库迁移边界与基线（3.1）

2026-09-22：已建立 Alembic 框架及冻结基线，在独立 PostgreSQL 17 / pgvector 0.8.6 环境验证，并只读核对主库。**主库未迁移、未 stamp，API/Worker 启动仍沿用既有 setup DDL。** 新装流程切换、旧库接管及部署顺序由 3.2 完成；失败恢复方案由 3.3 完成。本页命令用于隔离验证，不是当前主环境升级手册。

## 管理归属

|归属|对象|管理方式|
|---|---|---|
|应用 core|`rf_orders`、`rf_runs`、`rf_approvals`、`rf_refunds`、`rf_reviews`|Alembic core 基线|
|应用 core|`rf_jobs`、`rf_job_attempts`、`rf_worker_heartbeats`、`rf_audit`|Alembic core 基线|
|应用 core|`rf_knowledge_documents`、`rf_knowledge_chunks`|Alembic core 基线|
|应用 core|`rf_policy_releases`、`rf_policy_head`、`rf_policy_reviews`、`rf_policy_events`|Alembic core 基线|
|应用 vector|`rf_vector_batches`、`rf_policy_vectors`（384 维）|显式选择 vector 基线|
|应用迁移元数据|`rf_schema_version`|Alembic 版本记录，不属于业务表|
|LangGraph|`checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes`|锁定的 langgraph-checkpoint-postgres 3.1.2 / `PostgresSaver.setup()`|
|平台扩展|`public.vector`|数据库管理方预先安装；应用 migration 不创建、不删除扩展|

core 共 15 张表，vector 可选增加 2 张。主库目前共 21 张表（17 张应用表 + 4 张 LangGraph 表），没有 `rf_schema_version`。此前报告的“19 表指纹”排除了持续变化的 `rf_worker_heartbeats` 和库自身的 `checkpoint_migrations`，并非主库全部表数。core 的 3 个 BIGSERIAL 序列也纳入结构核对。

## 版本设计

- `rf_core_0001` 是 core 独立版本线起点。
- `rf_vector_0001` 是 vector 独立版本线起点，通过 `depends_on` 依赖 core 基线。
- 只需 BM25 的库升级 `core@head`，不需要安装 pgvector。混合检索库显式升级 `vector@head`；Alembic 会先执行 core 依赖。
- 两个分支存在时，不使用有歧义的 `head`。未来 core 修订接在 core 分支，vector 修订接在 vector 分支；不能把可选向量表串入所有库都必须经过的 core 链。
- 依赖被满足后，Alembic 可以只保留 `rf_vector_0001` 一条版本记录；这不表示 core 未执行。应结合版本依赖图理解状态。
- 基线从提交 `ba17859` 的实际 DDL 冻结而来，历史 revision 不导入会变化的运行时代码。保留原字段顺序、默认值、约束、索引和序列。
- 严格建表；已有表会报错并事务回滚，不能用 `IF NOT EXISTS` 掩盖结构漂移。业务订单、政策内容、发布和向量均不由迁移播种；唯一初始化内容是 `rf_policy_head` 的空指针（generation=0）及迁移版本记录。
- 暂不提供基线删除式 downgrade；命令会明确拒绝。不能借 downgrade 删除工单或 checkpoint。3.3 会定义恢复路径。
- 不启用 autogenerate：`target_metadata=None`。新增修订必须显式编写、审阅所有权；绝不把共享库的 LangGraph 表导入应用迁移。

## 隔离环境使用

使用 Python 3.12，安装 `requirements.lock`。连接从环境变量 `DATABASE_URL` 读取，不写进 ini 或提交记录。`RF_MIGRATION_SCHEMA` 默认为 `public`；测试可指定已创建的随机 schema。名称只接受小写字母、数字、下划线，拒绝系统 schema。连接内部显式设置 search_path，不把别的 schema 的同名表当成本地表。

下列命令假定连接已指向**可丢弃的隔离数据库**，该 schema 尚无应用表：

```text
python -m alembic history --verbose
python -m alembic upgrade core@head --sql
python -m alembic upgrade core@head
python -m alembic current
```

需要向量表时，先由数据库管理方安装 pgvector 到 public（当前验证版本 0.8.6），再执行：

```text
python -m alembic upgrade vector@head --sql
python -m alembic upgrade vector@head
```

离线 SQL 不连接数据库，也不自动安装扩展；执行向量 SQL 前同样需要扩展。在线执行会检查扩展所在 schema。运行版本不兼容时 PostgreSQL 会报错并回滚；本步未声明对其他 PG/pgvector 版本的兼容性。

Dockerfile 已包含迁移文件和只读工具；3.1 未把新镜像部署到 8003，也未把迁移加入启动命令。现有应用 setup 仍负责演示数据和 checkpoint 初始化，单独应用基线不等于应用新装交付。

## 只读结构核对

```text
python scripts/schema_catalog.py --profile hybrid --baseline migrations/baselines/hybrid.json --report schema-report.json
```

BM25-only 库使用 `--profile core --baseline migrations/baselines/core.json`。工具在只读、可重复读事务中读取 catalog，核对列顺序/类型/非空/默认/identity/generated、约束、索引、用户触发器、RLS 开关和序列定义/归属；报告迁移版本、扩展版本、LangGraph 表名和未知表名。core 模式发现向量表也会报告，不会默默忽略。

退出 0 表示所核对应用结构相符，退出 1 表示有漂移。报告只含结构，不含工单、文档、密钥或连接地址。它不会 stamp，也不会写任何数据库对象；**结构相符不等于旧库接管完成**。用户/授权、RLS 策略内容、数据语义、非表对象和扩展兼容性不属于该结构比较器的完整审计范围。

`migrations/baselines/*.json` 是从隔离库实际执行冻结 revision 得到的参考结构；未来修订新增对应基线和测试，不能为了消除差异而覆盖历史基线。库升级不能修改应用或 LangGraph 的历史迁移文件。

## 验证与后续

本步证据：[3.1 报告](validation/step-3.1-2026-09-22/REPORT.md)。测试包括离线渲染、分支依赖、无扩展 core、向量前置条件、真实旧 setup 对照、已有表拒绝、结构漂移和真实 checkpoint 保留。CI 已纳入新增测试；依约 3.9 统一推送前不宣称新提交远程 CI 成功。

下一步 **3.2**：基于这里的框架完成空库新装、现有库保留数据接管、应用启动与迁移的分工，并独立验收旧工单和待审批恢复。此步骤尚未执行。
