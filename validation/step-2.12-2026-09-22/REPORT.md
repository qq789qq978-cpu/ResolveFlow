# 2.12：第二阶段回归与交付

2026-09-22。本地第二阶段回归已通过，2.1–2.11代码、条件评估、质量测量和限制已汇总。阶段最终交付条件：本地HEAD等于远程main，该SHA的`ResolveFlow checks`全部成功，且下载的同次CI附件通过核对。远程证据见[Actions](https://github.com/qq789qq978-cpu/ResolveFlow/actions/workflows/ci.yml?query=branch%3Amain)，不能用旧提交的绿色徽章代替。

本步完成阶段收尾；3.1未开始。本次全部本地与CI测试使用demo、合成数据和模拟退款，不新增付费生成调用。2.11的真实DeepSeek测量作为既有证据保留，不重新调用模型刷分。

## 第二阶段交付与质量结论

|步骤|交付或条件结论|证据|
|---|---|---|
|2.1–2.2|80条模型候选标签、45/35冻结划分、完整计分口径；人工待复核|[数据集](../../evals/rag/README.md)、[基线](../step-2.2-2026-09-21/REPORT.md)|
|2.3|chunk_id和逐字摘录、旧引用保护、无依据转人工|[报告](../step-2.3-2026-09-21/REPORT.md)|
|2.4–2.5|政策审核来源、有效期、原子发布/回滚、规则版本绑定，审批/执行再次核验|[治理](../step-2.4-2026-09-21/REPORT.md)、[发布](../step-2.5-2026-09-22/REPORT.md)|
|2.6|BM25扩展调优完整命中36/40→40/40，保留仍15/20|[报告](../step-2.6-2026-09-22/REPORT.md)|
|2.7–2.8|固定本地E5-small、pgvector、BM25/RRF，保留初次检索完整命中17/20|[选型](../step-2.7-2026-09-22/REPORT.md)、[实现](../step-2.8-2026-09-22/REPORT.md)|
|2.9|当前证据不支持继续增加reranker，条件评估后暂缓，未冒充模型实测|[报告](../step-2.9-2026-09-22/REPORT.md)|
|2.10|暂无PDF来源需求，暂缓解析/OCR/页码；修复混合目录静默遗漏，明确拒绝PDF|[报告](../step-2.10-2026-09-22/REPORT.md)|
|2.11|80条RAG+12条业务真实DeepSeek测量；280次请求，保守费用3.103362元|[报告](../step-2.11-2026-09-22/REPORT.md)|

最新质量结果须按口径区分：混合检索保留初次top4完整命中17/20；真实生成最终有据引用覆盖16/20；79/79保留引用和摘录匹配原文。模型可追加搜索，不能把最终引用覆盖混称为初次检索召回。保留集可答题仍有17/20转人工；当前Agent面向订单调查，并不等于完整政策问答产品。

80条标签均为模型生成、人工待复核，两侧共享语料；不是外部盲测。引用匹配不证明自由reason的语义蕴含、答案完整性或自然语言拒答正确性。原3份政策保持demo_fixture，live不予授权；2.11的12条业务全转人工、退款0不能叫退款分流准确率100%。第二阶段完成是按这些可验证边界交付，不是生产质量合格。

## 本地阶段回归

|范围|结果|证据|
|---|---|---|
|基础、Engine/MCP、RAG、治理/发布、语义检索、计分/预算|176项通过|[unit-tests.txt](unit-tests.txt)|
|PostgreSQL队列、权限、故障恢复、发布和向量一致性|47项通过，1项依赖弃用警告|[postgres-tests.txt](postgres-tests.txt)|
|前端角色切换、并发响应、引用治理显示、断线恢复|17项通过|[frontend-tests.txt](frontend-tests.txt)|
|原80条标注结构/来源与冻结BM25基线|结构通过；数据/划分及两侧全部汇总与2.6最终基线一致|[标注检查](rag-candidates.json)、[基线](rag-baseline.json)|
|离线业务及小型词法检索样例|各12/12；非生产准确率|[业务](evaluation_v3.json)、[检索](evaluation_rag.json)|
|真实本地E5、混合查询和Worker业务|7条流程通过，含重复退款、拒绝、物流、审批及转人工|[hybrid-qa.json](hybrid-qa.json)|
|检索降级与资源边界|4种降级、并发200/503、2CPU/2GiB、只读模型及阻断公网出口通过|[同一演练](hybrid-qa.json)|
|容器删除重建与审批resume|19表指纹完全保留；已保存的审批同意恢复为refunded，总共2条模拟退款|[同一演练](hybrid-qa.json)|
|防误复用QA|已有项目明确拒绝，容器ID/状态不变、卷保留|[repeat-guard.json](repeat-guard.json)|

本地正式BM25基线是阶段复现，非再次选参；CI也会复现该基线。未追加混合检索保留集质量选择或付费评测。新QA入口复用2.8的已验证场景；其旧的步骤字样/公开fixture凭据不代表重复使用旧数据库。

主环境8003保持4服务healthy、demo/hybrid。原19表、容器镜像/启动时间和配置从开始到结束完全一致，见[开始](runtime-before.json)与[结束](runtime-after.json)。独立项目resolveflow-qa-step212使用新构建应用镜像；本次构建复用依赖层缓存，远程CI另做干净检出的依赖安装与构建。QA已停止，命名卷与历史全部保留。

[最终核对](final-checks.json)验证主环境与新QA镜像内应用源码一致、冻结标签/政策/规则及历史证据未变、文档链接与密钥检查通过。[扩展快照](extended-snapshot.json)实际读取QA全部20张目标表（包含checkpoint_migrations），覆盖新加入的6张政策/向量表。19表重建指标沿用原混合场景口径，不把额外读取1张表冒称为20表重建验收。演练后补入已有项目拒绝保护并单独验证，未改动原混合场景。

## 复验与CI覆盖

本步新增[scripts/stage2_qa.py](../../scripts/stage2_qa.py)和[Compose覆盖配置](../../compose.qa-stage2.yaml)。需Docker、8016空闲、固定E5权重和编码器镜像；模型获取/哈希检查见[语义检索运行说明](../../SEMANTIC_IMPLEMENTATION.md)。示例使用新的项目名和报告名：

```powershell
docker build -t resolveflow:stage2-qa .
# 编码器镜像不存在时，先按语义检索运行说明构建并验证模型文件。
python scripts/stage2_qa.py --project resolveflow-qa-stage2-recheck --report validation/stage2-recheck.json
```

不要并发运行共享8016端口的QA。脚本拒绝已存在应用容器/应用卷的项目，结束后停止而不删卷；下次复验换新项目名。若需改变权重路径，设置RF_EMBEDDING_MODEL_DIR；若无编码器镜像，构建`resolveflow:embedding-step28`。禁止通过删除历史卷绕过新项目要求。

CI包含176项基础、47项PG、前端、离线业务/检索/基线、全新构建、API/Worker、容器重建及1.5–1.9五类真实故障。新增JUnit附件；持久化脚本补入4张政策发布表，并在存在时核对2张向量表。默认CI是BM25部署，pgvector集成用合成向量；**不会下载E5权重或调用付费模型**。真实本地编码器和混合重建由本步本地报告证明，不将其冒称为远程CI覆盖。

推送后必须检查本地/远程SHA一致、同SHA工作流成功，下载同次`evaluation`附件核对JUnit、18表默认部署持久化、业务结果和五类故障全部通过。实际run URL、SHA、步骤状态和附件核对结果保存在忽略的work/step212目录；不回写该SHA到受测提交而制造新的未验证提交。最终以GitHub对应提交及附件为远程证据。

## 下一步与待办

下一步仅为**3.1：迁移框架与现有数据库基线，明确LangGraph表管理边界**。未开始3.1，也未实施备份恢复、连接池、停滞告警或云端部署。

人工标签/回答复核、通用政策问答产品边界、真实审核政策的live放行仍是质量待办；PDF和reranker待实际需求/证据再启动。主库仍靠启动建表，LangGraph自行管理checkpoint表；第三阶段按既定3.1–3.9处理数据运维，不在阶段收尾混入迁移变更。
