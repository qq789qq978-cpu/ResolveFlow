# PDF 政策导入

2026-09-23 RAG补充交付。支持**有文本层的PDF维护导入**，可与Markdown放在同一发布集合。没有网页上传接口、OCR或通用自然语言政策问答生成器。本机8003仍运行step36；新功能仅在隔离demo环境验收，部署按后续明确步骤进行。

## 从原件到草稿

使用项目锁定依赖的Python环境（运行时Python3.12、pypdf==6.19.0），或对应新版Docker镜像。命令不会连接数据库，也不会赋予政策审批状态：

```powershell
python policy_pdf.py D:/Policies/materials.pdf --directory work/pdf-draft-new --id materials-v1 --title "退货材料说明" --version "1"
```

目录必须不存在，成功后生成原始PDF、同名`.pdf.json`以及`inspection.json`。后者包含document_sha256、原件SHA256、解析器、物理页码、印刷页标签和每页提取文字；review_status始终为draft。请对照PDF逐页检查漏字、阅读顺序、表格关系、页眉页脚和图片影响。解析成功不证明内容准确，也不等于政策已获审核。

也可把原件与同名sidecar直接放入候选集合，必须严格使用三个字段：

```json
{"id":"materials-v1","title":"退货材料说明","version":"1"}
```

PDF原始字节、路径/版本/标题、提取文字和页来源共同绑定文档身份。修改任一项需重新核对SHA和审核；重命名原件后也要重新计算。原件由维护者保留在发布归档目录，数据库保存提取内容、页映射和原件哈希，**不保存PDF二进制**。备份数据库不能替代备份原件目录。

## 审核、完整集合发布和回滚

1. 从可信的**当前完整发布集合**建立新候选目录；保留现有Markdown/PDF、sidecar、governance.json与release.json，再加入新PDF。发布会替换整个索引，不能仅拿一个PDF目录发布并误删其他政策。内置knowledge仅适用于当前仍是内置集合的情况。
2. 运行`read_documents`或草稿命令获取新文档SHA。governance.json中的policies按文档id登记，document_sha256绑定该SHA；草稿可写`{"status":"draft","document_sha256":"实际SHA"}`。只有维护者完成实际审核后，才能按[治理契约](POLICY_GOVERNANCE.md)登记approved、reviewed_by、reviewed_at、review_note、effective_from、effective_until和review_basis。真实政策用operator_attested；合成demo_fixture只能用于demo，不能伪称人工批准。本次合成材料PDF无真实业务授权。
3. 给release.json一个新的id，保留并核对refund_rule及action_chunks。PDF解析不授予执行权限；更改退款执行条款仍需相应代码审查与发布，不能靠导入文档覆盖确定性规则。
4. 用独立迁移/维护账号查看当前generation，然后发布。以下命令中的DATABASE_URL必须已经指向**预期目标的rf_migrator**，遵循[数据库权限手册](DATABASE_ROLES.md)，不要把管理员连接传给API/Worker：

```powershell
python policy_releases.py status
python policy_releases.py publish --directory work/policy-candidate-new --expected-generation 2 --actor maintainer --reason "已核对新政策原件及审核记录"
python scripts/build_vector_index.py --report work/pdf-vector-build-new.json
```

数字2只是例子，必须替换为status实际返回的generation。向量命令仅在已安装pgvector、已配置本地encoder的hybrid环境执行；发布与向量重建分开，旧批次不能冒充新发布，尚未重建时回退BM25。生产角色本身不能安装扩展；空库hybrid初始化由管理员先安装public.vector，随后按已有迁移流程操作，不能盲跑旧脚本。

5. 在知识库查询新文档关键词，核对返回页码与原文。必要时用维护CLI回滚：

```powershell
python policy_releases.py rollback --release previous-release-id --expected-generation 3 --actor maintainer --reason "回滚原因"
```

被撤销/过期的政策不会随回滚重新获批。历史工单保留旧页来源；旧审批遇发布generation变化会重新核验，不借新文件绕过审批。

## 支持范围和失败处理

- 单文件5MiB、最多64页、总提取文字20万字符；PDF/Markdown合计最多200文档。PDF以物理页为边界分块，每块最多500字符、长段重叠60字符；PDF行号仅指提取文本，界面显示物理页码。印刷页码单独保存，不能替代物理页。
- 子进程解析最长15秒。Linux另设512MiB地址空间和10CPU秒限制；Windows只验证了参数/代码路径，未做原生Windows解析资源故障验收。正式验收在Linux Docker运行。
- 加密、损坏、超限、错误编码及无可提取文字的非空页拒绝。扫描件需先经可靠OCR并由人复核，当前不提供OCR。真正空白页可保留；整份没有文字仍拒绝。
- 有文字也有图片的页只提取文字并在inspection标记图片；图片内容、复杂多栏/表格语义、手写、附件和表单语义未获完整支持保证。没有把有限中英样例的成功推广为所有PDF都能正确解析。
- 单个PDF失败使候选整体失败，当前索引、发布、审核记录不应变化；不要删原件来绕过报错。先修正候选、再次检查，再用最新generation发布。
- 缺少`.pdf.json`时明确报metadata sidecar；`pdf_parser_version_mismatch`需恢复锁定解析器；`pdf_parse_timeout`、`pdf_parser_failed`或`pdf_*requires_review_or_ocr`需检查文件及资源，不自动降级为漏掉该文档。

复现隔离验收见`scripts/pdf_ingestion_qa.py --help`。它仅允许新的resolveflow-qa-*项目，使用demo、合成资料与本地encoder，结束停止容器并保留卷。报告见[补充验收](validation/rag-supplement-2026-09-23/REPORT.md)。
