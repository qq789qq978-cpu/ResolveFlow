# 4.3 截图材料与操作说明复核

复核日期：2026-10-03。起点为同目录 main 分支的 4.2 提交 `f98ba14`，开始时工作区干净。本步只复核和补充展示材料，不录制视频、不推进 4.4、不推送。4.5 再统一推送并核验对应提交 CI。

## 交付与结论

- [使用指南](../../docs/demo/PRESENTING.md)：Word 页码、九个场景、证据对应关系、讲解计数与能力边界，可直接用于离线演示。
- [复现说明](../../docs/demo/REPLAY.md)：补充 Docker/Compose/镜像只读检查、已测镜像 ID、失败处理、历史状态与当前状态区分。
- [图文入口](../../docs/demo/README.md) 和生成器同步新增导航、复核入口，避免后续生成覆盖文案。Word、story.json 和九张 PNG 保留原件与原日期。
- 重新渲染 Word 并检查全部 11 页；九张原图也已逐张检查。标题、图片、正文、页码可读，没有空白页、错图或溢出，无需补拍或替换截图。

## 核验方式

[audit.py](audit.py) 对留存证据重新计算业务结论，对比 4.2 哈希、Word 图片像素与 story 裁切范围，核对九场景正文、页码、相对链接及运行源码，并执行六个不依赖 Docker 的入口拒绝检查。共 **28 项检查通过**，含 46 个本地链接、27 个运行源码引用核对；结果见 [verification.json](verification.json)。这些是离线材料检查，不是重新运行全部应用测试。

重新核对的业务证据包括：四个工单的订单和最终状态、同一待审批工单恢复、checkpoint 4→6、审批角色与完整理由、退款金额 29900/15900 分、重复申请后完整退款行不变、零模型调用和 token，以及五次执行/零失败的历史监控记录。原记录来自 2026-09-23，未修改，不能当作今天新运行的结果。

视觉检查记录见 [visual-review.json](visual-review.json)。宿主无 LibreOffice，使用 Microsoft Word 只读导出 PDF，再以文档技能的 `render_docx.py` 栅格化，逐页查看 PNG。中间文件在忽略目录 `work/step43/render`；不作为新 PDF 或视频交付。程序检查只能证明文件一致性，视觉判断另行记录。

复核脚本使用带 Pillow、pypdf 的文档依赖 Python，在仓库根目录执行；输出文件必须不存在：

```powershell
python validation/step-4.3-2026-10-03/audit.py --render-dir work/step43/render --output work/step43/recheck.json
```

## 操作材料检查

对照 `scripts/demo_environment.py`、`scripts/demo_evidence.py` 与 OPERATIONS.md 检查：隔离命名、保护 8003、拒绝旧容器/卷/网络、端口检查、使用空 env 文件、demo/BM25/无模型密钥、只读采证及 stop 保留卷均与复现说明一致。六个入口检查实跑覆盖非法主项目、8003/低端口/越界端口、主项目采证、已有证据路径拒绝；都应在 Docker 操作前退出 2。Docker 依赖的资源冲突检查、启动和停止没有本次重跑，沿用 4.2 的历史核验并明确范围。

## 环境限制与未验证内容

本次只读探测确认 Docker Linux 引擎不可用，管道 `dockerDesktopLinuxEngine` 未找到；见 [environment.json](environment.json)。未启动 Docker Desktop、容器或数据库，未修改 `.env`，未触碰历史卷。**本日主环境健康、历史卷现状、镜像是否仍在以及全新实时复演未验证。** 主环境 step36、演示停止和三卷保留是 9 月 23 日的留存状态。

本步的验收目标是静态截图材料与操作说明复核，因此无需启动环境即可完成。若今后要求现场操作，必须先通过 REPLAY.md 的环境检查，再在全新隔离 demo 复演；不能直接宣称本次已复演通过。

真人讲解时长、人工标签/通用语义支持性、OCR、外部告警、生产上线仍未验证；reranker 不启用，文本 PDF 仅维护 CLI 导入。没有新增付费模型调用或真实退款。CI 35832707932 仍仅认证代码 `526e166`，不代表当前材料提交已有 CI。

本步更新交接、执行清单及路线图后本地提交；提交 SHA 由 Git 历史记录，工作区以 git status 为准。下一项为 4.4 简历、项目说明和面试问答，等待用户指令。
