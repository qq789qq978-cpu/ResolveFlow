# RAG基线协议 v1（步骤2.2）

本协议和 [冻结划分](split.v1.json) 在首次运行80条检索之前确定。原 [候选标注](cases.v1.json) 保持2.1快照，不回写其 `unassigned` 字段；实际集合归属以split.v1.json为准。全部标注仍为同一模型生成/自查、人工待复核。

## 划分与使用边界

45条调优、35条保留验收。按问题意图审查原53组，将同一问题的近义或对照变体合并为37个族，例如退款条件分支、幂等/重试、模拟与真实到账、丢件与赔付公式、物流停滞与时限、版本可获得性和版本有效期。整个族只进入一侧。

审查了evaluate_rag.py的12条公开查询、evaluate_v3.py的12条业务查询和eval_cases.json的10条历史查询。与公开题相同意图及其对照变体全部进调优，其余进入保留集；没有根据检索分数挑选。清单逐组记录合并来源和公开题编号，历史业务标签不作为当前政策答案。归一化字符bigram Jaccard >= 0.25的跨集合/保留集对公开题相似性检查无未决项；此检查不能替代语义复核。

**“保留验收”只表示不参与调优，不是盲测或独立人工标注。** 两侧共用3份文档/7段证据、同一标注模型，仓库内标签公开，不能证明模型未见过类似问题。分配不是随机抽样，也不强求每类两侧都有；报告逐侧类别/答案类型的样本量，不拿45与35直接比较难度。真实泛化结论需要新增外部独立样本和人工复核。

以后只根据调优集逐题分析修改检索；保留集默认只展示汇总。每次验收记录次数/提交和固定协议。不要查看保留集逐题失败后反复调参；若已用于调优，应退役该保留集并另建独立样本。修改源标注、语料、公开题或分组须显式新版本，不能静默重洗。

## 固定运行范围

- 使用当前 `rag.rank`，top_k=4，保留原BM25分词、标题权重、正分数门槛和排序，不调参。
- 直接从本地冻结文档读取，不使用生产数据库，不重建索引，不读.env，不调用项目模型。
- demo探针调用真实 `Engine.investigate` 的demo分支，仅将MCP工具传输替换为已计算的检索结果及固定合成owner。未实例化Engine、未运行validate/execute、未写checkpoint或退款台账。它测调查节点的建议，不代表完整工单最终结果或自然语言拒答。
- 不用预期标签生成demo输出；标签只进入计分。原12条公开检索冒烟测试单独保留。

## 指标与分母

每项比例保留numerator、denominator、value；分母为0返回null，不能把空集合计为100%。每侧分别报告，不用整体平均掩盖保留集表现。

|指标|计分规则|
|---|---|
|required_group_recall_at_4|只在supported/partial上；每题“命中必要组数/必要组数”后宏平均，组内任一证据命中即满足|
|all_required_at_4|supported/partial中全部必要组均命中的题数/题数；防止只命中一个片段冒充完整证据|
|mrr_at_4|supported/partial中首个必要证据的倒数排名宏平均；未命中0，可选/限制说明不算必要答案|
|complete_evidence_rr_at_4|supported/partial中首次覆盖全部必要组的位置倒数；缺任一组0|
|context_precision_at_4|每条返回片段属于必要、可选或限制说明证据的比例（按返回片段微平均）；只是相关上下文，非答案支持性|
|source_integrity|返回原文、文档ID、片段ID、行号、版本、文件指纹与冻结目录完全匹配的片段数/返回数|
|empty_retrieval_rate|在unsupported和out_of_scope上分别统计空检索比例；有相关限制说明时非空可合理，不称它为系统拒答准确率|
|demo_escalation_rate|真实demo建议action=escalate的比例，在unsupported、out_of_scope分别报告；这是转人工代理指标，不是生成模型拒答率|
|demo_over_escalation_rate|supported/partial中建议escalate的比例；防止全部拒答带来虚高“安全”分数|
|document_citation_validity|demo引用的政策ID确实在本题检索结果中的个数/引用个数；仅证明引用存在|
|cited_required_coverage|supported/partial中，demo引用文档下的**已检索片段**覆盖全部必要组的题数/题数；无引用为0。这是查询证据覆盖上界，不能当作生成断言的蕴含准确率|
|unsupported_citation_rate|unsupported/out_of_scope中仍给出政策引用的题数/题数；记录缺依据问题仍引用政策的风险，不据此断言固定reason已产生幻觉|
|chunk_citation_rate|明确引用实际chunk_id的个数/全部引用个数。当前demo只引用文档ID，细粒度能力基线应为0|

部分可答题的召回只针对可回答部分，不能凭命中给整题“已回答”分。每类小样本只提供描述性计数，不提供生产准确率或统计显著性结论。

## 尚不能计分的回答质量

demo的reason是固定演示说明，没有逐条政策回答或“断言—片段”映射。因此**逐句引用蕴含、回答完整性、部分问题的拒答边界、真实模型拒答率均记为not_measured**，不能把候选answer_outline当作模型输出，不能把引用存在率写成引用支持准确率。2.3接入片段引用后再扩展可观察输出；真实模型评测留到2.11，并单独记录模型、费用、输入范围和人工复核。当前步建立可复现的检索/转人工/引用证据基线及上述明确缺口。

## 运行与回归

```powershell
python scripts/evaluate_rag_baseline.py --report validation/rag-baseline-recheck.json
python -m pytest test_rag_baseline.py -q
```

使用项目锁定依赖，报告路径必须未存在。默认两侧汇总、仅调优集逐题结果；不提供保留集逐题输出选项。报告记录数据/划分/语料及评测与应用代码指纹。`completed=true`只表示协议执行成功，不代表质量合格。当前基线不设任意新质量门槛；CI校验协议、运行和计分行为，低分会如实保留，不修改标签以刷绿。
