# 3.6 数据库角色隔离验收

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

2026-09-22完成。迁移、业务、只读三个PostgreSQL账号已在本机真实部署；普通API/Worker不再持有数据库管理员连接。

## 实现

- `db_roles.py`显式配置`rf_migrator/rf_app/rf_readonly`，分别承担对象所有权/维护、业务允许清单写入和只读查询。三个账号无超级用户/创建库/创建角色等提升权限，无成员关系、DB CREATE/TEMP。
- Compose启动顺序为DB → db-roles → migrate → API → Worker。业务与只读连接分离，MCP严格模式缺少只读连接即失败；本地启动器也要求受限账号。
- app不能改订单/政策/向量/版本、删除审计/退款、重置序列或DDL。退款使用固定无参数SECURITY DEFINER政策锁函数维持原并发边界，不增加政策写权限。重复配置清除列级授权漂移；未来迁移对象默认SELECT，新写权限须明确审核。
- 新版恢复在全新隔离库恢复归档后重建账号与ACL，生成独立随机密码；旧镜像保持原恢复契约。维护脚本通过migrate账号执行，不把管理连接放进API/Worker。
- 无新业务表、无Alembic revision，hybrid仍为`rf_vector_0001`。操作、权限矩阵及边界见[DATABASE_ROLES.md](../../DATABASE_ROLES.md)。

## 验证证据

|范围|结果|证据|
|---|---|---|
|基础回归|232通过，0失败/跳过；含9项角色/凭据专项|[unit-step36.xml](unit-step36.xml)|
|真实PostgreSQL回归|120通过，0失败/跳过；含8项权限专项|[postgres-step36.xml](postgres-step36.xml)|
|最终镜像全新Compose安装|44项越权拒绝；自动退款/拒绝、人工批准退款、重复退款拦截|[fresh-final.json](fresh-final.json)|
|旧镜像待审批接管|原主库备份恢复后，用旧镜像产生待审批工单；更新角色后20表不变、原run继续退款且调查不重做；44项越权拒绝|[legacy-adoption.json](legacy-adoption.json)|
|受限权限备份再恢复|两次22表核对通过、角色重建；原待审批同意/拒绝、重复审批409、运营审批403、重复退款拦截|[restricted-restore.json](restricted-restore.json)|
|本机实际部署|四服务healthy，API/Worker身份rf_app、MCP连接rf_readonly、迁移rf_migrator；无管理凭据进入应用；数据与结构保留|[main-deployment.json](main-deployment.json)|
|最终核对|主库20表指纹一致、6套QA停止保留卷、两份备份离线校验、测试计数、源码和配置检查|[final-evidence.json](final-evidence.json)|

PG专项涵盖所有权接管和幂等配置、禁止业务/只读账号迁移或配置角色、真实事务锁等待、关闭只读设置仍禁止写入、MCP实际使用只读连接、运行身份拒绝、未受管同名角色回滚、列级越权授权清除。测试使用隔离schema和独立测试库，不对主库执行权限破坏探针。

`fresh-roles.json`是早期候选的新装结果；最终结果以`fresh-final.json`为准。`unit-roles.xml`、`postgres-roles.xml`及`postgres-roles-final.xml`保留迭代中的专项记录，不重复计入232/120总数。一条既有Starlette/AnyIO弃用警告不影响通过结果。

## 主库与保留资料

本机访问地址仍为 http://127.0.0.1:8003/ ，MODE=demo、RETRIEVAL_MODE=hybrid、SEMANTIC_WEIGHT=0.25。API/Worker为`resolveflow:step36`，`resolveflow:local`同镜像，ID为`sha256:ca318ed482147fb2307c6d1b53e95e2a7c93301eef7a35a9b1ee3bb4cc220cec`。DB与embedding的容器、镜像、启动时间完全不变；API/Worker重建，db-roles/migrate退出0。政策检索实际使用hybrid，Worker在线。

原20张非心跳/非应用版本表内容逐表SHA256一致，22表结构与版本不变，保留9张工单、43个checkpoint、7条向量。权限及所有权是预期变更，结构比较不包含ACL/函数；另行检查角色标记、提升权限、成员关系、表所有权、可见授权和锁函数定义属性。主库无新增业务工单，只有正常Worker心跳写入，付费模型调用0。

- 权限接管前备份：`work/step36-main/backups/20260922T122001Z-1cf306517ab0`，79,374字节，SHA256 `b6d5e2cd70a3d46ad8baffb3c0d7f2d96908717b67754e651d3bd04a47eb22bc`；已在旧库接管演练中真正恢复。
- 停API/Worker后的附加备份：`work/step36-main/pre-deploy.dump`，79,335字节，SHA256 `1cd0d8cbf7e706d60697263c56f1de599c43fde914262664ad571913ffd24f12`。
- 受限账号下的新主库备份：`work/step36-main/restricted-backups/20260922T123412Z-b1e88a0d3e0d`，80,455字节，SHA256 `a645e399e33ad1a6af878ccfe36ee02dbccd6ce5c8a4de37b49e88f9574178a9`；完整解码/清单验证通过。该份主库归档未单独再恢复；新版恢复/审批能力由上述独立QA归档验证。
- 原`.env`私有副本与`resolveflow:pre-step36`镜像保留。本地`.env`仅追加三个随机数据库角色密码，未公开或提交。所有私有恢复配置/归档位于Git忽略目录。
- 6套本步QA项目、临时启动的step33测试DB均已停止，保留容器和命名卷；没有删除历史QA卷。

## 可复验入口与限制

在有对应镜像的Docker主机，选择从未用过的QA项目名和报告名：

```powershell
python scripts/roles_qa.py --image resolveflow:step36 --project resolveflow-qa-roles-recheck --report work/roles-recheck.json
python scripts/roles_upgrade_qa.py work/step36-main/backups/20260922T122001Z-1cf306517ab0 --image resolveflow:step36 --project resolveflow-restore-roles-recheck --report work/roles-upgrade-recheck.json
python scripts/restore_qa.py <可信新版demo备份目录> --prefix resolveflow-restore-roles-flow --report work/roles-flow.json
```

宿主Python标准库即可运行这些编排入口；应用/PG测试在Python3.12容器中执行。新装测试需要8019端口可用。不要复用既有项目/报告，不运行`down -v`。基于同名角色的权限隔离不提供个人身份/RLS；app仍可写其业务授权范围，迁移管理员仍能改变历史数据。便携PG未升级；本步不验证云端部署、任意阻塞恢复或连接耗尽，不更改RAG标签/模型/退款规则。3.7继续处理连接池、超时与慢查询。
