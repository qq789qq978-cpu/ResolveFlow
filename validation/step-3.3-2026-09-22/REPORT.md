# 3.3：迁移失败、回滚/继续及不可逆变更恢复

2026-09-22。起点：`e8ba010`。**本步完成**，本地提交，3.9统一推送；3.4未开始。

## 结果与修复

本步覆盖三个独立提交边界：应用Alembic事务、LangGraph库自身autocommit setup、演示数据播种。修复3.2的两个缺口：部分checkpoint表不再一概拒绝，而是对锁定库版本/SQL指纹及真实中间结构验证后续跑；新装demo在结构提交后播种失败，可在明确用途并核对空数据后执行bootstrap-demo。prepare不自动重播种旧库，check仍仅代表结构/版本就绪。

新增checkpoint_state.py与冻结langgraph-3.1.2.json参考（从隔离PG实跑每条库迁移生成11种状态）；应用迁移入口和runtime就绪检查均拒绝未知结构、不连续/未来版本及无效checkpoint索引。库自身DDL/版本写入继续完全交由PostgresSaver维护，没有手改checkpoint_migrations。

CLI失败输出phase和安全的异常类型/校验原因，便于区分应用事务回滚与后续阶段已经提交。bootstrap-demo与迁移共用advisory lock，并在锁表后验证空数据、同事务播种；已有数据/非demo拒绝。[恢复手册](../../MIGRATION_RECOVERY.md)说明重试、REINDEX、受审阅downgrade、独立备份恢复、损失窗口和连接切换边界。

## 验收

|项目|实际结果|证据|
|---|---|---|
|断网基础/MCP回归|187/187通过|[unit-step33.xml](unit-step33.xml)|
|完整PG回归|97/97通过；补充CLI覆盖后恢复专项30/30通过，去重98项PG用例|[postgres-step33.xml](postgres-step33.xml)、[recovery-final.xml](recovery-final.xml)、[汇总](test-summary.json)|
|应用事务SQL错误|新库建表、旧库stamp后注入真实SQL错误，DDL/版本/数据全部回滚，重试成功|恢复专项2项|
|库自身迁移中断|0–9个已记录步骤，以及各自下一条DDL已提交但未记版本，共20种窗口；就绪先拒绝，再由setup续跑成功|恢复专项20项|
|错误中间结构|拒绝继续，不把错误表标记成成功|恢复专项1项|
|并发索引取消|真实阻塞+statement_timeout取消CREATE INDEX CONCURRENTLY，实际留下indisvalid=false；入口拒绝，明确REINDEX后继续|恢复专项1项|
|演示数据恢复|发布阶段异常使订单/知识/发布全部回滚；显式空demo补齐成功，重复/已有checkpoint/live拒绝且数据不变|恢复专项2项|
|CLI覆盖|显式core选项优先于hybrid环境默认；重复恢复拒绝，phase=demo_recovery|恢复专项1项|
|可逆与基线downgrade|临时QA修订增加nullable列、应用拒绝未知结构，精确降级后数据相同；降至base明确拒绝|恢复专项1项|
|真实进程强制结束|应用事务未提交、checkpoint某DDL提交未记版本两个窗口；进程结束后锁释放，事务回滚或库续跑，demo显式恢复|恢复专项2项|
|不可逆变更恢复|合成库删used列再以FALSE重建不能恢复原值；备份还原到新库后20表指纹一致、真实checkpoint可读|[irreversible-restore.json](irreversible-restore.json)|

共30项恢复专项，全部为真实PostgreSQL执行；故障控制仅存在测试进程，不在生产命令提供故障开关。可逆修订只生成在pytest临时目录，未新增生产revision。原来基线删除式downgrade仍拒绝。PG回归有1条既有Starlette/AnyIO弃用warning，无失败。

## 本机运行

主环境已更新API/Worker为resolveflow:step33，本地resolveflow:local指向同镜像。prepare返回unchanged，原20张非心跳表（含checkpoint_migrations）指纹相同，7向量保留，结构仍22表，版本rf_vector_0001。DB/encoder镜像与启动时间不变；4常驻服务healthy、migrate成功退出，demo/hybrid/0.25不变。升级后只读查询工单/监控与真实本地混合检索通过，没有向主库新增测试工单或执行bootstrap-demo。

证据：[升级前](runtime-before.json)、[部署数据/镜像对照](main-deployment.json)、[最终结构](main-schema-after.json)、[最终运行检查](runtime-final.json)。部署前私有备份在忽略目录work/step33-main/pre-deploy.dump，摘要写入部署报告，旧镜像resolveflow:pre-step33保留。

## 复现与保留

PG专项在独立DATABASE_URL、RUN_PG_TESTS=1环境运行：

```text
python -m pytest test_migration_recovery_pg.py -q
```

不可逆演练脚本要求专用QA项目、公开fixture连接、全新数据库名前缀，不连接主库：

```text
python scripts/migration_restore_qa.py --image resolveflow:step33 --project resolveflow-qa-step33 --prefix rf_recovery33_repeat --report validation/recovery-repeat.json
```

本次resolveflow-qa-step33内的rf_recovery33_damaged和rf_recovery33_restored保留供复查，备份在忽略的work/rf_recovery33；QA容器停止，命名卷保留。所有主库和QA备份内容均不入Git，提交证据仅含结构、计数、指纹和版本等，不含凭据。模型调用/真实支付均为0。

新增专项已加入CI，当前未推送，远程状态到3.9再核验。本次未重复RAG质量、浏览器或全部Worker业务故障演练。3.2真实待审批升级恢复仍见其历史报告，本步恢复库只验证合成数据与checkpoint可读，不声称完成3.5待审批灾备验收。

下一步 **3.4：备份脚本、版本时间和校验信息**。本步是故障恢复机制和操作方案，不包含PITR、生产连接切换、备份调度/保留策略或任意结构漂移自动修复。
