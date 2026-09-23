# ResolveFlow V3 项目交接

当前状态（2026-09-23，3.9远程验收修复中）：本地完整验收已通过，2debee2已统一推送，但CI 35828431484失败，尚不能认定3.9完成。远程261基础/148PG及构建业务流已通过；roles_qa在干净Runner缺少work父目录时提前失败，尚未进入权限业务检查。已修复roles/runtime/observability/PDF四入口创建父目录，保留目标已存在拒绝；正在无.env和work的全新Git源码导出目录实跑权限/业务回归。失败记录validation/step-3.9-2026-09-23/ci-first-failure.json及txt，原始附件在忽略目录work/step39/35828431484。当前未提交是四脚本修复、失败证据和交接记录；下一项具体操作为干净源码验证通过后提交推送，再确认修复SHA的完整CI和附件。主环境仍step36且数据/卷/审批/checkpoint/退款幂等保留；不改.env、不删卷、不付费/真实退款、不读旧会话排障备份、保留SQL空格。第四阶段未开始。

历史状态（2026-09-23，3.8完成，本地提交交付）：3.8已完成（2026-09-23）：脱敏关联日志、管理员只读告警、独立监控进程，覆盖Worker离线、任务执行过久、排队延迟、连续失败和读取失败状态未知。修复基础设施重试重置运行计时及提交前误报完成；没有数据库迁移。261项不同基础、148项不同PG、21项前端、最终镜像27项双Worker故障检查通过。主库20表/22表结构/7向量和四服务不变，主环境仍step36；所有本步QA停止保留卷。见[3.8报告](validation/step-3.8-2026-09-23/REPORT.md)与[告警手册](OBSERVABILITY.md)。本步仅本地提交，不推送；3.9未开始，下一步独立完整验收、部署交付核验、操作手册、统一推送及对应提交CI。 本步代码、测试、文档和证据全部纳入3.8本地提交；提交SHA以git log最新3.8记录为准，未提交状态以git status复核。证据在validation/step-3.8-2026-09-23，verification.json核对主库与受测镜像源码，首次QA旧事件误判和一次隔离网络冲突均保留记录。最终运行镜像resolveflow:step38-release仅用于QA，未替换主环境resolveflow:local。告警为管理员页面及本地JSON日志，未接外部通知；运行时长预警不等于已证实死锁，去重不跨monitor重启持久化，未做大规模负载或主机整体故障告警验收。下一项具体操作：本轮停止，收到用户指令才执行3.9独立完整验收与统一推送/CI。仍保留同目录、数据/历史卷/审批/checkpoint/退款幂等/权限，不覆盖.env、不付费调用/真实退款，不读旧会话或排障备份，保留延迟SQL空格。RAG补充已完成，人工标签/语义复核仍待审核；PDF无OCR，reranker实验未启用，这些边界不被本步覆盖。

历史状态（2026-09-23，用户追加RAG补充已完成，本地提交交付）：RAG补充任务已完成（2026-09-23，用户在3.8前追加）：80条结构/来源校验、45条公开调优标签及历史输出模型辅助核查；真实reranker45题/218对实测后决定不启用；文本PDF维护导入、物理页来源与完整发布流程已验收。253项不同基础测试、139项PG、18项前端、14项真实PDF混合检索/Worker检查及44项越权拒绝通过。人工复核、通用语义蕴含、OCR仍未完成。见[补充报告](validation/rag-supplement-2026-09-23/REPORT.md)。主库20表/22表结构/7向量及四服务不变，仍step36，新镜像只在隔离demo验收。未推送，3.8未开始；3.9统一推送并确认对应提交CI。 本次PDF代码/测试、审查/实验工具、合成PDF、证据与文档全部纳入本地RAG补充提交；SHA以git log最新RAG补充记录识别，未提交状态以git status复核。证据目录validation/rag-supplement-2026-09-23，verification.json确认主库及容器完整一致。首次专项PG失败（测试漏掉重复例外审批）和首次Compose失败（隔离脚本漏装vector扩展）均保留，最终已通过。四套新QA数据库均停止、卷和网络保留；主服务healthy。下一项具体操作：本轮完成后停止，收到指令才开展3.8；3.9统一推送/CI。仍有效：同目录保留数据/审批/checkpoint/幂等/权限，不覆盖.env，不删卷，不读旧会话与排障备份，保留两处延迟SQL空格，不付费调用或真实退款。PDF原件/复杂版式与实际人工审核须按PDF_IMPORT.md处理；历史生成错误有修正建议但未通过新生成评测证明消除。

历史状态（2026-09-23，3.7已完成）：从47d98a0和原未提交实现接续，在同一目录修复并验收。**3.7连接池、超时、连接耗尽和慢查询行为已完成**（2026-09-23）。240项基础、134项PostgreSQL和23项双Worker业务检查通过；包含非MCP SQL、长事务、整体任务期限、原审批/checkpoint恢复与退款幂等。主库20表/22表结构/7向量保留，四服务仍为step36，step37仅在隔离demo环境验收。见[3.7报告](validation/step-3.7-2026-09-23/REPORT.md)及[运行手册](RUNTIME_LIMITS.md)。本步本地提交、不推送，下一步3.8未开始；3.9统一推送并确认对应提交CI，当前第三阶段无远程CI结果。 6秒任务实测6.162/6.156/6.143秒，无残留任务SQL，原领取锁释放，Worker未重启。证据在validation/step-3.7-2026-09-23；首次失败记录保留。两套业务QA和专项PG全部停止保留卷。本步代码/测试/文档全部纳入本地3.7提交；提交标识以git log最新3.7记录为准，未提交状态以git status复核。尚未验证静默丢包/网络分区、Windows父死亡、付费模型中断及完整历史故障组合。下一项仅在用户指定后执行3.8关联日志、Worker离线/任务停滞/连续失败告警，本轮到此停止。保留两处延迟SQL空格；不读取旧会话/排障备份，不覆盖.env、不删历史卷、不新增付费调用/真实退款；每次一步。

更新日期：2026-09-23。主目录 `D:/AgentProjects/ResolveFlow`，私有仓库 `qq789qq978-cpu/ResolveFlow`。本文替代此前“没有 Git / Docker 尚未实跑”的过期交接说明。验证结果见 [VALIDATION.md](VALIDATION.md)，后续计划见 [ROADMAP.md](ROADMAP.md)。

第一阶段历史交付（2026-09-21）：1.1–1.10收口，[1.10报告](validation/step-1.10-2026-09-21/REPORT.md) 汇总阶段回归、CI入口、交付核验方式与已知边界。12项前端、42项基础、14项PG集成、两组12条合成评测、三条API业务流、14表重建持久化及五类真实故障场景均复验。该阶段提交 e15ea5355d95384b76ac22715fa7c8d1fa82328e 已推送，对应 [Actions运行](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/35577421865) 成功。用户要求每次对话一步、阶段末推送，见 [EXECUTION_PLAN.md](EXECUTION_PLAN.md)。

历史3.6交付：迁移、业务、只读数据库权限隔离完成，232基础/120PG及新装/旧库44项越权拒绝通过，见[3.6报告](validation/step-3.6-2026-09-22/REPORT.md)。本机step36部署状态继续有效，最新3.7代码/隔离验收以上文为准。

本机8003运行resolveflow:step36，resolveflow:local同镜像；DB/embedding未重启，API/Worker已重建。两个一次性任务db-roles/migrate均退出0。新随机角色密码已补入本地.env，未输出或提交；旧.env、停机备份和旧镜像分别保留在work/step36-main/pre-roles.env、pre-deploy.dump、resolveflow:pre-step36。旧主库备份已在隔离库演练接管；新版主库备份位于work/step36-main/restricted-backups/20260922T123412Z-b1e88a0d3e0d。恢复工具按镜像版本重建账号/授权，runtime.env不含管理员连接；维护使用独立maintenance.env。非Docker便携PG未升级，run_local要求受限连接；维护命令见DATABASE_ROLES.md。QA停机保留全部历史卷；无主库新工单、无付费调用。

历史3.5交付：**3.5独立库恢复与待审批resume完成**，见 [报告](validation/step-3.5-2026-09-22/REPORT.md) 和 [操作手册](RESTORE.md)。database_restore.py将可信备份恢复到全新隔离容器/卷，22表内容/结构/版本、索引、三条序列及原checkpoint核对；restore_qa.py实测原工单同意/拒绝、重复审批409和模拟退款幂等。223基础/112PG通过。源备份仍在work/backups/20260922T113706Z-dff0eaa57542；私有随机配置在work/restores/<项目>/runtime.env，最终演练备份在work/restore-qa/resolveflow-restore-step35-final/backups。六套曾启动的本步QA项目均已停止，保留卷；默认地址池耗尽后工具使用不重叠的内部/24网络。主库20张非心跳表、7向量、结构/版本及四服务完全不变，仍为step33运行镜像；无付费调用、无主库新增工单。恢复验证不等于生产切换/异地容灾。下一步3.6未开始，3.9统一推送。

历史3.4交付：**3.4备份工具与版本/时间/校验信息完成**，见 [报告](validation/step-3.4-2026-09-22/REPORT.md) 与 [备份手册](BACKUPS.md)。宿主执行`python scripts/database_backup.py create`，默认生成work/backups下私有备份；verify离线校验。两份22表主库备份实跑、完整解码通过；206基础/104PG通过。原20张非心跳表、7条向量、22表结构及版本保留；四主服务未重启，仍为step33运行镜像。备份脚本通过现有API容器导出快照，无需重建应用镜像。SHA256清单不等于签名，在线序列为观察值；独立恢复尚未验收。无付费调用、无主库新增工单，QA已停止且保留历史库/卷。下一步3.5未开始，每步本地提交，3.9统一推送。

历史3.3交付：**3.3 迁移失败、回滚/继续及不可逆变更恢复完成**，见 [报告](validation/step-3.3-2026-09-22/REPORT.md) 与 [恢复手册](MIGRATION_RECOVERY.md)。187基础/98不同PG检查通过，包含30项恢复专项。LangGraph按锁定3.1.2包/SQL指纹和冻结中间结构续跑；未知结构、缺历史、未来版本或无效索引拒绝。新增bootstrap-demo只在显式demo、结构就绪且所有业务/checkpoint内容为空时播种，绝不猜测旧库意图。应用事务失败/进程强杀回滚、20种库迁移中断窗口、无效索引REINDEX、播种原子性、可逆QA修订降级均验证；不可逆合成删列后恢复至独立库，20表相同且checkpoint可读。

本机8003的API/Worker已更新为resolveflow:step33，resolveflow:local指向同镜像；主库20张非心跳表及7向量保留，结构仍22表、版本rf_vector_0001，无新迁移revision、无新主库测试工单。DB/encoder镜像与启动时间不变，4常驻服务healthy、migrate退出0，demo/hybrid配置不变。主库不执行故障注入或bootstrap-demo。备份work/step33-main/pre-deploy.dump、旧镜像resolveflow:pre-step33保留；QA损坏库、恢复库和卷保留并停机。该段为3.3历史状态；3.4已按上文完成备份工具，3.5已按上文完成独立库待审批恢复，未执行生产切换。

升级前私有备份在忽略目录work/step32-main：before-adoption.dump（用于隔离恢复验证）、pre-deploy.dump（停API/Worker后的备份）；摘要在本步报告中。旧镜像保留resolveflow:pre-step32。它们是本次升级保障，不代表3.4备份机制或3.5完整恢复演练已完成。便携PG和历史QA库未自动接管；旧无版本库运行新版时须先显式adopt。

2.12第二阶段已完成推送：最终提交 `ba17859` 的 [CI 35714352504](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/35714352504) 成功，181基础/47PG/17前端及5类故障198项断言通过。先前失败由QA把Worker多网卡地址误当成多个Worker造成，已修复并加入5项测试。阶段报告见 [2.12](validation/step-2.12-2026-09-22/REPORT.md)，本机同SHA与附件核验回执在忽略的work/step212。此成功记录不代表3.1新提交的远程CI。

2.11既有真实DeepSeek评测：80条RAG+12条业务、280次调用、保守费用3.103362元；保留有据引用覆盖16/20、79项引用/摘录匹配。此次2.12不增加付费调用。私有账本和保留逐题输出仍在work/step211/run，演示政策仍不可授权live；语义蕴含与人工复核待办。2.9暂缓reranker，2.10暂缓PDF解析且明确拒绝PDF输入。

新工单citation_schema=2，citations为chunk_id，quotes为逐字摘录；引用真实不等于语义支持，claim_entailment仍未评测。旧已完成记录保留；旧待审批/执行记录若只有文档ID，恢复后转人工，不静默升级或新增退款。demo只处理明确业务请求，其历史冻结保留集20条可答/部分可答问题全转人工、引用覆盖0/20；2.11真实生成结果见最新报告，不能混用两种口径。政策问答产品能力和语义质量仍需后续完善。

2.2历史基线：[冻结划分](evals/rag/split.v1.json) 将37个意图族分为45条调优/35条保留验收。旧基线检索完整命中15/20、引用覆盖9/20、69项测试通过，见 [2.2报告](validation/step-2.2-2026-09-21/REPORT.md)。原证据未覆盖，不用2.3结果重写旧报告。

后续不要按保留集逐题结果调参：脚本只输出其汇总，调优集保留逐题证据。两侧共享语料和标注模型、人工复核均待办，不能声称外部独立盲测。新版本数据/政策/公开样例变更会触发冻结校验，需显式复核、版本化，不能重洗集合以提高分数。

2.1历史交付：数据见 [cases.v1.json](evals/rag/cases.v1.json)，阅读入口为 [80条复核表](evals/rag/CASES.md) 和 [标注契约](evals/rag/README.md)。固定3份政策/7个片段，为80条问题记录预期证据、回答边界、禁止断言和标注来源。全部为模型生成及同一模型自查，人工待复核、未独立复核；当时53个初步语义分组未拆分，离线校验和54项测试通过，见 [2.1报告](validation/step-2.1-2026-09-21/REPORT.md)。CASES.md的未划分说明保留当时快照，当前归属以2.2的split.v1.json为准。

1.10新增 `scripts/stage1_qa.py`，串行调用1.5–1.9独立故障环境，CI同样调用并保存JSON附件。本机另以 `resolveflow-qa-step110`（8011）验证PG集成、角色权限/业务流和容器重建，主环境13表保持只读不变。没有更改业务代码或替换主环境镜像。下文1.1–1.9的数量均为各步当时的历史快照，最新QA数量与状态以1.10报告为准；不要删除历史卷。

1.9使用独立 `resolveflow-qa-step19`（8010），Compose扩展两个真实Worker，测试保持其PID不变。一个任务退款写入受阻35.016秒时，另一Worker完成无关工单；同订单两条并发工单只有一条退款。MCP SQL超时后每次约10.9秒退出，实际间隔2.045/4.070秒；三次失败后无残留受阻查询，管理员重试恢复原checkpoint。旧版残留查询复现的历史失败任务另经一次管理员重试恢复。复验用 `python scripts/multi_worker_qa.py --report validation/multi-worker-recheck.json`，新增两张合成订单、五条工单、两条退款；勿与同库测试并行。全部demo、无模型调用。

1.8使用独立 `resolveflow-qa-step18`（8009）。调查中、审批保存后各停库约12秒，数据库启动后约8.0/8.2秒观测到原任务完成，期间API/Worker的PID和启动时间不变。最终4条工单、4条done任务、2条审批、1条15900分模拟退款；包含旧版本复现工单，修复后单独做过一次管理员重试。新版本故障场景没有管理员重试。复验用 `python scripts/database_outage_qa.py --report validation/database-outage-recheck.json`，每次新增三条工单，已有退款保留。全部demo，无模型调用。

1.7使用独立环境 `resolveflow-qa-step17`（8008），同主环境镜像、独立数据库与卷。最终5条工单、5条done任务、2条审批、3条模拟退款、26个checkpoint，包含首次窗口校准的RF-1700历史记录。正式验收新增RF-1701/1702两张订单、4条工单与2条退款；原工单重放后均already_refunded，原退款的金额、所属工单、时间完全不变。通过 `python scripts/worker_crash_qa.py --step 1.7 --report validation/refund-replay-recheck.json` 复验，每次新建两张合成订单以确保真正覆盖首次退款提交。测试专用触发器/函数在恢复前删除，主环境没有安装它们。

1.6环境 `resolveflow-qa-step16`（8007）本次回归后共6条工单、6条done任务、6条审批、1条15900分退款，原台账不变。复验使用 `--step 1.6`。1.6初验的3条工单和原证据继续保留，不删除历史数据。

既有QA：8006的 `resolveflow-qa-step15` 在1.6共享脚本回归后共4条工单、4条done任务、1条29900分退款（1.5初验2条工单，1.6回归新增2条），原退款内容不变。复验1.5不加 `--step`，仍使用8006。原8004保持1.1–1.4的11条工单、3条审批、2条核查、2条退款，用 `scripts/operator-qa.ps1 -Action Start` 恢复。所有QA已停止并保留卷；后续新增合成样例，不删除历史数据。

## 当前架构

1.10沿用1.9已部署镜像，主环境三服务healthy；五类故障脚本只在各自QA项目操作，8011重建也不影响8003。全部demo，未调用付费模型、未接真实退款。远程交付状态见 [Actions](https://github.com/qq789qq978-cpu/ResolveFlow/actions/workflows/ci.yml?query=branch%3Amain)，本机同提交核对结果保存在已忽略的 `validation/github-actions-local.json`，避免为了把新SHA写入报告而再次改变受测提交。

1.9收尾状态：修复镜像为 `sha256:77aed6dc7dafa9cbef5b53bc24fa54748adad342d5bceadcfec8bdd9b7b864f9`。本机更新和主库13张表核对记录见 [部署证据](validation/step-1.9-2026-09-21/main-deployment.json)。8010及其他QA停止并保留卷。阶段中途不推送，1.10尚未开始。以下1.4–1.8为各步当时的历史状态。

1.8收尾状态：8003三服务healthy，镜像为 `sha256:e90cbe09ef2a60cd74b762373c71dcb6b93c2780a61ae0e610f1e328d975d30a`。主库13张表在旧版复现、修复验收和部署前后指纹均相同；8009及其他QA停止保留卷。8003和8009已使用新镜像，其他历史QA容器下次启动时按Compose更新。未推送，未开始1.9。

1.7收尾状态：8003三服务healthy，主库13张业务/checkpoint/RAG表从首次窗口校准到验收及回归结束均保持一致；8008、8007等QA已停止并保留卷。全部demo，无模型调用。未修改业务代码或重建镜像，仅扩展故障脚本和证据。未推送，未执行1.8；本次仅终止特定的QA checkpoint连接，不代表数据库整体断网验收。

1.6收尾状态：8003主环境三服务healthy，主库13张业务/checkpoint/RAG表在本步和1.5回归前后指纹均一致。审批已保存后，同向重复和反向改口在Worker停止及恢复后均返回409；后续同订单的另一条已批准工单也未新增退款。未修改业务代码或重建镜像，仅扩展故障脚本和证据。未推送，未验证进程自动拉起；脚本显式启动Worker，任务恢复不需要管理员重试。

1.5收尾状态：8003主环境三服务healthy，测试前后业务、checkpoint和知识库13张表行数及内容指纹一致。8006和8004 QA均已停止并保留卷。未改业务代码或重建镜像，未推送；执行过程明确调用容器启动，未证明Docker自主拉起进程，原任务恢复未调用管理员重试接口。

1.4收尾状态：主环境8003已更新修复后的镜像，三服务healthy；主环境六张业务表更新前后指纹一致。独立QA容器已停止并保留卷，测试代理8005已结束，浏览器临时视口已还原。1.2/1.3记录的过期底部提示与结案建议已修复。页面增加会话/选择校验、请求超时与断线提示，旧响应不能恢复旧角色或覆盖新工单，写请求不会自动重发。前端测试用 `node --test test_frontend.cjs`，CI已增加此检查，但远程CI须阶段末推送后验证。打开旧页面需刷新以加载带版本标记的资源。

- 正式入口 `operations:app` + `worker.py`，前端是 `frontend/dist` 原生 HTML/CSS/JS。
- Compose 管理 PostgreSQL 17、FastAPI、Worker与可选本地embedding服务；本机四服务已启用，入口 `127.0.0.1:8003`，数据库和编码器不开放宿主端口。
- Docker 程序位于 `D:/Programs/DockerDesktop`，Docker 数据在 `D:/DockerData`。便携 PostgreSQL 不与容器数据库混用。
- 当前以 demo 运行。本次升级没有付费模型调用。live 通过兼容接口调用 DeepSeek。
- PostgreSQL 保存业务、队列、LangGraph PostgresSaver checkpoint 和 RAG 索引。Engine 在 PG 模式仍初始化遗留 SQLite 文件，但不以它们作为主数据或 checkpoint；独立测试使用 SQLite。
- 运营、审批、管理员三角色由后端校验，仍采用共享角色码，没有个人账号或租户隔离。

## Agent 与恢复

图流程为 investigate → validate → execute / approval / END。模型负责调查建议，确定性规则作最终分流。退款规则 refund-v2 在事实完整、已签收、证据可用且无冲突时检查“签收不超过7天”和“商品未使用”：两项符合自动模拟退款，零项符合自动拒绝，一项符合人工审批；未知事实优先人工核查。

run_id 同时是 thread_id。API 在同一事务保存工单与任务；人工决定先落库再入队。Worker 以 FOR UPDATE SKIP LOCKED 锁任务行执行，从 checkpoint 继续原工单。退款表订单唯一键防止检查点提交窗口中的重复执行。保证是任务至少一次执行加业务幂等，不是分布式 exactly-once。

普通异常最多自动重试3次，耗尽后管理员可重试。1.8起数据库OperationalError/InterfaceError向Worker抛出，回滚任务事务并重建Engine/PostgresSaver，不消耗业务重试次数；重建失败每3秒再尝试，PG连接建立超时5秒。队列成功更新在保存点内，普通SQL错误先回滚保存点再写失败状态，外层任务行锁仍保留。API数据库不可用返回脱敏503；写入结果不确定时仍应先查询，不自动重复提交。强制退出的未提交尝试不一定计入attempts，正常重建不覆盖所有故障。

1.9起管理员重试使用NOWAIT，任务锁占用时409且不改变状态；自动重试从失败的实际时间起等待2秒、4秒。MCP子进程单条SQL设置10秒statement_timeout，保留原连接的search_path等options；客户端仍为15秒读取、30秒会话限制。查询取消经MCP返回普通工具错误，按三次业务尝试处理；错误记录仅保存类型。上述仅MCP超时是1.9/step36历史运行边界；3.7代码已扩展到退款、队列、checkpoint及API，并增加任务子进程期限，详见RUNTIME_LIMITS.md；主环境未部署3.7。

## 文档 RAG

rag.py 从Markdown元数据或文本PDF的sidecar读取id/title/version，按段落分块，长段落最多500字符、重叠60字符。PDF不跨物理页，保留原件SHA、解析器、页标签与页文字到不可变发布归档；Markdown继续保留文件名、行号、版本、SHA-256。PDF新功能主环境尚未部署，见PDF_IMPORT.md。中文 bigram / 英文词的 BM25 在小语料上排序，默认返回4个片段。

生产索引在 rf_knowledge_documents / rf_knowledge_chunks。空库首次登记内置演示发布，已有索引/发布不被重启覆盖。2.5起policy_releases.py负责显式发布、回滚、审核和历史查询；python rag.py原始导入会取消当前发布身份，同哈希文档保留最新审核，不能借导入复活撤销。查询使用一致快照；数据库错误或发布不兼容不回退到本地样例。

MCP search_policy 调用检索，lookup_order 被宿主绑定当前订单与 owner；没有退款写工具。页面“政策知识库”和 /api/knowledge 可独立查询。工单保留当时证据，后续导入不修改旧工单。2.8已接入本地E5、pgvector和RRF，新增rf_vector_batches/rf_policy_vectors；没有reranker或任意用户上传。启用、发布后索引维护和故障回退见 [说明](SEMANTIC_IMPLEMENTATION.md)。

## 数据与验证

业务表包括 rf_orders、rf_runs、rf_jobs、rf_approvals、rf_audit、rf_refunds、rf_reviews、rf_job_attempts、rf_worker_heartbeats；另有 LangGraph checkpoint 表、两张 RAG 索引表，以及rf_policy_releases、rf_policy_head、rf_policy_reviews、rf_policy_events四张发布表。

verify_persistence.py 比较重建前后行数和完整内容指纹，检查容器ID已变、卷名未变，查询旧工单并恢复待审批工单。动态心跳表不要求逐行一致。该脚本新增合成工单，仅允许demo。报告存放在 validation/。

旧 async_live_report.json 是历史真实模型报告，不代表当前容器使用live。工作流文件存在不代表CI通过；以对应提交的远程结果为准。

## 本次修复

- “没有使用过”等否定短语不再误命中“使用过”；字段缺失不再在冲突检查中直接抛错。仍是有限短语规则。
- 审批或自动执行完成时同步最终说明，避免UI继续显示等待审批。
- CI 的测试数据库密码和容器端口移至 job 环境，清理阶段也可解析Compose。
- 增加文档导入/检索/回滚、PG人工同意路径、知识查询权限和容器重建验证。

## 已知边界

1. 三角色与页面、1.5–1.7各Worker故障窗口以及1.8 PostgreSQL停机造成的连接断开已按报告边界验收。1.7仍需区分在途SQL完成和执行重放；1.8仅验证服务器主动断开/连接拒绝，不覆盖静默丢包、网络分区、实体手机或所有浏览器。
2. 1.9已验证双Worker与退款幂等。3.7保留跨外部调用长事务，但增加统一非MCP SQL期限和每次任务尝试的整体期限，超时后结束子进程并定向清理SQL，再释放行锁。隔离测试证明三次耗尽、管理员恢复和第二Worker进展，不代表任意阻塞/网络分区自动自愈。主环境仍step36。
3. 1.9实测任务停滞时两个Worker仍healthy且在线；3.8已补运行时长/排队/离线与连续失败告警，仍不等于逐节点进展或死锁证明。token汇总不覆盖所有失败重试，不是账单。全部故障验收为demo，不覆盖真实模型中断或整机断电。
4. 3.1–3.3迁移与失败恢复、3.4备份、3.5独立恢复、3.6数据库角色隔离、3.7连接池与运行时期限均已完成；3.7–3.9在隔离环境验收，3.9阶段交付按顶部同SHA CI及附件规则核验，主环境仍step36。
5. 文档版本和程序退款规则必须共同审核；RAG对近义表达有限，需扩大标注集后评估语义检索。
6. 退款使用调查时快照；真实支付前需执行前重查、独立授权、幂等键、对账和状态机。
7. 合成订单、共享角色码和本机服务尚不是公网生产系统。

## 维护约束

不要覆盖.env，不提交密钥、数据库、work/或data/。不要运行docker compose down -v或删除数据库。不要重跑work/下的旧升级脚本。保留订单唯一退款约束、持久化审批、同一thread_id恢复和后端角色权限。

## 关键文件

operations.py/auth.py：API权限；engine.py/refund_policy.py/conflicts.py：Agent与规则；jobs.py/storage.py/worker.py：队列存储；rag.py/knowledge/：文档RAG；mcp_gateway.py/mcp_server.py：工具；frontend/dist/：页面；verify_persistence.py：重建验收；test_*.py/evaluate_*.py：测试评测；compose.yaml/.github/workflows/ci.yml：部署CI。
