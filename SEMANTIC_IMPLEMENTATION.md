# 本地 embedding 与混合检索（2.8）

2026-09-22。冻结选型见 [2.7方案](SEMANTIC_RETRIEVAL.md)，本步结果、运行版本和失败记录见 [验收报告](validation/step-2.8-2026-09-22/REPORT.md)。本步不包含 reranker、PDF 导入或付费生成模型评测。

## 运行方式与数据范围

本地 `intfloat/multilingual-e5-small`，固定 revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`，MIT 许可。使用 FP32 CPU、384 维、attention-mask mean pooling、L2 归一化，query/passage 前缀按原方案固定。输入超过512 tokens明确拒绝，不截断原文。当前7片段实际长度57–95 tokens。

编码器独立容器限制2 CPU/2 GiB，单进程、并发1。模型文件只读挂载，启动时逐文件校验；使用离线加载、禁用远程代码和遥测。只连接 Docker internal 网络，不发布宿主端口，不持有数据库密码或项目模型密钥。运行时发送到该本机容器的内容仅为检索查询或政策标题/片段；不保存查询向量，不记录请求正文。首次下载公开权重需要联网，推理不使用云端服务；付费模型 API 调用为0。

API 和 Worker 通过 MCP 使用同一检索配置。MCP 仅传递三个检索环境变量，检索在线程中运行，以便同步检索内部的有界异步 HTTP/SQL 不嵌套到 FastMCP 事件循环。

## 索引与安全边界

PostgreSQL17 + pgvector0.8.6，新增 `rf_vector_batches` / `rf_policy_vectors`。7个向量使用精确 cosine 查询，没有 HNSW/IVFFlat 近似索引。批次绑定发布内容SHA及embedding契约；清单记录每个片段的文档SHA、标题/正文SHA及向量float32校验和。

向量先编码，再在事务中锁定政策文档并核对发布代次和治理状态，最后整体写入。任一失败不留下部分批次。查询验证当前发布、原文、完整清单、全部向量，并在top-k前排除不可用政策。向量不替代原文、chunk_id、版本、行号、治理状态或退款规则校验。相同内容回滚可复用向量，但审批仍须匹配当前发布generation，不能借复用绕过旧审批校验。这些属于应用一致性检查，不防护拥有数据库完全写权限的人重写全部校验值。

BM25使用2.6的expanded配置；dense及BM25各top4，以RRF(k=60)融合，dense权重0.25。默认配置仍为BM25；本机验收通过后显式启用hybrid。无索引、内容/向量不符、编码器不可用、繁忙、超长或超时都会回退当前有效政策的BM25。一次dense调用的HTTP和SQL合计预算5秒。API的`retrieval.used/reason`及非空工单证据的`retrieval_fallback`记录回退；不可用发布不会借回退绕过治理。

混合检索对无关问题也可能返回4片段；得分不是置信度，不可以“有检索结果”直接退款或回答。demo拒答/转人工、引用校验和确定性退款规则继续执行。

## 复现与维护

默认的README启动方式不下载模型、不创建向量表。启用混合检索前需下载固定文件并校验，建议权重目录位于仓库外；脚本只依赖Python标准库：

```powershell
python scripts/download_embeddings.py --directory D:/AgentModels/ResolveFlow/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3 --report validation/model-download-local.json
# 首次数百MB下载；目录和报告路径可调整，权重不提交到Git。
```

在已有`.env`中保留数据库密码和角色码，补充以下非密钥配置；路径按本机修改。已有数据库更换镜像前先确认PG主版本一致、暂停接单/Worker并备份，不能直接拿PG17镜像启动其他主版本的数据目录。

```dotenv
MODE=demo
POSTGRES_IMAGE=pgvector/pgvector:0.8.6-pg17-trixie@sha256:724a4041afdb1750446e3f6b5cfa8f3b0ac5a2cf538ddfa6bfee4f94c2fa85c6
COMPOSE_PROFILES=semantic
RF_EMBEDDING_MODEL_DIR=D:/AgentModels/ResolveFlow/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3
RETRIEVAL_MODE=hybrid
SEMANTIC_WEIGHT=0.25
```

```powershell
docker compose build
docker compose up -d --wait --wait-timeout 180
docker compose exec -T resolveflow python scripts/build_vector_index.py --report /tmp/vector-build-001.json
```

新库由API初始化政策发布，随后运行显式向量构建；构建前hybrid回退BM25。报告文件须不存在。维护窗口内等待批次完成，再恢复业务访问。API知识库查询的`retrieval.used=hybrid`说明本次确实走混合检索，仅容器healthy不足以证明索引有效。

发布新的政策内容后重新执行向量构建，使用新的报告名。索引缺失时继续安全回退；不会在请求路径中自动下载模型或偷偷编码全库。治理状态变化实时过滤；全部政策可用才允许重新构建完整批次。撤销/过期不会自动删除历史批次；以后做数据运维时再设计清理策略。

回退方式：将`.env`的`RETRIEVAL_MODE=bm25`，重新创建API和Worker，然后停止embedding服务；可移除`COMPOSE_PROFILES`以禁用自动启动编码器。保留当前PG17+pgvector镜像、命名卷及向量表，不执行`down -v`。数据已经包含vector类型时不要直接换回不带扩展的PostgreSQL镜像。

## 隔离验收与冻结质量评测

2.12起推荐使用可指定全新项目名的 [stage2_qa.py](scripts/stage2_qa.py)，完整命令和依赖见 [阶段报告](validation/step-2.12-2026-09-22/REPORT.md)。下列2.8命令保留为历史说明，不应复用已有业务数据的旧QA项目。

`compose.qa-semantic.yaml`使用公开测试凭据、8016端口和独立卷；勿用于正式数据。`scripts/semantic_qa.py`固定操作`resolveflow-qa-step28final`，首次需在全新项目初始化向量；故障复验保留原记录，不清库。旧QA项目停机保留卷。

```powershell
docker build -t resolveflow:step28 .
docker build -t resolveflow:embedding-step28 -f embedding/Dockerfile .
docker compose -p resolveflow-qa-step28final -f compose.qa-semantic.yaml up -d --wait --wait-timeout 180
docker compose -p resolveflow-qa-step28final -f compose.qa-semantic.yaml exec -T resolveflow python scripts/build_vector_index.py --report /tmp/vector-build-001.json
python scripts/semantic_qa.py --report validation/semantic-recheck.json
```

已有成功业务数据时脚本会拒绝复用，需另建明确隔离的项目并同步脚本项目名；不要删除历史卷。不要同时在该数据库上运行PG集成测试和容器重建故障验收。

质量脚本`scripts/evaluate_semantic.py`顺序为`tuning`→冻结selection→`held-out`→`performance`，均要求新的报告路径、同一有效发布、真实本地编码器及数据库。模型、分词、原文、标签、划分和候选权重不可依据保留集结果修改。脚本在源指纹变化时拒绝复用旧selection。本步保留集只评分一次，集成修复后未重复保留评测；最终源码与评测源码的差别在报告及`final-checks.json`中逐项说明。

80条数据仍为模型生成、人工待复核；保留集共享政策语料与标注来源，不能称为外部盲测。demo并未证明生成回答或逐句语义支持性的改善，2.11已单独完成真实生成测量，见 [报告](validation/step-2.11-2026-09-22/REPORT.md)；语义正确性仍未获人工验收。
