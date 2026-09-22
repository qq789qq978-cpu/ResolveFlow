# 3.2：空库新装与旧库保留数据升级

2026-09-22。起点：3.1提交`dc7648c`。**本步完成**，只本地提交，3.9统一推送；3.3尚未开始。

## 变更及运行结果

新增`db_migrate.py prepare/adopt/check`与就绪契约。应用表由Alembic管理，LangGraph自身setup只在迁移入口调用；API、Worker、知识库导入、向量构建不再执行PostgreSQL建表/补列。Compose增加一次性migrate任务，按DB健康→迁移成功→API健康→Worker顺序启动。非Docker supervisor也在启动子进程前检查版本。

prepare在新库建表，只有全新demo库播种演示订单/政策；live新库不播种。旧无版本库必须显式adopt，按冻结基线核对并锁表后同事务stamp。漂移/部分表/未知版本拒绝，core→hybrid可显式增加向量表；安装vector扩展仍是维护动作。所有版本化入口使用schema范围互斥锁。重复prepare/adopt不重新播种旧数据。

主环境8003已在维护窗口完成备份、adopt、API/Worker更新。原20张非心跳表的数据指纹完整一致，7条向量保留，只新增应用版本表。主库目前**22张表 = 17张应用表 + 4张LangGraph表 + rf_schema_version**，应用版本rf_vector_0001。4常驻服务healthy，migrate为正常Exited (0)，demo/hybrid/0.25不变，DB/encoder镜像和启动时间不变。没有新增主库测试工单，也没有项目模型付费调用。

## 验收证据

|检查|结果|证据|
|---|---|---|
|断网基础/MCP回归|187/187通过|[unit-step32.xml](unit-step32.xml)|
|独立PG回归|68/68通过，包含新增12项新装/接管检查|[postgres-step32.xml](postgres-step32.xml)|
|Compose全新安装|迁移任务先完成，API/Worker健康，自动模拟退款通过|[compose-acceptance.json](compose-acceptance.json)|
|真实旧容器→新版接管|旧版产生自动退款、自动拒绝、人工拒绝及待审批；默认prepare拒绝，显式adopt保留18张原非心跳表|同上|
|重建与旧审批恢复|保留卷重建后数据/旧工单响应一致；原待审批批准后退款成功；旧退款再请求被幂等拦截|同上|
|主库真实备份副本验证|恢复至独立rf_mainclone32，adopt和重复prepare前后20表相同，7向量保留|[main-clone.json](main-clone.json)|
|主库实际接管/更新|停机备份、20表保留、原镜像留存、4服务healthy、只读API及hybrid查询通过|[main-deployment.json](main-deployment.json)|
|主库结构/运行状态|22表，rf_vector_0001，migrate退出0，最终镜像CLI与pip check通过|[main-schema-after.json](main-schema-after.json)、[runtime-final.json](runtime-final.json)|

PG用例具体覆盖：demo/live初始化差异、重复运行保留所有业务行、core/hybrid旧库显式接管、真实checkpoint保留、漏索引/额外列/缺表拒绝stamp、core扩展hybrid保留数据、API/Worker拒绝未准备库且不建表、未知应用/checkpoint版本拒绝、并发迁移拒绝、只读连接就绪检查。原3.1的6项离线/9项PG迁移检查继续通过。1条既有Starlette/AnyIO弃用warning，无失败。

本步没有重复浏览器、RAG质量或全部1.5–1.9故障注入。新增迁移PG用例已接入CI，但尚未推送，不能用第二阶段的成功CI代替本次远程状态。

## 隔离、备份与复现

PG测试使用独立resolveflow-qa-step32项目/随机schema。真实Compose验收使用resolveflow-qa-step32fresh（8017）与resolveflow-qa-step32legacy（8018），公开QA凭据、demo模式，不读取主环境.env。旧容器镜像为resolveflow:step210；其DDL和Compose另冻结为tests/fixtures中的升级测试输入，生产代码不再导入这些旧DDL。

```text
python scripts/migration_qa.py --image resolveflow:step32 --legacy-image resolveflow:step210 --prefix resolveflow-qa-step32-repeat --report validation/migration-repeat.json
```

须先构建新镜像并保留旧镜像，选择未使用的项目名前缀/报告路径，8017/8018空闲。脚本会拒绝复用已有项目，结束停止服务并保留卷。单独PG回归在隔离DATABASE_URL与RUN_PG_TESTS=1下执行`python -m pytest test_database_upgrade_pg.py test_migrations_pg.py -q`。

3个QA项目均已停止，所有命名卷保留，历史QA卷未删除。私有主库备份仅在忽略的work/step32-main目录中，未提交数据库内容或密钥；报告仅记录备份大小、SHA256、表计数/指纹及结构。旧主镜像保留为resolveflow:pre-step32。最终CLI增加.env加载，Compose显式环境优先；最终镜像已在主库实际执行adopt/check并通过启动和检索验收。

## 边界与下一步

3.1之前的主库总21表；历史19表指纹排除了心跳及checkpoint_migrations。本次20表比较包含checkpoint_migrations，排除动态心跳及新增的rf_schema_version，不混淆这些计数。

支持的是当前冻结基线旧库，任意更早或已漂移结构不会自动修复。便携PG和其他历史QA库没有自动接管。应用迁移、LangGraph autocommit setup和demo播种分属不同事务，跨阶段失败恢复尚未验收。基线删除式downgrade仍明确拒绝。

下一步 **3.3：迁移失败、回滚/继续及不可逆变更恢复方案**。此次升级保障用的备份/隔离副本，不等于提前完成3.4定期备份工具或3.5完整恢复演练。
