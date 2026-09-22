# 3.1：迁移框架与现有数据库结构基线

日期：2026-09-22。起点：`ba1785975a24cced4502b48bece24cbaa3560f3a`。结论：**本步完成**；3.2 未开始，本步仅本地提交，第三阶段在 3.9 统一推送。

## 完成内容

- 新增 Alembic 1.20.0 框架、`rf_schema_version` 元数据及冻结 core/vector 两条版本线。
- core 管理 15 张业务/队列/知识/政策表；vector 可选增加 2 张表并依赖 core 基线。基线保存现有列顺序、类型、默认值、约束、索引和 3 个序列。
- LangGraph 的 4 张 checkpoint 表及内部迁移版本继续由 PostgresSaver 管理。应用迁移不创建、修改或删除这些表，也不管理 pgvector 扩展的安装/卸载。
- 新增只读 catalog 核对工具及 core/hybrid 参考 JSON；真实旧 setup、隔离基线及主库应用结构一致。
- Dockerfile 包含迁移文件及核对工具；依赖锁只新增 Alembic、Mako 1.4.1、MarkupSafe 3.0.3，原依赖版本不变，`pip check` 通过。
- 更新 README、路线图、执行清单、交接、验证索引及 [MIGRATIONS.md](../../MIGRATIONS.md)。CI 已加入新增的 6 项离线和 9 项 PostgreSQL 测试，未推送或触发新远程 CI。

## 实测结果

|检查|结果|证据|
|---|---|---|
|基础与 MCP 回归（断网、无主库连接）|187/187 通过，其中 6 项迁移离线检查|[unit-step31.xml](unit-step31.xml)|
|PostgreSQL 回归（专用 QA 库、随机 schema）|56/56 通过，其中 9 项迁移检查|[postgres-step31.xml](postgres-step31.xml)|
|没有 vector 扩展时创建 core|15 张应用表成功，版本 rf_core_0001|[baseline-generation.json](baseline-generation.json)|
|缺少 vector 扩展时升级 vector|明确拒绝，原 core 结构和版本原样保留|同上|
|显式安装扩展后升级 vector|17 张应用表成功；依赖满足后版本表记录 rf_vector_0001|同上|
|实际旧 Store/setup 与新基线对照|hybrid 结构一致；旧 setup 有演示数据但不被版本标记|PG 测试|
|对已有业务表误执行初始基线|报错、事务回滚；原表/订单保留，无新版本表|PG 测试|
|漂移检测|漏索引、错误默认值、额外列、未知表均检出|PG 测试|
|LangGraph 归属与数据保留|真实 PostgresSaver 创建并存储 checkpoint；应用迁移前后4表结构、行和迁移版本不变，checkpoint仍可读|PG 测试|
|主库只读结构核对|17 张应用表与 hybrid 基线相符；4 张 LangGraph 表；无应用迁移版本|[main-schema.json](main-schema.json)|
|主环境保留检查|19 表数据指纹、4 服务镜像/启动时间/健康和 demo/hybrid 配置全部不变|[before](runtime-before.json)、[after](runtime-after.json)|
|正式 Dockerfile 构建与打包检查|独立镜像成功，依赖检查/离线向量 SQL/核对工具入口通过；未部署|[package.json](package.json)|

PostgreSQL 测试有 1 条来自既有 Starlette/AnyIO 别名弃用的 warning，无失败。新增基础检查包含离线 SQL 所有权、无业务播种、core/vector 分支和不合法 schema 拒绝。

主库实际是 **21 张表 = 17 张应用表 + 4 张 LangGraph 表**。19 表数据指纹是历史固定口径，不包括持续变化的 `rf_worker_heartbeats` 与 `checkpoint_migrations`；不把此口径误写成主库只有19张表。

## 隔离与复现

测试使用项目 `resolveflow-qa-step31` 的 PostgreSQL 17.11 / pgvector 0.8.6，未启动其 API、Worker 或 encoder。15项新增测试使用 Python 3.12，数据库测试创建和清理独立随机 schema；首次基线另保留 `baseline_step31` schema 供复查。QA 数据库已停止，`resolveflow-qa-step31_postgres-data` 卷保留。其他历史 QA 卷未删除。

安装锁定依赖后，在隔离库连接环境运行（不得指向主库）：

```text
python -m pytest test_migrations.py -q
```

设置 `RUN_PG_TESTS=1` 与隔离库 `DATABASE_URL` 后：

```text
python -m pytest test_migrations_pg.py -q
```

测试源代码副本位于本机忽略目录 `work/step31-src`，未复制 `.env`。基础回归在 Docker `--network none` 下执行，PG 回归仅连接 QA 内部网络。测试没有项目模型付费调用、真实支付或主库写入。只读主库报告仅含结构及版本信息。

## 本步边界

主环境 8003 未重建/重启，继续既有 startup DDL。结构基线可在空 schema 执行，并不代表新装流程已切换；主库未 stamp、未执行 upgrade。旧库接管、版本检查与迁移运行顺序仍是 **3.2**；失败继续/恢复策略是 **3.3**。基线 downgrade 明确拒绝删除操作。

本步未重复浏览器/模型质量/故障重建演练；这些在2.12已有证据，此次未修改业务执行或前端。新增迁移测试远程状态待3.9推送后验证，不能引用 `ba17859` 的历史成功代替本次 CI。
