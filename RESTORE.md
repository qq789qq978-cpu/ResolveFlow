# 独立库恢复与待审批恢复手册（3.5）

3.4的备份必须经过真正恢复才能证明可用。3.5新增`database_restore.py`，只允许把可信备份恢复到**全新的隔离Docker数据库**；`restore_qa.py`进一步通过真实API/Worker验证待审批工单继续执行。本步不会切换主环境连接。

## 单次恢复与数据核对

需要Python 3.9+、Git、运行中的Docker，以及备份清单记录的本地数据库/API镜像。工具只使用清单中的不可变镜像ID，缺失时直接失败，不自动换成latest，也不拉取未知版本。

在项目根目录执行，替换备份路径、全新项目名和全新报告名：

```powershell
python scripts/database_restore.py work/backups/<实际备份目录> --project resolveflow-restore-mycheck --report work/restore-mycheck.json
```

项目名必须以`resolveflow-restore-`开头；不能是备份来源项目，不能复用任何现有容器、卷、网络或该名称的工作目录。每次复验换新名称。工具按顺序执行：

1. 校验备份三份文件及SHA256，检查所需镜像和目标名称。
2. 创建内部网络、带本次所有权标记的新命名卷和独立PostgreSQL容器，不映射宿主端口。历史QA网络可能耗尽Docker默认地址池，工具在`10.240.0.0/12`中选一个不与现有Docker网络重叠的`/24`；不删除旧网络。该范围用于本机隔离演练，特殊网络环境应先调整选段策略。
3. 在`work/restores/<项目名>/runtime.env`生成独立随机数据库密码及角色码。不会复制主环境`.env`，不会把密钥写入报告；该目录禁止提交。
4. 确认新数据库没有用户对象，再以`pg_restore --single-transaction --exit-on-error --no-owner --no-privileges`恢复至固定目标库`resolveflow`。不执行`--clean`、不覆盖旧库、不运行迁移或demo播种。
5. 若匹配镜像包含3.6角色模块，先用新随机密码配置迁移/业务/只读账号与对象所有权；私有admin.env用于配置、maintenance.env用于维护，runtime.env仅保留业务和只读连接。旧镜像保持历史账号契约；报告database_role_isolation注明是否启用。不执行迁移或播种。然后用备份对应API镜像只读核对：所有表行数/内容SHA256、完整结构/索引/约束定义、索引有效性、扩展版本、应用和LangGraph迁移版本、依赖与关键运行源码摘要。
6. 检查每个已记录业务状态工单的checkpoint能被PostgresSaver反序列化；按原UUID的字符串形式查同一thread_id。
7. 检查三条业务序列：下一值在合法范围内且高于对应表最大ID。兼容空表的`is_called=false`与正常序列空洞；不调用nextval、不自动setval，也不强制等于备份时观察值。
8. 写独立恢复报告，停止目标数据库，保留容器、网络、卷和私有配置；成功退出0。失败退出1，保留失败阶段/类型；已创建的目标数据库也尝试停止，报告注明停止是否成功。

`passed=true`表示该次独立恢复、结构/内容/序列及checkpoint读取核对成功。报告仍为`resume_verified=false`，因为没有执行审批；完整业务演练使用下一节入口。原备份清单不会被改写，仍保持其创建时的`restore_verified=false`。

## 真实Worker恢复演练

只用于**demo数据**，要求归档包含内置RF-1004，所有既有工单均为demo且异步任务已done；否则在启动Worker前拒绝。为避免复制真实模型任务后自动执行，不使用主环境密钥，模型密钥为空，内部网络无法访问模型服务。

```powershell
python scripts/restore_qa.py work/backups/<实际备份目录> --prefix resolveflow-restore-myflow --report work/restore-myflow.json
```

该命令会使用`<prefix>-seed`和`<prefix>-resume`两套新数据库/卷：

- 第一次恢复既有备份，核对全部表后启动匹配版本的API/Worker。以RF-1004为模板，在该隔离副本新建RF-3501、RF-3502，两者都停在人工审批；主库不新增这些订单或工单。
- 对包含两张待审批工单的副本执行3.4备份工具，再停止副本全部服务。
- 将新备份恢复到第二套独立数据库，逐表对照同一份备份清单，包括心跳和checkpoint；检查成功后才启动API/Worker。演练运行使用demo/BM25，向量仍被完整恢复核对，本步不测试embedding服务。
- 核对原工单/API内容与备份前一致，用原run_id分别审批拒绝和同意。验证调查轨迹、建议和政策证据没有重做或改变，checkpoint继续增加。
- 运营审批403；审批员可提交；重复或相反决定409。再次提交已批准退款的订单，只得到already_refunded；拒绝订单无退款，批准订单恰好一笔模拟退款，属于原恢复工单。
- 检查恢复后实际业务写入产生的新审计/任务尝试ID高于恢复前最大值，证明默认序列没有撞号。
- 完成后停止两套演练服务并保留所有卷。报告记录两次恢复证据及`resume_verified=true`，不会复制私有配置、票据正文或密钥。

## 失败后的处理

不要删除原备份、原库或历史卷，也不要把失败报告改成成功。根据报告的`failed_phase/error_type/database_stopped`定位问题：校验失败先核对文件；缺镜像先找回对应镜像；版本/结构不符先核对备份契约；序列落后先调查源库和备份，维护方案审阅后再修复，不由工具静默回退或改号。再次验收使用新名称，避免旧数据残留掩盖问题。

主进程被强制结束可能无法执行finally；此时检查本次项目的容器状态，只停止明确属于本次恢复的容器。不要针对所有Docker资源清理，更不要`docker compose down -v`。备份SQL只能来自可信来源，SHA256用于意外损坏检测，不是来源签名。

## 本步证明与未覆盖范围

[3.5报告](validation/step-3.5-2026-09-22/REPORT.md)给出22表主库备份恢复、三条序列、原checkpoint可读、两条待审批分支和模拟退款幂等的实测证据。

本工具在同一台Docker主机创建独立数据库及独立卷，证明逻辑备份可以恢复，不等于整机/异地灾备。没有生产连接切换、恢复后增量对账、真实支付、live模型调用、PITR、异地备份或恢复时间/数据损失SLA。向量数据完整性已核对，语义检索服务可用性不在本步验收范围。3.6已补充受限账号下的备份、权限重建及审批恢复验收，见[3.6报告](validation/step-3.6-2026-09-22/REPORT.md)。下一步3.7处理连接池与超时。
