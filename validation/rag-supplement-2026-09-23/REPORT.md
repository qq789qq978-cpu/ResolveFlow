# RAG补充验收 — 2026-09-23

从本地3.7提交a20793e接续，在原目录完成用户追加的RAG-S1/S2/S3；未开始3.8。本次本地提交、不推送，3.9统一推送并核对对应SHA的CI。主环境不部署新版，仍运行step36。

## 标签和引用支持性

[support-review.json](support-review.json) / [逐题记录](SUPPORT_REVIEW.md)：80条冻结样例结构、语料指纹、原文来源校验通过；45条公开调优标签按政策问题含义核查，保留候选标签。45条历史真实生成建议的引用摘录均能匹配来源，但发现3处把用户题设写成数据库事实（008/046/047）、1处把退款流程泛化为必须审批（034）、1处扩张问题范围后误判证据不足（049），以及若干漏答、部分答复和额外核查建议。

这是模型辅助审查，非人类或独立审核，不能称语义准确率通过。逐题记录保留问题、必要证据、历史原句、判断与修正建议。35条保留未做逐题语义审查或调参；v1、知识库与旧指标不变。未用新付费生成调用验证错误已消除。

[产品边界](../../RAG_SUPPORT_REVIEW.md)：知识查询只读返回有效政策原文及来源，不要求订单、不产生业务写入；订单调查绑定真实数据库事实，由规则和审批决定执行。后端claim_entailment仍为not_assessed，引用真实不能代替自由文本蕴含判断。网页说明已同步。

## 真实reranker实验

固定协议、模型及文件SHA见[原始结果](reranker.json)与[实验说明](../../RERANKER_EXPERIMENT.md)。本地cross-encoder/mmarco-mMiniLMv2-L12-H384-v1，45条调优、218对真实评分，0截断；断网、2CPU、2GiB，无付费API调用。

|方案|完整证据命中|首条相关MRR|完整证据RR|
|---|---:|---:|---:|
|原hybrid top4|40/40|0.933333|0.843750|
|相同top4重排|40/40|0.945833|0.833333|
|候选并集重排选4|39/40|0.945833|0.825000|

冷加载1.4405秒，候选池打分p50/p95为0.1630/0.2205秒，峰值RSS732.31MiB。不是端到端在线延迟，没有保留集泛化或生成质量改善证据。**实验完成，不启用该模型**：必要证据与完整排名退化，不满足事先目标。模型权重留在忽略的work目录，不进Git。

## PDF实现与验收

新增policy_pdf.py与锁定pypdf6.19.0。文本PDF和同名sidecar进入已有完整发布流程，逐页分块，原件SHA/解析器/全文页映射进入不可变release payload；无需数据库迁移。DB重读、BM25/向量/混合检索、grounding、Worker快照和前端保留真实物理页码。原Markdown身份和退款动作绑定不改。

维护CLI先生成草稿、原件、sidecar和可逐页检查的inspection.json，再由维护者按现有治理契约审核发布；没有自动批准或网页任意上传。解析有文件/页数/字符数和子进程时间资源限制；加密、损坏、无文字非空页等显式拒绝。详见[导入手册](../../PDF_IMPORT.md)。

- [regression/unit.xml](regression/unit.xml)：完整原CI基础列表加首版PDF检查，252通过。
- [pdf-final-unit.xml](pdf-final-unit.xml)：最终PDF版本13通过，包含新增的真实CLI草稿/逐页文字导出，12条与上项重叠。合计**253条不同基础测试**，没有将复跑相加冒充不同用例。
- [regression/postgres.xml](regression/postgres.xml)：**139通过**，含5条新增PDF集成。验证原文/页码DB往返、只读API无业务/checkpoint改动、失败发布原子性、归档篡改拒绝、撤销后回滚不可复活、向量发布身份、旧审批重新核验和退款幂等。PG向量专项用合成向量测试契约，不作模型质量结论。
- 前端18通过（含物理页/Markdown行号区分）；运行输出frontend.log，本目录日志显式纳入Git。
- [pdf-compose-final.json](pdf-compose-final.json)：全新受限角色Compose、真实本地E5、pgvector、API和Worker，**14项检查及44项越权拒绝通过**。真实CLI草稿/发布、PDF两页混合检索、查询无副作用、损坏文件发布失败、工单保存页来源、自动模拟退款/拒绝/人工审批resume、同订单幂等、回滚及历史PDF快照保留。
- [pdf-visual-review.json](pdf-visual-review.json)、[第一页](pdf-rendered/materials-1.png)、[第二页](pdf-rendered/materials-2.png)：合成中英双页PDF经Poppler渲染，助手逐页检查文字可读、无裁切/重叠。非真实业务政策或人工审批，未覆盖所有PDF版式。

基础/PG列表已加入CI配置；本次没有推送，也没有新的远程CI结果。真实PDF Compose脚本需现成固定本地E5模型路径，不自动在普通CI里下载新模型。

## 失败记录及修正

首次专项[focused/postgres.xml](focused/postgres.xml)为4通过/1失败：新测试错误期待例外退款新工单未经审批就返回already_refunded。既有正确流程先审批，再在执行时幂等拦截；已补必要审批，最终139项中通过。业务规则未因此放宽。

首次[pdf-compose.json](pdf-compose.json)启动失败：新测试脚本遗漏管理员预装public.vector。迁移正确拒绝；修正隔离初始化次序后，换全新项目pdf-supplement-final完成验收，旧失败库保留，没有盲重跑主库迁移。最终无测试失败。

## 保留状态与未验证边界

[verification.json](verification.json)逐一核对[before](main-before.json)/[after](main-after.json)：主库20张非心跳数据表、7条向量、22表结构/版本、四服务镜像/启动时间/卷映射**完全一致**。`.env`未修改，未付费调用、无真实退款，原延迟SQL空格保留。两个专项PG数据库、两套PDF Compose环境都停止，所有卷保留；只有原四个主服务运行且healthy。

人工标签/语义复核仍待实际审核人完成；运行时未实现通用蕴含判定。PDF只验证文本层与有限合成样例，OCR、手写、复杂表格/多栏/图片理解、原生Windows资源故障未验收；PDF二进制需维护者另存，DB只存文字及来源身份。没有全量重跑历史1.5–1.9故障组合、没有把新功能部署主环境；独立环境完整验收与统一推送仍归3.9。

下一步仅在用户指定后开展3.8。RAG补充完成，不自动新增付费模型评测或人类审核结果。
