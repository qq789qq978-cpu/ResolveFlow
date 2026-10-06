# 3.9 第三阶段完整验收与交付

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

2026-09-23，从 394377f 接续，在原目录保留所有数据和历史卷。本轮只完成 3.9；主环境继续 step36，最新版本仅在隔离 demo 部署验收。没有主库故障注入、付费模型调用或真实退款。第四阶段未开始。

本地完整验收已通过，本提交用于第三阶段统一推送。远程结果只按实际工作流及下载附件认定，不预填成功结论。最终交付必须同时满足本地 HEAD = 远程 main = 成功工作流 head_sha，并下载同次附件核对。不能用第二阶段或其他 SHA 的绿色 CI 代替。

## 本步交付

- 新增 `scripts/stage3_qa.py`：不读取宿主 .env，从新项目、新卷完成新装、44 项权限拒绝、业务路由、四服务重建、备份完整解码与两次独立恢复。原工单分别同意/拒绝、重复审批拒绝及退款唯一性均检查；源服务在恢复续跑时停止。
- 新增 [统一操作手册](../../OPERATIONS.md)，将新装、旧库升级/回退、权限维护、备份恢复、运行期限、告警与 PDF 操作串联起来。
- 旧版升级脚本增加独立子网和已有容器/孤立卷保护。本轮旧版镜像从冻结的第二阶段提交 `ba1785975a24cced4502b48bece24cbaa3560f3a` 通过 Git archive 重新构建；未读取或执行本机旧升级备份。
- 修正 PG 退避测试的调度敏感断言：使用同一数据库时钟验证持久化期限晚于实际失败至少 1 秒，避免提交后观察消耗退避时间导致误报；仍拒绝从长事务开始时计时的错误实现。
- CI 新增独立安装/恢复入口，保留原持久化与五类故障回归，时限调为 40 分钟。`scripts/verify_delivery.py` 核对同 SHA 工作流、全部 job 和下载附件，凭据不进入报告。

## 本地复验

|范围|结果|证据|
|---|---|---|
|完整基础/PG 回归|261 基础、148 PG 通过|[基础](regression-final/unit.xml)、[PG](regression-final/postgres.xml)|
|前端行为|21 通过|[frontend.txt](frontend.txt)|
|独立新装、重建、备份与恢复|16 检查通过；18 张非心跳/非版本数据表重建保留、20 表备份/恢复|[install-restore-final.json](install-restore-final.json)|
|真实旧版本迁移和原审批续跑|3 项通过；旧业务/审批/checkpoint保留，原工单恢复|[migration-final.json](migration-final.json)|
|运行期限、SQL 超时与双 Worker|23 项通过|[runtime.json](runtime.json)|
|关联日志和独立告警故障恢复|27 项通过，结束告警为空|[observability.json](observability.json)|
|真实本地 encoder、PDF 发布/回滚及业务|14 项及44项权限拒绝通过|[pdf-hybrid.json](pdf-hybrid.json)|
|离线规则及小型检索样例|各 12/12，通过不代表生产质量|[业务](evaluation_v3.json)、[检索](evaluation_rag.json)|
|冻结 RAG 标注/描述性基线|80 条结构校验通过，数据/划分/语料和 2.12 分组指标完全相同|[结构](rag-candidates.json)、[描述性基线](rag-baseline.json)|
|受测镜像与当前运行源码|54 个文件逐项匹配（统一换行 SHA256）|[image-source-sha256.json](image-source-sha256.json)|
|已有资源拒绝保护|2 项通过；仅有停止容器、没有数据库卷时仍拒绝复用，原容器状态/ID不变|[existing-resource-guards.json](existing-resource-guards.json)|
|无本机残留目录的权限业务验收|从Git导出且起初没有.env/work的目录实跑，44项权限拒绝及业务流程通过|[clean-checkout-roles.json](clean-checkout-roles.json)|
|主环境保留|20张非心跳数据表、22表结构/7向量及四服务完全一致；QA停止保留卷|[开始](main-before.json)、[结束](main-after.json)、[总核对](verification.json)|

基础/PG 测试在隔离内部网络和全新 pgvector 数据库运行。独立验收串行分配子网，全部项目结束停止且保留卷。BM25 安装无 encoder 依赖；本地 PDF 混合检索另显式使用已固定的 E5 权重，不调用付费服务。完整回归覆盖迁移中断/继续、未知结构拒绝、不可逆变更独立恢复、备份校验、账号权限、池耗尽与执行期限。

## 失败与修正

1. 首轮基础 261 通过、PG 147 通过/1 失败：[原始记录](regression/postgres.xml)。`test_slow_failure_backoff_starts_after_failure` 观察到剩余 0.471732 秒，未满足旧的半秒下限；日志证明慢调用后已经安排退避，提交/调度消耗时间。已用数据库失败时间与持久期限取代“观察时剩余时间”的判断，完整复验结果以上表为准。
2. 首次阶段入口完成新装/权限/重建/备份后，恢复项目名前缀被既有保护拒绝：[失败报告](install-restore.json)。修正新入口的命名为 `resolveflow-restore-*`，未放宽恢复工具；全新项目完整复验 16 项通过。
3. 旧版升级首次在内部隔离网络中新装健康，但 Windows 宿主访问发布端口被拒绝：[失败报告](migration.json)、[过程](migration.txt)。验收脚本改为从 API 容器内调用真实 HTTP 接口，与其他 QA 保持一致，不放开外部网络；重新使用全新项目，结果以上表为准。

失败报告和对应卷保留，不覆盖成成功结果。Shell 命令构造期间的宿主引号错误未进入业务验收，不计作业务测试结果。

最终审查补强两个新入口的资源保护：显式列出停止容器并检查已有网络，避免默认 `container ls` 只检查运行容器。定向实际创建两个从未启动的合成容器后，两个入口均在启动服务前拒绝且原容器不变；`guard-stage3.json` 的 passed=false 是预期拒绝证据，汇总以 existing-resource-guards.json 为准。容器保留且从未启动，没有数据库卷。

## 第三阶段证据边界

3.1–3.3 的迁移框架、旧库接管、LangGraph 中断续跑与恢复由当前 PG 回归及真实升级共同验证；3.4–3.5 的备份和独立恢复由本次全新安装源库两次恢复验证；3.6 由受限角色实测；3.7–3.8 由双 Worker 故障脚本验证。历史每一步的首次证据保留，不用本轮结果重写旧报告。

本轮新增 core 新库的备份为 20 表，不能冒称 22 表 hybrid 备份。本机原有 hybrid 主库 22 表/7 向量的保留由开始/结束快照核对；此前 3.5/3.6 的 22 表恢复证据仍有效。PDF 回归另覆盖真实 encoder 和物理页来源，但不是 OCR、复杂版式或人工语义审核。

人工 RAG 标签/引用语义复核仍待完成，reranker 实验不启用，PDF 仅支持文本层维护导入；本轮不追加生成质量评分。告警只有管理员页面/本地事件，无外部通知和跨重启持久去重；运行过久不是死锁证明。无公网生产部署、整机灾备、PITR、大规模容量、静默丢包/网络分区、真实模型中断或真实支付验收。本轮前端为自动化行为回归，没有重新做实机浏览器截图验收。

## 推送与同提交 CI

本轮统一推送第三阶段及已完成的 RAG 补充。推送后等待 `ResolveFlow checks` 在该提交上全部成功，核对 261 项基础、148 项 PG 的实际 JUnit（数量以实际附件为准）、独立安装/恢复、权限、运行期限、告警、重建及 1.5–1.9 五类故障附件。CI 默认 BM25 与合成向量，不下载本地 E5 权重，不产生付费调用。

实际 SHA、run URL、步骤与附件验证回执存放于被忽略的 `validation/github-actions-local.json`，原始附件保存在 `work/delivery/`。不把自身 SHA 回写该提交而制造新未验收版本。远程证据以 [Actions](https://github.com/qq789qq978-cpu/ResolveFlow/actions/workflows/ci.yml?query=branch%3Amain) 对应提交为准，最终回复给出确切运行链接。

首轮提交 `2debee2` 的 [CI 35828431484](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/35828431484) 失败，不能作为阶段成功证据。远程261基础/148PG、前端、离线评测、构建及异步业务流先后通过，但 roles_qa 在创建工作子目录时因干净检出没有被忽略的 work 父目录而抛出 FileNotFoundError，尚未执行权限业务断言。见[失败摘要](ci-first-failure.json)和[错误片段](ci-first-failure.txt)。

已修复 roles/runtime/observability/PDF 四入口使用 parents=True 创建缺失父目录，同时保留 exist_ok=False 拒绝已有项目目录。应用运行时代码和权限契约未更改。从已提交Git源码导出到全新目录，仅覆盖这四处修复，确认没有.env和work后实际运行权限QA，44项拒绝、自动退款/拒绝、审批续跑及重复退款均通过；此证据只针对该入口，不冒称四个入口都在缺父目录时完整跑过。修复提交仍须重新通过完整远程CI和附件核验，首轮失败及原始附件保留。

第二轮 `ca46fed` 的 [CI 35829414120](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/35829414120) 在权限、期限、告警、新装恢复和普通持久化通过后，失败于1.5观察阻塞阶段（仅5项断言，尚未SIGKILL）。不是旧邮件重复，也不是可忽略的告警。原远程报告只保存RuntimeError，不能声称知道远程丢失的异常子类型；[失败摘要](ci-second-failure.json)和[全新本地复现](fault-diag15a.json)区分两者。本地观察到任务checkpoint结构检查在读取rf_orders约束时被测试表锁阻塞，尚未进入MCP订单查询，观察器因此TimeoutError。

修复将每次任务的checkpoint目录读取限定为四张checkpoint表，完整应用/迁移检查保持全表校验。真实PG回归同时证明：业务表锁不阻止checkpoint就绪检查、完整应用检查仍受该锁约束、checkpoint结构漂移仍被拒绝。退款重放QA改在真实退款INSERT已阻塞后才安装checkpoint触发器，先让任务通过真实结构检查；仍验证退款已提交但checkpoint及缓存输出均未提交的强杀窗口，不放宽应用结构契约。历史故障入口支持显式镜像和新项目前缀，拒绝已有停止容器/卷/网络，原主服务继续step36。失败诊断保留异常类型、命令和隔离库阻塞活动。

第二轮修复后的回归记录：基础261项及额外资源保护测试合计264个不同测试通过；PostgreSQL149项通过（含新增锁/漂移回归）。见[基础JUnit](regression-ci-fix/unit.xml)、[资源保护JUnit](fault-resource-guards.xml)、[PG JUnit](regression-ci-fix/postgres.xml)。受测镜像resolveflow:step39-ci-fix的54个运行文件与当前源码一致，见[哈希核对](image-ci-fix-sha256.json)。此前step39-release的完整安装/升级/PDF证据保持其原镜像口径，最终修复提交还必须通过完整同SHA远程CI。

最新镜像的五类真实故障本地复验全部通过：1.5调查强杀25项、1.6审批保存后强杀52项、1.7退款提交后强杀59项、1.8停库恢复34项、1.9双Worker/长阻塞/超时28项，共198项。见[五类汇总](fault-fix-a/summary.json)及同目录逐项完整记录。[最终核对](second-ci-fix-verification.json)确认全部清理无错误、四个主服务及20表完整快照不变、仅原主服务运行，所有测试卷保留；没有部署主环境或付费模型调用。此结果是在推送前完成，不用后续CI替代本地故障验证。

人工质量审核和条件上线阶段的限制继续保留。
