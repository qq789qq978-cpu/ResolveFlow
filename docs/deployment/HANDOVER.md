# 第五阶段本地交付与运维交接

交付范围：0元新增支出的本机求职演示版，demo/BM25、合成订单、个人账号和双工作区。公网、真实商家、正式资金不在本次范围。5.8远程完成须满足[报告](../../validation/step-5.8-2026-10-05/REPORT.md)的同SHA CI与附件闸门；没有匹配回执时不能宣布远程验收成功。

## 当前入口与版本

|环境|用途与状态|版本边界|
|---|---|---|
|127.0.0.1:8057，resolveflow-accounts-trial57|推荐演示入口；个人账号、双工作区、订单同步、100工单/日与三Worker|固定step56-capacity镜像；5.7生成配置修正alpha预期Worker为2，beta为1|
|127.0.0.1:8056，resolveflow-accounts-capacity|保留的5.6环境|业务运行文件相同；监控期望配置未升级，不作为部分Worker掉线演示入口|
|127.0.0.1:8003，resolveflow|历史主环境继续保留|step36；不能用它证明个人账号、PDF、期限或后续告警已经部署|
|8053/8054，local/orders|历史部署已停止，卷保留|不要直接重跑首次初始化或旧升级脚本|

8057运行镜像为 `resolveflow:step56-capacity`，固定ID `sha256:417e98da2cbef1f69d998587feff4327b81fa05359b3a13ae4c01cda7c4d6d72`。5.8核对67个运行文件与该镜像一致；发布脚本/测试/文档在宿主执行，不属于该镜像。Git最终版本以 `git rev-parse HEAD` 和同提交CI回执为准，不用镜像标签推断源码版本。

新部署结构是浏览器→个人身份gateway/SQLite→指定工作区API→独立PG与Worker。alpha两个Worker、beta一个，彼此不借槽；两库隔离订单、政策、checkpoint和退款台账。每工作区各有私有合成HTTP订单源及只读monitor。gateway维护全局账号、会话、额度与限流；后台每次核验个人身份和权限。旧架构图/Word仍是第三阶段和2026-09-23截图的历史材料，新增能力以本页及阶段五证据讲解，不改旧图日期或冒称截图覆盖新版。

## 日常演示

从 `D:\AgentProjects\ResolveFlow` 执行。先启动Docker Desktop的Linux引擎，再恢复8057已有服务（5.8修复后按现有容器ID分层等待健康，不运行初始化/迁移）：

```powershell
.venv\Scripts\python.exe scripts/local_trial.py resume --work work/resolveflow-accounts-trial57
docker compose --env-file work/resolveflow-accounts-trial57/empty.env -p resolveflow-accounts-trial57 -f work/resolveflow-accounts-trial57/compose.json ps
```

12个长期服务应healthy，仅gateway发布回环端口。维护员 `maintainer` 密码在私有 `work/resolveflow-accounts-trial57/bootstrap.txt`；维护员无业务权限。验收业务账号 `admina`/`reviewera` 属于alpha，`adminb`/`operatorb` 属于beta，密码仅在同目录 `trial-accounts.json`。不要复制到Git或聊天。账号创建/停用/会话撤销见[账号手册](../../ACCOUNTS.md)；最多5个有效账号含维护员，演示前勿盲目创建第六个账号。

演示建议：管理员查看合成订单/政策证据→运营提交一条工单→审批员处理需要审批的工单→管理员查看结果与告警。不要处理已保留的验收待审批工单 `cf00ad9e-8bd1-4c59-9969-f51f14ce9183`，需要新演示就创建新工单。已退订单的新请求仍可能先要求审批，不能绕过；同订单原退款台账不变。重复提交新工单消耗日额度，额度与审计不可手工清空。

停止只操作本项目，保留容器与卷：

```powershell
docker compose --env-file work/resolveflow-accounts-trial57/empty.env -p resolveflow-accounts-trial57 -f work/resolveflow-accounts-trial57/compose.json stop
```

新机器使用[本地部署](../../LOCAL_DEPLOYMENT.md)和[容量配置](../../CAPACITY.md)，选择从未使用的项目名/空闲端口，生成时同时启用 `--orders --capacity`。串行生成并启动；已有部署只resume，不能把首次初始化命令当成重启。私有配置、账号、数据库备份不在Git，新机器需按手册生成自身密钥。

## 备份、恢复与故障处理

重要演示或修改前做身份SQLite＋双PG的联合备份。备份会短暂停止本部署写入并恢复原先运行的服务；使用新的目录，先记录当前状态。现存20261005T091446641380Z备份及独立目录副本均保留于私有work目录，两个目录都在D盘，不是异地备份。5.8再次校验其完整性。

```powershell
$rfBundle = 'work/resolveflow-accounts-trial57/backups/manual-' + (Get-Date -Format 'yyyyMMddTHHmmss')
.venv\Scripts\python.exe scripts/local_backup.py create --work work/resolveflow-accounts-trial57 --bundle $rfBundle
.venv\Scripts\python.exe scripts/local_backup.py verify --bundle $rfBundle
```

按[备份恢复手册](../../LOCAL_DEPLOYMENT.md)恢复到全新项目/新卷及空闲端口；旧会话撤销，重新登录，核对身份、结构、数据、原审批恢复与重复退款后再切换入口。保留源环境；恢复失败停止候选，不覆盖原卷。只验证同固定镜像的快照恢复/入口回退，未认证跨版本schema降级。5.7实测事故点RPO约312秒、恢复约90秒、回退约96秒。

|现象|首项操作|
|---|---|
|入口不可达|检查Docker、目标项目ps及gateway健康；按resume顺序恢复，不初始化|
|Worker离线/队列增长|在8057管理员页看告警及关联run_id；核对alpha两Worker/beta一Worker，确认数据库就绪|
|任务执行超时/连续失败|按[告警手册](../../OBSERVABILITY.md)核对脱敏关联日志及任务状态；管理员确认可重试后操作，不能直接改队列或台账|
|429/额度满|等待限流窗口/香港自然日切换；100/日是全部署共享，审批恢复不计新工单|
|提交503或结果未知|先查列表与额度审计中的run_id；未知额度保守占用，不盲目反复提交|
|订单同步错误|按[同步手册](../../ORDER_SYNC.md)核对workspace、版本、状态与合成源；不直接改订单表|
|GitHub失败邮件|按邮件SHA/运行ID比对当前HEAD；查看该次失败步骤，修复后新提交并推送，旧失败邮件不会被撤回|

七天自动任务 `resolveflow-5-7` 保持PAUSED，宿主采样已停止；容器monitor继续运行。固定单日status是历史验收结果，**不能当作当前健康探针**。需要新演练时另存新证据，不改原日期或冒充七天。

## 验收与未完成项

|要求|状态和证据|
|---|---|
|AC-01/02 账号、权限与隔离|通过；[5.2](../../validation/step-5.2-2026-10-03/REPORT.md)、[5.6](../../validation/step-5.6-2026-10-05/REPORT.md)|
|AC-03 本地入口、密钥、持久化|按本地范围通过；[5.3](../../validation/step-5.3-2026-10-03/REPORT.md)|
|AC-04 备份与恢复|按当天1份及独立目录副本通过；[5.7](../../validation/step-5.7-2026-10-05/REPORT.md)，未验证七份跨日保留|
|AC-05/06 合成订单、业务和幂等|通过；[5.4](../../validation/step-5.4-2026-10-03/REPORT.md)、5.6复验|
|AC-07 服务商沙箱|用户明确暂缓，未通过；[5.5](../../validation/step-5.5-2026-10-05/REPORT.md)仅local_mock契约，不接业务支付|
|AC-08/09 容量与安全|通过已定义样本；5账号含维护员，100工单/日，alpha2＋beta1；[5.6](../../validation/step-5.6-2026-10-05/REPORT.md)|
|AC-10/11 单日运行与本地故障演练|按用户修订通过；约26.1分钟实际观察、28项演练，不是24小时/七天或生产SLA|
|AC-12 发布交接|以当前HEAD与远程main相同、CI成功、附件回执passed=true及干净工作区联合判定；[5.8](../../validation/step-5.8-2026-10-05/REPORT.md)|

长期可靠性、七日备份保留、30天审计留存时长、公网/HTTPS、异地备份、宿主整体离线外部监控未验证。RAG标签人工复核、通用引用语义支持性和OCR仍未完成；reranker实测未获益，保持关闭；PDF只支持文本层的维护CLI导入。4.4简历/面试材料继续暂缓，5.8完成后停下，后续另行指定。

发布执行 `python scripts/verify_delivery.py`，下载当前SHA的evaluation附件并核验基础/PG/前端、四项第五阶段报告、权限/期限/告警/恢复及五类历史故障。容量压力和单日演练复用明确日期的本地证据，不声称它们在CI重新实跑。回执在忽略文件 `validation/github-actions-local.json`、原始zip在 `work/delivery`，避免提交回执造成SHA循环；任何后续修改都须新提交并重新核验。
