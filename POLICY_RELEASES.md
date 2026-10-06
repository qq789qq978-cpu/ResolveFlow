# 政策发布、回滚与退款规则一致性（2.5）

发布把一整套政策原文、片段、初始审核信息、动作依据和退款程序版本保存为一个完整快照。切换当前版本和记录操作历史在同一个 PostgreSQL 事务内完成。回滚切换政策，不删除工单，也不撤销已发生的模拟退款。

## 版本与规则绑定

[knowledge/release.json](knowledge/release.json) 声明发布ID、退款规则版本和代码SHA，以及退款/物流动作对应的完整chunk_id。运行时同时核对：

1. 发布清单声明的规则版本、SHA与当前镜像的 `refund_policy.py` 完全匹配。
2. 它们与 `policy_releases.py` 中显式的 `RULE_BINDING` 一致；仅在JSON里填写一个新的程序SHA不能授权未知程序。
3. 绑定段落的全文SHA与代码里的 `ACTION_CLAUSES` 一致，不能将退款规则的“7天”改成“14天”后继续沿用旧代码。
4. 当前数据库索引内容与被激活的快照一致，引用来自同一发布ID、快照SHA及变更序号。

文档版本与程序版本是明确映射，**不是要求字符串相等**。验收中的退款文档v3只修改版本元数据，资格段落仍完全相同，显式绑定原refund-v2程序后可以发布；资格原文变更则被拒绝。真实规则变更需要同时修改规则代码、版本号、明确的代码/段落绑定及发布清单，再协调升级API和Worker。本功能不会从政策文件或JSON执行新代码，也不会自动证明自然语言政策与程序语义等价。

同一个发布ID不允许被替换成不同快照；已发布文档ID或同一来源/版本不能对应不同原文SHA；同一退款规则版本号不能重用到不同代码SHA。相同发布ID、相同快照允许显式再次激活，但仍增加序号和审计记录，不能把它当作无影响的刷新。

## 存储与事务

|表|用途|
|---|---|
|rf_policy_releases|应用只追加的完整快照，保留发布主体、原因和时间|
|rf_policy_head|当前发布ID及单调递增的generation|
|rf_policy_reviews|按完整文档SHA保存最新审核状态，覆盖非当前版本|
|rf_policy_events|发布、回滚、审核变更、原始导入导致失效的操作记录|

原 `rf_knowledge_documents` / `rf_knowledge_chunks` 仍是当前索引。发布时先核对预期generation、规则绑定和当前审核有效期，再保存快照并替换索引、切换指针、追加事件。任何失败均回滚这个事务。并发发布者使用同一预期序号时仅一个成功，另一个必须重新查看状态。

发布、回滚、审核修改与退款事务采用一致的政策表锁边界。检索使用一致快照；执行退款时在锁内重新核对当前发布与审核。正常重启不重置当前版本。数据库的拥有者仍有直接修改数据的能力，本步的不可变性是应用命令契约，不是防数据库管理员篡改的签名系统；3.6已限制普通业务账号修改政策，维护账号仍保留必要权限，见[DATABASE_ROLES.md](DATABASE_ROLES.md)。

## 审批、回滚和撤销

工单证据保存检索当时的发布ID、快照SHA、generation和规则身份。发布、回滚及显式审核变更均推进generation，因此A→B→A以后，A时代的旧审批也不会自动生效。旧工单保留原证据并转人工核查，需要基于当前政策发起新的调查。升级前未携带发布身份的待执行工单也按此保守处理；旧已完成记录和台账不改写。

回滚使用数据库历史快照，不依赖旧源码目录仍在磁盘。**当前审核状态优先于快照里的旧审核信息**：已经撤销、过期、尚未生效或未经审核的历史文档不能通过回滚重新获得权限。维护人员可显式更改审核信息；该动作有独立审计记录并增加generation。

`rag.py` 的原始导入如今会撤销当前发布身份，索引在重新发布前不能用于检索/执行；同哈希文档保留当前审核状态，不能靠重新导入文件把撤销改成通过。重新审核必须走明确的review操作。缺少有效发布或代码不兼容时不回退到本地演示授权。

2.4关于真实UTC时间、有效期端点、demo/live、引用真实性和长事务边界继续适用。当前全部内置审核仍为demo_fixture，不是人工业务签字；live模式不能直接采用。授权核对是检查时间点的约束，不能扩展为任意SQL阻塞后提交时刻的实时截止保证。

## 维护命令

这些命令由能够访问数据库的部署维护人员执行；运营/审批角色没有政策发布权限。管理员可只读查看 `GET /api/policy-releases`，网页当前仅显示版本，不提供发布/回滚按钮。CLI的actor是维护声明，不等于个人账号认证。

```powershell
# 先查看head.generation、当前发布与事件；不要猜测序号。
docker compose exec -T resolveflow python policy_releases.py status

# 以下示例假定刚核对到generation=1。目录须包含完整政策集合、
# governance.json和release.json，并使用新的发布ID/文档版本。
docker compose run --rm --no-deps -v "${PWD}/work/policy-candidate:/candidate:ro" migrate python policy_releases.py publish --directory /candidate --expected-generation 1 --actor maintainer --reason "发布已核对的政策集合"

# 示例：上一步成功后generation=2，回滚到仓库初始发布。
docker compose run --rm --no-deps migrate python policy_releases.py rollback --release demo-policy-2026-09-21 --expected-generation 2 --actor maintainer --reason "回退当前政策"

# 审核文件是某一文档的完整治理对象，包含document_sha256；
# 先重新查看generation，再以其当前值执行。示例值为3。
docker compose run --rm --no-deps -v "${PWD}/work/review.json:/review.json:ro" migrate python policy_releases.py review --metadata /review.json --expected-generation 3 --actor maintainer --reason "记录政策审核变更"
```

以上写入命令通过rf_migrator执行，业务账号会被数据库拒绝。宿主work/policy-candidate及work/review.json须先准备好；临时容器只读挂载它们。

候选目录应与正式knowledge分开，不要把另一套同ID文档嵌入knowledge子目录（导入器会递归读取）。首次安装空库自动登记内置演示发布；既有库升级只初始化新表，保留原索引，需要维护人员核对当前原文和审核后显式publish。API/Worker须协调升级：旧2.4程序没有发布门禁，不能依赖它执行2.5的约束。当前部署流程先停止接单、确认队列空闲、停止Worker，登记发布后启动同版本服务。

本机8003已登记 `demo-policy-2026-09-21`，generation=1；日期对应所绑定的原2.4语料，登记日期为2026-09-22。原13张业务/checkpoint/知识库表完全不变，新增上述4张表，见 [2.5报告](validation/step-2.5-2026-09-22/REPORT.md)。

## 隔离复验

```powershell
docker build -t resolveflow:step25 .
docker compose -f compose.qa-release.yaml up -d --wait
python scripts/release_qa.py --report validation/release-recheck.json
docker compose -f compose.qa-release.yaml stop
```

脚本使用专用8014项目、公开测试凭据和合成文档；先检查是尚未演练的新发布历史，拒绝覆盖已有历史。首次验收后保留卷。再次演练应另行配置新的隔离项目/端口，不删除旧卷。主库只读取指纹，脚本不读取主环境授权码。

冻结80条标签、45/35划分、原政策和退款规则保持不变；[评测补充协议](evals/rag/RELEASE_PROTOCOL.md) 解释离线generation=0与真实数据库的区别。检索优化与模型评测由独立实验覆盖，结果见[验证成果](docs/RESULTS.md)。
