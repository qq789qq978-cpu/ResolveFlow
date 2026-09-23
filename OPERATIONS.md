# ResolveFlow 操作与阶段交付手册

适用于第三阶段交付的本地 demo：PostgreSQL 17、API、Worker、独立 monitor；hybrid 另加本地 encoder。共享角色码和模拟退款不是公网生产授权。日常操作在目标检出的项目根目录执行；命令默认作用于当前 Compose 项目，先确认项目和卷，切勿把 QA 命令套到主库。

## 新机器、新库

需要 Git、Docker Compose v2；维护/验收宿主 Python 3.9+，应用镜像内使用锁定依赖和 Python 3.12。前端测试另需 Node 22+。克隆仓库后，只有不存在 `.env` 时才从 `.env.example` 创建；本地填写不同的随机网页角色码和数据库管理员密码，设 `MODE=demo`、`RETRIEVAL_MODE=bm25`、空 `OPENAI_API_KEY`，不提交配置。

```powershell
python scripts/setup_db_credentials.py
docker compose up --build -d --wait --wait-timeout 180
docker compose ps -a
docker compose exec -T resolveflow python db_migrate.py check
```

角色脚本仅追加缺失的三个受限账号密码。DB healthy 后运行 db-roles，再 migrate，随后 API、Worker 和 monitor；两个一次性任务 Exited(0) 正常。访问 `http://127.0.0.1:8003`。新装 demo 显式播种，旧库不会重播种；无版本或结构不明的旧库应拒绝启动，不绕过检查。

hybrid 的权重下载、固定版本/哈希及 encoder 配置见 [SEMANTIC_IMPLEMENTATION.md](SEMANTIC_IMPLEMENTATION.md)。先启动 pgvector 数据库并显式安装 vector 扩展，再迁移与建索引，详见 [MIGRATIONS.md](MIGRATIONS.md)。BM25 安装无需权重、付费 API 或本机历史备份。

## 已有库升级与回退

1. 核对 `git status`、目标提交、现用镜像/卷和 [迁移说明](MIGRATIONS.md)；停止外部接单，等执行中/排队任务处理完，保留等待人工审批的 checkpoint。先用下节工具生成并校验备份，记录旧镜像 ID。
2. 构建独立版本标签，保持旧镜像可用。停止 API/Worker（本次版本已有 monitor 时也停止 monitor），在维护窗口只用 db-roles/migrate 执行账号配置和 `prepare`。仅确认符合冻结旧基线且尚无版本的库使用 `adopt`，不是每次升级都执行。
3. 使用一致的新镜像重建 API/Worker/monitor，检查结构、健康、后台心跳与告警，再验证旧工单可读和原待审批可恢复。不要让普通 API 持有迁移或管理员连接。
4. 失败先停止接单并保存证据。只在旧程序与当前结构、权限契约兼容时回退应用镜像；不可逆结构变更从可信备份恢复到独立库核对后再安排切换，绝不直接覆盖原卷。checkpoint 中断和未知结构按 [MIGRATION_RECOVERY.md](MIGRATION_RECOVERY.md) 处理。

本次 3.9 的部署交付验证在隔离环境进行，主环境继续 step36。以上是今后维护程序，不代表本轮已升级主服务或完成生产切换。

## 备份、校验与恢复

```powershell
python scripts/database_backup.py create --project resolveflow --output work/backups
python scripts/database_backup.py verify work/backups/<本次备份目录>
python scripts/database_restore.py work/backups/<本次备份目录> --project resolveflow-restore-new --report validation/restore-new.json
python scripts/restore_qa.py work/backups/<本次备份目录> --prefix resolveflow-restore-rehearsal-new --report validation/resume-new.json
```

替换尖括号路径，每次恢复用新项目和新报告。备份要求单个 API/Worker、匹配镜像、就绪结构，包含归档、清单和校验和；只读快照与完整归档解码通过才算成功。备份内含业务数据，留在忽略的私有目录，异地保存需另外安全配置。SHA256 不是来源签名。

恢复工具拒绝已有目标，创建独立库/卷，核对结构、全部表、序列和 checkpoint，并按备份镜像契约重建账号。首次恢复不自动启动 Worker；`restore_qa.py` 只在确认全部工单为 demo 且无未完成队列后生成合成审批样例，第二次恢复后继续原工单。源库在续跑期间停止，证据与卷保留。详见 [BACKUPS.md](BACKUPS.md)、[RESTORE.md](RESTORE.md)。没有自动异地备份、PITR 或生产 RPO/RTO 承诺。

## 日常观察与故障处理

```powershell
docker compose ps -a
docker compose logs --since 10m --tail 200 resolveflow worker monitor
```

管理员页面的告警与 `GET /api/alerts` 均受后端权限保护。后台 monitor 使用 rf_readonly，不依赖浏览器；API/Worker/monitor 日志轮转为各 3×10 MiB。按 request_id → run_id → attempt_id/worker_id → task_token 定位请求、执行尝试及 SQL。只分享脱敏事件，不分享 `.env`、完整 inspect 或业务原文。

|现象|处理与验收|
|---|---|
|Worker 离线|检查该 Worker 容器、健康和退出原因，确认配置后恢复该服务；等心跳恢复并出现 resolved，不能只看 API healthy|
|任务运行过久/队列延迟|检查 run_id 对应尝试、SQL 等待与资源占用；运行时长告警不等于死锁。人工审批等待和未来退避不应误报|
|连续失败/重试耗尽|先解除根因，再由管理员重试原工单；核对原审批/checkpoint 与退款唯一性，不通过直接修改数据库跳过流程|
|数据库不可用|API 返回 503，monitor 标为未知并保留未解决事件；恢复 DB 后确认 Worker 续跑和 monitor 恢复，不能将读取失败视为正常|
|连接池耗尽/慢 SQL|按 [RUNTIME_LIMITS.md](RUNTIME_LIMITS.md) 检查池等待、SQL/锁/空闲事务和整体任务期限；扩容前衡量各进程连接总数，不简单取消超时|
|迁移失败|查看一次性迁移退出码与受控错误，核对锁/结构/版本；未知结构不盲目重试旧升级脚本|

详细阈值、心跳正常但任务停滞及告警截断语义见 [OBSERVABILITY.md](OBSERVABILITY.md)。告警目前仅管理员页面和本地日志，无外部消息投递或跨进程持久去重；宿主/monitor 整体离线还需外部平台观察。

暂停项目优先 `docker compose stop`；普通重建保留卷。不要删除数据库卷，不运行带 `-v` 的 down 或全局清理。多 Worker 时同步 RF_EXPECTED_WORKERS，备份工具目前要求单 Worker，应在受控维护窗口使用。

## 政策、PDF 和质量边界

政策导入、审核、发布与回滚使用迁移维护账号，参见 [POLICY_RELEASES.md](POLICY_RELEASES.md)、[DATABASE_ROLES.md](DATABASE_ROLES.md)。PDF 用 [PDF_IMPORT.md](PDF_IMPORT.md) 的维护 CLI，先解析为草稿，核对原件/物理页与治理信息，再显式发布；解析失败不得影响当前发布。支持文本层 PDF，无 OCR 和网页文件上传。

模型辅助标签/引用审核不等于人工审核或通用语义蕴含验证；这些工作仍待人工完成。真实 reranker 实验未达到启用门槛，保持关闭。历史付费评测为历史证据，本轮 demo 回归不新增付费调用，也不把检索命中/引用逐字匹配称为生产准确率。

## 独立复验与 GitHub 交付

```powershell
docker build -t resolveflow:release-check .
python scripts/rag_supplement_tests.py --image resolveflow:release-check --report-directory validation/release-check-tests
node --test test_frontend.cjs
python scripts/stage3_qa.py --image resolveflow:release-check --project resolveflow-qa-release-check --report validation/release-check.json
python scripts/runtime_limits_qa.py --image resolveflow:release-check --project resolveflow-qa-release-runtime --report validation/release-runtime.json
python scripts/observability_qa.py --image resolveflow:release-check --project resolveflow-qa-release-alerts --report validation/release-alerts.json
```

按顺序执行，复跑改用新名称。脚本从受版本控制的配置创建全新数据库，结束停止容器而保留卷，不依赖主目录 `.env`。真实本地 encoder/PDF 回归另用 `scripts/pdf_ingestion_qa.py`，必须显式提供已核对的模型目录；远程 CI 不下载模型。

真实旧版本升级复验使用 `scripts/migration_qa.py --image resolveflow:release-check --legacy-image <冻结旧版镜像> --prefix resolveflow-qa-upgrade-new --report validation/upgrade-new.json`。旧版镜像可从第二阶段提交 `ba1785975a24cced4502b48bece24cbaa3560f3a` 的 Git archive 在独立临时构建目录重新构建；不要切换当前工作目录或运行旧升级脚本。需要空闲的 8017/8018 端口。该入口同时拒绝已有容器和孤立历史卷，在隔离新库演练旧待审批接管和恢复。

CI 从干净检出安装锁定依赖，运行基础/PG/前端、冻结检索结构和描述性评测、Compose 业务流、权限/期限/告警、全新安装重建及备份恢复，以及历史五类强杀/停库回归。任何失败必须保留，修复后以新提交重跑全部 CI。

阶段结束统一提交推送，并同时确认：本地 HEAD = origin/main = GitHub workflow head_sha；工作流成功；该次 evaluation 附件中的 JUnit、持久化、阶段恢复、权限、期限、告警及五类故障报告成功。CI 的 BM25/合成向量结果不代替本地真实 encoder 验收。

实际 SHA、run URL 和附件核对回执保存在忽略的 `validation/github-actions-local.json`，避免把自身提交 SHA 回写后制造新未测提交。阶段报告见 [3.9 验收报告](validation/step-3.9-2026-09-23/REPORT.md)。完成 3.9 后停止，第四阶段由用户另行指定。

推送后可执行 `python scripts/verify_delivery.py --status` 查询本地 HEAD 的 push 工作流，再执行 `python scripts/verify_delivery.py` 下载并核对对应附件。该脚本使用本机 Git 凭据管理器或 GH_TOKEN/GITHUB_TOKEN，只在内存中使用凭据，不写入报告；产出同 SHA 回执和忽略目录内的原始附件 ZIP。没有运行、未成功或证据缺失时不能作为交付完成。
