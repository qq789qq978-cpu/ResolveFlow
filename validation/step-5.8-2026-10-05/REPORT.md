# 5.8 本地交付与发布核验

日期：2026-10-05。沿用原目录，起点8772292，原工作区干净、8个阶段五提交尚未推送。用户指定完成5.8，授权本阶段统一正常推送与同SHA CI验收；4.4继续暂缓。

## 本地验收

|范围|结果与证据|
|---|---|
|完整基础回归|400项不同测试、另35契约子用例通过；[JUnit](regression/unit.xml)，包含10项新发布闸门测试，不重复累计|
|真实PostgreSQL|166项通过，无失败或跳过；[JUnit](regression/postgres.xml)，全新隔离DB，结束停止保留卷；[运行记录](regression/run.json)|
|前端|27项通过；[JUnit](frontend.xml)，与CI使用相同Node JUnit方式|
|交付保护|31项检查通过；[核验](verification.json)，[主库之前](main-before.json)/[之后](main-after.json)、[部署基线](before.json)/[之后](deployments-after.json)|
|运行版本|8057镜像ID及67个运行文件与交付源码一致；仅宿主发布脚本/测试/文档改变，不需替换业务镜像|
|现有数据与环境|主库20张业务表、8056/8057双库业务/身份/checkpoint/结构与序列保留；正常Worker心跳时间变化明确排除。原758个ResolveFlow容器身份/启动/运行状态/挂载与历史卷保留；8056/8057各12服务healthy|
|配置与备份|8057 alpha API/monitor期望Worker=2、beta=1；8056配置未升级。原联合备份和同D盘独立目录副本完整性复验通过；不称异地备份|
|历史材料|Word、截图、架构图原件哈希未变；新增架构说明指向5.x部署结构，不改历史图片日期|
|凭据与链接|[审核](file-audit.json)：扫描当前阶段修改与8个未推送提交的287个新blob、699个已知本地凭据值，未发现匹配；Python解析和本地链接通过。该扫描不证明不存在未知凭据|
|自动任务|旧七天heartbeat仍PAUSED，observer-stop保留；不重新启动长期观察|

首次文档生成因Windows默认GBK读取UTF-8失败，改为显式UTF-8后完成；首次汇总在PG尚未产出JUnit时提前读取失败，待PG完成后使用已保存保护快照重新汇总。两项为本步辅助执行问题，不是业务测试失败，也没有跳过测试。基础测试的单个依赖弃用警告未影响结果。所有业务/故障QA仅使用隔离环境；本步未重复5.6压力或5.7恢复演练，其原日期结果继续保留。

## 交付改动

新增[运维交接](../../docs/deployment/HANDOVER.md)，统一最新入口与版本说明、启动停止、备份恢复、故障处置和AC矩阵。扩展发布附件核验：增加账号、联合备份、合成订单、离线支付报告及前端JUnit；拒绝失败子检查、缺失/歧义附件和支付范围错误，并要求工作区干净。历史4.5回执见previous-ci-receipt.json，它不认证本次提交。

## 同SHA发布完成条件

统一推送阶段五已完成提交，正常push、不改写历史。完成条件：validation/github-actions-local.json的passed=true，sha等于本地HEAD与远程main，当前push CI所有job成功且evaluation附件已下载核验。回执及原始zip不提交，避免自身SHA循环；没有匹配回执则5.8远程交付未完成。容量与单日演练复用5.6/5.7真实日期证据，不声称在CI重复执行。

AC-07按用户授权暂缓，mock不冒充服务商沙箱；AC-04/10/11按单日修订通过，非七天/24小时或生产SLA。公网/HTTPS/异地备份/宿主外部监控、人工RAG标签及语义支持性/OCR仍未验证，reranker保持关闭。保留审批、checkpoint、退款幂等和后端权限、原SQL空格；无付费调用、真实退款或数据卷删除。


## 首轮CI失败与恢复启动修复

首轮提交95b0380的[CI 37295737144](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/37295737144)失败。基础/PG/前端及19项联合备份、原审批恢复、退款幂等检查通过，随后恢复副本整套 `compose start --wait` 返回1。原包装器没有保存stderr，**具体引擎错误未验证**，不猜测为数据库损坏或认定业务回归失败。见[失败摘要](ci-first-failure.json)、[原附件](ci-first-local-deployment.json)；原始日志仅保留私有work目录。

新增共享现有容器启动器：先核对本项目完整容器集合，缺失即拒绝隐式安装；使用明确容器ID，依次启动数据库、入口/API、Worker/monitor并等待健康，失败即停止后续启动。安装、角色配置和迁移容器不参与。试运行resume和本地恢复验收共用此入口，维护CLI增加resume；QA失败保存脱敏状态/退出码/健康摘要。未改变67个业务镜像运行文件或数据库schema，不升级原环境。

修复后[43项专项](restart-unit-final.xml)通过，包含4项新用例：不重跑安装、缺失容器拒绝、DB不健康不启动业务、健康等待有期限。此前Windows旧临时目录权限导致13个夹具初始化错误（[原记录](restart-unit.xml)），Linux复验暴露旧resume单测桩未适配共享入口的1项失败（[原记录](restart-unit-linux.xml)）；修正测试桩后通过，全部失败保留。43项与原400项重叠，不相加为不同测试总数；新提交完整测试数量由同SHA CI回执给出。

全新resolveflow-accounts-release58a及其恢复副本27项真实检查通过，包含故障位置的停止/重启、原审批恢复、重复退款和离线/排队告警恢复；见[完整报告](local-restart-fix.json)。QA结束停止、卷保留。[再次保护核对](verification-ci-fix.json)32项通过，主库/8056/8057数据身份checkpoint、67运行文件、原容器及历史卷均保留。最后远程完成仍以本次修复提交的成功CI和附件回执为准，首轮失败不被覆盖或冒称成功。
