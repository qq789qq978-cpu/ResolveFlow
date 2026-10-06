# 真实本地 reranker 实验

2026-09-23补充实验，补充2.9的历史条件评估。使用cross-encoder/mmarco-mMiniLMv2-L12-H384-v1，revision `1427fd652930e4ba29e8149678df786c240d8825`，模型卡Apache-2.0。协议在推理前写入[RAG_SUPPLEMENT_PLAN.md](RAG_SUPPLEMENT_PLAN.md)，原始结果和模型文件SHA在[reranker.json](validation/rag-supplement-2026-09-23/reranker.json)。

断网容器、CPU2线程、内存上限2GiB；只读取本地safetensors，不启用远程模型代码。对原45条调优查询、218对query/原文片段真实打分，最长512token，无截断。35条保留集未跑、未用于挑模型；仍用冻结标签，标签采用冻结版本，审查方式为模型辅助核查。

|方案（top4）|完整证据命中|必要组召回|首条相关MRR|完整证据RR|上下文相关片段|
|---|---:|---:|---:|---:|---:|
|原BM25/E5混合|40/40|100%|0.933333|0.843750|59/180|
|同4段重排|40/40|100%|0.945833|0.833333|59/180|
|BM25/dense并集重排选4|39/40|98.75%|0.945833|0.825000|58/180|

40是有必要证据的题数，45还包含2个政策缺口、3个范围外/噪声题。来源一致性均100%，不等于答案正确率。没有按本轮结果反复调整阈值、模型或划分。

冷加载1.4405秒；每题候选池打分p50 0.1630秒、p95 0.2205秒；进程峰值RSS732.31MiB。此延迟仅包含缓存候选的tokenize/重排，**不包含在线召回、生成或端到端请求**。同4段的测量复用候选并集分数，没有声称另测服务延迟。本地下载/CPU有资源成本，本轮付费API调用0。

决定：**保留实验脚本和证据，不启用该模型进入线上检索**。首条相关位置略有提升，但完整证据排名退化，扩大池后还丢失一题所需证据，未满足“不损失必要证据且有稳定收益”的目标。这不是证明所有reranker无效，也未证明生成答案改善或保留集泛化。

复现：用`scripts/download_reranker.py --help`下载固定revision到新目录，再用已有embedding镜像运行：

```powershell
docker run --rm --init --network none --cpus 2 --memory 2g -v D:/AgentProjects/ResolveFlow:/source:ro -v D:/AgentProjects/ResolveFlow/work/rag-supplement/reranker-model:/model:ro -v D:/AgentProjects/ResolveFlow/work/reranker-rerun:/evidence -w /source resolveflow:embedding-local python scripts/reranker_experiment.py --model /model --report /evidence/reranker.json
```

输出目录须事先创建，报告必须是新路径，旧证据不能覆盖。模型文件未提交Git。
