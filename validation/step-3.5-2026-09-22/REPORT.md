# 步骤3.5：独立库恢复、数据核对与待审批工单恢复

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

日期：2026-09-22。

## 交付与实际结果

新增恢复入口`database_restore.py`、只读验证器`restore_validation.py`和真实API/Worker演练`restore_qa.py`。运行方式、私有配置位置和失败处理见 [RESTORE.md](../../RESTORE.md)。

1. 使用3.4真实主库备份`work/backups/20260922T113706Z-dff0eaa57542`，恢复到新PostgreSQL容器和新命名卷。22张表的内容、结构、约束/索引、扩展、应用版本rf_vector_0001及10条LangGraph迁移记录全部匹配；7条向量保留，9张既有工单的checkpoint可读。[首次成功独立恢复](base-restore.json)。
2. 最终演练用修订后的工具再次恢复出隔离副本`resolveflow-restore-step35-final-seed`；仅在此副本新建RF-3501/RF-3502及两张待审批工单。用3.4工具备份，然后停止副本全部服务。
3. 将带待审批状态的备份恢复到`resolveflow-restore-step35-final-resume`，使用另一套数据库/网络/卷。启动业务前22张表全部与备份相等，包括心跳、审批、退款台账、RAG和checkpoint；11张既有工单checkpoint可读。
4. 启动匹配镜像的真实API/Worker，原run_id继续执行：[审批恢复证据](approval-resume.json)。

|订单|原恢复工单|审批|最终状态|
|---|---|---|---|
|RF-3502|7bba9649-01b2-489d-886c-06bec5972897|拒绝|rejected，无退款|
|RF-3501|d591f98f-1f4f-41f8-a599-6be21bef83b8|同意|refunded，恰好一笔模拟退款|

两张工单调查轨迹、模型建议和政策证据均与恢复前一致，审批节点各出现一次。运营审批403；审批员成功；重复/相反审批409。再次提交RF-3501并批准，结果already_refunded，退款仍归属于原恢复工单，总退款由备份中的1笔增至2笔，没有第二笔重复退款。checkpoint最终61条。

三条BIGSERIAL序列恢复后安全：audit与job_attempts下一值均为15，高于各自最大ID14；policy_events下一值2，高于最大ID1。实际恢复业务继续写入后，审计/尝试最大ID均为18；没有手工重置序列。在线备份的序列观察值不作为强制相等标准。

## 隔离与保护

- 工具只接受新`resolveflow-restore-*`目标，不复用现有容器/网络/卷；创建卷后核对本次所有权随机标记，恢复前再次确认库为空。只接受备份记录的不可变本地镜像，固定目标库名。
- 恢复使用`--single-transaction --exit-on-error --no-owner --no-privileges`，不执行迁移、播种、清库或生产连接切换。单次恢复工具完成后停止数据库；业务演练显式启动其隔离副本。
- 所有QA网络internal，无宿主端口；独立随机凭据保存在忽略目录，demo模式、空模型密钥，零付费调用。演练使用BM25；向量数据恢复完整，但未启动embedding服务。
- [最终证据](final-evidence.json)：主库原20张非心跳表与3.4指纹相同，结构/版本相同；API、Worker、DB、embedding的ID、镜像、启动时间和healthy状态均保持不变。主库无新测试订单/工单。
- 真实CLI复验：已有目标拒绝恢复，损坏归档拒绝创建目标；均未生成成功报告。原3.4备份仍通过校验，原manifest未改写。
- 六套本步创建过容器的QA项目均已停止，全部卷保留；旧测试项目resolveflow-qa-step33-db-1也已重新停止。早期失败和第一次成功演练产物均保存在work/，不混入最终通过证据。

## 测试与排障记录

- [unit-step35.xml](unit-step35.xml)：223项基础/离线检查通过，含17项恢复专项。覆盖目标命名、已有容器/网络/卷拒绝、子网重叠、不可变镜像、事务恢复参数、失败停止/保留、报告不覆盖、损坏备份不创建资源、卷所有权冲突与非空库拒绝。
- [postgres-step35.xml](postgres-step35.xml)：112项PG检查通过，含8项恢复专项。覆盖空表/未调用序列、落后序列拒绝且不改号、合理序列空洞保留、数据和版本不一致拒绝、真实UUID待审批checkpoint可读且不写库、业务状态缺checkpoint拒绝。
- 最终脚本再次完成真实双恢复/审批演练。
- 初次尝试遇到Docker默认地址池耗尽，未创建数据库；新增与已用网络无重叠的小网段选择，保留全部旧网络。后一次恢复成功但读取checkpoint时把UUID直接传给text型thread_id，验证器报错；修正为字符串，并加入真实UUID回归。失败副本已停止保留，不当作验收成功。
- 一个已有Starlette/AnyIO弃用警告，无失败；本步没有UI或核心业务逻辑变更，未重复浏览器演示和付费模型评测。

## 边界

本步证明：同一Docker主机上的逻辑备份可恢复到独立数据库及独立卷，且原待审批流程可在新Worker继续执行。没有生产切换、异地灾备、PITR、备份后增量对账、真实支付或恢复SLA承诺；数据库权限仍待3.6隔离。原备份的`restore_verified=false`是创建时历史事实；本步独立报告记录恢复通过，完整业务演练另记录`resume_verified=true`。
