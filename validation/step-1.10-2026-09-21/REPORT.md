# 1.10 第一阶段回归与交付

日期：2026-09-21。本地阶段回归全部通过，原1.1–1.9的实现与证据已收口，真实故障验收接入GitHub Actions。最终交付条件是：本地HEAD与远程main一致，且该SHA的 `ResolveFlow checks` 工作流全部成功。远程状态和可下载原始证据由 [同提交Actions记录](https://github.com/qq789qq978-cpu/ResolveFlow/actions/workflows/ci.yml?query=branch%3Amain) 维护，不用旧提交的绿色结果替代。

本次只完成1.10，未开始2.1。没有修改业务代码或主环境镜像；应用沿用已验证的1.9版本，新增阶段回归入口、CI步骤和文档。所有测试为demo、合成订单和模拟退款，无付费模型调用。

## 本地回归证据

|范围|结果|证据|
|---|---|---|
|前端异步/会话/断线|12项通过|[frontend-tests.txt](frontend-tests.txt)|
|基础、规则、Engine、MCP与RAG|42项通过|[unit-tests.txt](unit-tests.txt)|
|PostgreSQL、权限、队列与恢复|14项通过，仅依赖弃用警告|[postgres-tests.txt](postgres-tests.txt)|
|离线业务分流|12/12合成样例|[evaluation_v3.json](evaluation_v3.json)|
|文档检索|12/12合成查询；Recall@4=1，MRR@4≈0.944，无关查询拒答3/3|[evaluation_rag.json](evaluation_rag.json)|
|真实API与Worker|RF-1001自动退款、RF-1002拒绝、RF-1004审批拒绝；401/403/409权限与重复操作检查通过|[async-demo.json](async-demo.json)|
|容器删除后重建|三容器ID改变、卷名不变、14表内容一致，3条既有工单保留；待审批工单原checkpoint恢复为rejected，无新增退款|[persistence.json](persistence.json)|
|1.5调查中Worker强杀|25项通过|[step-1.5.json](faults/step-1.5.json)|
|1.6审批保存后Worker强杀|52项通过|[step-1.6.json](faults/step-1.6.json)|
|1.7退款提交后checkpoint前中断|59项通过，自动/人工两路径重放不重复退款|[step-1.7.json](faults/step-1.7.json)|
|1.8 PostgreSQL短时停机|35项通过，保持API/Worker进程恢复原调查与审批任务|[step-1.8.json](faults/step-1.8.json)|
|1.9双Worker、长事务和超时|28项通过，退款唯一、MCP SQL取消及重试间隔正常|[step-1.9.json](faults/step-1.9.json)|

五类故障共199项断言，[串行汇总](faults/summary.json)。1.8本轮已有历史退款，多一项“原退款内容不变”检查；在全新CI库该项不适用，故总数可以不同，不应硬编码一个断言数作为通过条件。各脚本的 `passed`、具体断言及退出码共同决定结果。

1.1–1.4的浏览器操作证据继续见各步报告；本轮复跑12项前端回归及HTTP角色流程，未声称重新完成实体手机或所有浏览器人工验收。业务和RAG各12条样例也不是生产准确率、人工标注大集或真实模型质量评测。

## 数据与运行状态

- 主环境8003保持三服务healthy，镜像为 `sha256:77aed6dc7dafa9cbef5b53bc24fa54748adad342d5bceadcfec8bdd9b7b864f9`。13张业务/checkpoint/RAG表从本轮开始至结束指纹完全一致，见 [local-summary.json](local-summary.json)。主环境没有接受测试工单或重建。
- 1.5–1.9分别在8006–8010独立项目执行并保留历史；各故障脚本串行运行，没有共享活动测试库。
- 额外 `resolveflow-qa-step110`（8011）使用独立数据库和卷执行集成、API流程与重建，见 [runtime.json](runtime.json)。它与故障环境不共享数据库；其PG集成结束后才开始业务和重建操作。
- 本轮全部QA容器已停止，卷及历史保留；临时故障触发器由相应脚本清理。13表主环境指纹与14表重建指纹的差异是后者额外包含LangGraph的 `checkpoint_migrations` 表；动态心跳表均不做逐行一致断言。
- 仅演示模式，模型调用为零，未接触真实支付。没有做云端部署、数据库迁移或删除历史数据。

## CI与复验

新增 `scripts/stage1_qa.py`，按1.5、1.6、1.7、1.8、1.9串行运行已有真实故障脚本；遇到失败停止后续场景并保存汇总，各子脚本负责自己的停止和锁清理。使用未存在的报告目录，避免覆盖证据：

```powershell
python scripts/stage1_qa.py --reports validation/stage1-recheck
```

前提为Docker可用、主环境demo健康、`resolveflow:local`镜像与主环境一致、8006–8010端口可用。宿主Python仅需标准库。不要同时启动使用这些QA项目的其他脚本。

工作流在全新检出上安装依赖，运行前端、基础/MCP、PG、两组离线评测，构建镜像并通过HTTP业务验证；随后重建容器、恢复审批，再运行故障回归。设置30分钟作业上限；正常收尾不删除数据卷。`evaluation` 附件包含业务/RAG报告、HTTP报告、持久化报告和五类故障JSON。

推送后的核验必须满足：远程main等于本地HEAD；该SHA工作流 `status=completed` 且 `conclusion=success`；测试、全新构建、重建、故障回归和附件上传均成功。实际run URL、head_sha和步骤结果同时保存在本机已忽略的 `validation/github-actions-local.json`。不把该SHA回写进同一受测提交，避免修改报告又改变SHA；GitHub对应提交及其Actions附件是远程证据来源。

## 阶段交付边界与下一步

- 已验证的是列出的进程故障、数据库主动断连、双Worker和受控阻塞窗口。脚本显式启动被强杀的Worker，不能声称任意进程均会被自动拉起。
- 队列仍持长事务锁跨越外部调用；退款/checkpoint等非MCP连接缺少统一SQL超时，没有整张工单总期限，也没有任务停滞告警。心跳正常不代表业务在推进；这部分继续留在3.7、3.8。
- MCP单条SQL限制10秒，客户端读取15秒、会话30秒，属于局部限制；本步没有验证付费模型中断或静默网络分区。
- 迁移、备份恢复、连接池/耗尽、个人账号、真实订单/支付与公网部署仍按既定阶段处理。第一阶段完成不表示生产就绪。
- 下一步 **2.1：准备约80条RAG评测查询，记录预期证据、标注来源和复核状态**。2.1尚未执行；按约定等待下一次对话启动，第二阶段结束时再统一推送。
