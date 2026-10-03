# 截图演示复现

静态展示直接打开 [Word](ResolveFlow-demo.docx) 或 [图文脚本](README.md)，无需启动服务。以下仅用于再次生成真实页面和证据，不要在主环境 8003 操作。

翻页、计数与讲解边界见 [使用指南](PRESENTING.md)。截图日期为 2026-09-23；4.3 复核时 Docker 引擎不可用。随后 4.5 已恢复 Docker 并在独立环境复验，最新结果见 [交付入口](../../DELIVERY.md)；本组截图没有重新采集。

## 复现前检查

在仓库根目录先执行以下只读检查，每条均须成功后再继续：

```powershell
python --version
docker version --format '{{.Server.Version}}'
docker compose version
docker image inspect resolveflow:step39-ci-fix --format '{{.Id}}'
```

需要 Python 3.9+、可用的 Linux Docker 引擎和 Compose v2。若提示找不到 `dockerDesktopLinuxEngine` 管道，说明引擎不可用；不要把后续脚本的失败当成演示业务失败。先安排 Docker 恢复，再重新检查。启动 Docker Desktop 可能按重启策略带起原有容器，先确认能接受该影响；仅做静态展示无需启动它。

4.2 留存的已测镜像 ID 为 `sha256:a1fbdc1037c4bee5d50d98954b9ed96a6cd7402992f602cd4ac32046dabeaa11`。标签可变，若当前 ID 不同，不应沿用原镜像的验收结论。镜像缺失时从明确的代码基线构建专用标签，再传入 `--image`；可在单独目录解包 `git archive 526e166` 后构建，不切换或覆盖当前工作目录、不复制 `.env`。新构建镜像应重新验证，不能仅凭标签相同认定已测。

## 全新隔离环境

在仓库根目录执行（示例名必须从未使用过，8027 必须空闲）：

```powershell
python scripts/demo_environment.py start --project resolveflow-qa-demo-replay01 --port 8027 --image resolveflow:step39-ci-fix
```

脚本使用 Docker、现有 Compose 和已测镜像，随机生成独立数据库密码与三个角色授权码，保存于忽略目录 `work/resolveflow-qa-demo-replay01/demo-env.json`。不读取或覆盖 `.env`，不构建/替换主镜像。需要已有 Python 3.9+、Docker 和镜像；若镜像不存在，先按 [操作手册](../../OPERATIONS.md) 从对应代码基线构建专用镜像再传入 `--image`，不要盲用主环境旧镜像。

打开 `http://127.0.0.1:8027/`，只在本地读取该 JSON 中的 `APP_API_KEY`（运营）、`REVIEWER_API_KEY`（审批）、`ADMIN_API_KEY`（管理员）填入登录框。不要把授权码截图、复制进报告或提交 Git。页面不保存登录码，刷新后需要重新输入。

启动入口检查已有容器、卷、网络、工作目录及端口，拒绝覆盖旧演示；失败时保留已创建资源并停止服务。脚本只允许 `resolveflow-qa-demo-*` 项目名。它不是通用部署或旧库升级工具。

## 操作顺序

1. 运营选择 RF-1001，输入“申请退款”，点击开始调查；等待“已模拟退款”，记录完整 run_id。
2. 运营选择 RF-1004，输入相同诉求，等待“等待审批”；保存审批前证据。
3. 切换审批角色，按 RF-1004 找到**原工单**，填写“合成演示：核对订单与政策后，批准本次例外模拟退款。”并批准；等待原工单完成，保存审批后证据。
4. 切回运营，再次提交 RF-1001；等待“已拦截重复退款”，核对原台账完全不变。
5. 可选对照：提交 RF-1002，等待“自动拒绝退款”。本文监控截图包含这条对照工单，所以复现同样计数时应执行此项。
6. 切换管理员，查看 Worker 与告警；政策框查询“重复退款”。按图文脚本的讲解顺序组织截图。不要把检索候选片段都称为已采用证据。

流程用时取决于运行环境；按状态推进，不依赖固定等待秒数。写入返回不确定时先查询工单，不自动重复点击。四条工单最终应为 2 个 refunded、1 个 already_refunded、1 个 auto_rejected，只有 2 条模拟退款；审批为 1 条，Worker 执行尝试为 5 次。

|遇到的问题|处理方式|
|---|---|
|项目资源或工作目录已存在|保留它们，换全新项目名；失败创建留下的资源也不作为可重置空库|
|端口占用或 8003 被拒绝|选另一个空闲非主环境端口，并同步浏览器地址|
|角色切换后不能审批|确认已登录审批角色且打开 RF-1004 原工单；不要把运营授权码当审批码|
|工单没有到预期状态|记录 run_id，查询原工单与管理员监控；停止重复提交，按操作手册排障|
|证据路径已存在|换新的文件名；审批前、后和最终证据不能互相覆盖|
|启动或停止报错|保留资源与脱敏错误，确认引擎可用后针对同一项目执行 stop；不删除卷或盲目重启初始化|

## 保存只读证据并停止

在审批前、审批后及最终分别使用不同的输出路径，不覆盖已有证据。例如最终：

```powershell
python scripts/demo_evidence.py --project resolveflow-qa-demo-replay01 --output work/resolveflow-qa-demo-replay01/final.json
python scripts/demo_environment.py stop --project resolveflow-qa-demo-replay01
```

证据收集器只通过 API 和只读数据库账号查询，不输出授权码。停止会保留所有卷；**不要使用 `down -v`，不要删除退款、审批或 checkpoint 来重置场景。** 若要重新展示首次退款，使用另一个全新项目名和空闲端口。

2026-09-23 采集结束时，项目 `resolveflow-qa-demo-step42` 已停止、数据卷保留；此为历史核验状态。复现入口不会在这个名字上重新初始化。主环境约定端口为 8003，与该演示无关。

## 文档维护

讲解、截图关联工单和 Word 裁切范围在 [story.json](story.json)。`scripts/build_demo_document.py` 用相同来源生成 Markdown 与 Word，需使用文档依赖环境中的 python-docx、Pillow。修改 Word 后要重新渲染并逐页目视检查；渲染中间 PDF/PNG 放本步忽略目录（4.3 为 `work/step43`），不当作视频交付。仅修改操作说明时无需重新生成 Word，更不要把原图采集日期改成复核日期。

PDF 导入、故障恢复、告警故障注入没有在这组截图中重新执行；参见 [PDF 导入手册](../../PDF_IMPORT.md)、[期限手册](../../RUNTIME_LIMITS.md)、[告警手册](../../OBSERVABILITY.md) 和 [3.9 验收](../../validation/step-3.9-2026-09-23/REPORT.md)。人工语义复核等边界继续保留。
