# 真实模型评测运行说明

2.11协议见 [LIVE_MODEL_EVALUATION.md](LIVE_MODEL_EVALUATION.md)，已完成运行的结果以 [验收报告](validation/step-2.11-2026-09-22/REPORT.md) 为准。本说明用于另行安排复验，不由CI自动执行；每个新批次会产生费用并增加保留集验收次数。

## 环境与输入

评测依赖项目Python 3.12锁定环境、隔离PostgreSQL及已启动的本地E5编码服务。使用主环境同版本的应用镜像只提供依赖，挂载当前代码的无密钥副本；不得挂载整个项目.env、主数据库卷或主数据库连接。主环境保持demo。

本次使用应用镜像`resolveflow:step210`、隔离项目`resolveflow-qa-step211`，只启动`compose.qa-semantic.yaml`中的db。评测容器连接该项目private网络、既有`resolveflow_embedding-private`编码服务网络，以及单独的出站网络。编码服务仅访问本机固定模型，DeepSeek是唯一生成模型服务。历史QA卷保留，新的复验项目应使用新名字；不要删除历史卷。

QA数据库连接固定为公开合成凭据`postgresql://resolveflow:qa-step28-database@db:5432/resolveflow`。评测器对这个地址做严格检查；勿将此公开密码用于业务环境。初始化先执行`Store(DATABASE_URL).setup()`，再执行`semantic.build_index(store)`；需得到原3份演示政策、7个片段/向量、generation=1。不得改成已人工审核来使live放行。

准备一份只含`OPENAI_API_KEY`、`OPENAI_BASE_URL=https://api.deepseek.com`、`MODEL_NAME=deepseek-flash`的私有env文件，置于已被Git忽略的`work/`内。正文、报告中均不写密钥。用Docker的`--env-file`读取，不把Key拼在命令行中。代码副本应从Git归档生成；本次执行前使用等价的文件白名单副本，未包含.env。

## 执行契约

容器环境另设置`MODE=demo`、`RETRIEVAL_MODE=hybrid`、`SEMANTIC_WEIGHT=0.25`、`EMBEDDING_URL=http://embedding:8080`和上述QA数据库连接；MODE在此仅允许检索原演示政策，评测代码明确调用live模型。将代码只读挂载到`/qa-src`，私有输出目录可写挂载到`/private`，新报告目录挂载到`/reports`。工作目录及PYTHONPATH均为`/qa-src`。

在该容器执行：

```text
python scripts/evaluate_live_rag.py --live --private-dir /private --report /reports/live-evaluation.json
```

私有目录必须已存在；报告路径必须不存在。模型、提示词、规则、语料、数据划分、检索参数等需符合冻结协议。本批次首次推理前的输入指纹保存在私有manifest，并写入公开结果。复验前应先运行以下无网络、无密钥测试：

```text
python -m pytest test_live_eval_budget.py test_live_rag_evaluation.py -q
```

前后对主环境19表做只读行数/哈希检查，确认4个容器健康且镜像、启动时间、配置一致。本次记录见`runtime-before.json`和`runtime-after.json`。评测结束停止本步QA数据库，保留命名卷及本地私有审计记录。

## 中断与费用核查

模型客户端关闭自动重试。每次请求先写预算预留，成功后以供应商返回token用量结算保守估算；不能把此数当成供应商账单。请求失败或用量缺失保留预留并停止。上限10元、368次请求；不自动充值或扩大范围。

完整样例独立落盘。若无计费不确定且输入指纹不变，可在新报告路径恢复已完整保存的样例；已收费但未保存完整样例、pending或uncertain记录会拒绝自动重付，应先人工核对供应商记录。不要删除私有账本来绕过保护。HTTP失败/超时不意味供应商没有扣费。

公开报告包含调优逐题输出、保留集汇总及12条合成业务样例；私有目录中的保留逐题结果不用于调参，也不提交Git。程序执行完整不等于质量合格，逐字摘录率不等于整段回答准确率。
