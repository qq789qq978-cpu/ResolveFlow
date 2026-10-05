# 5.7 本地七天试运行

当前进度以 [验收记录](validation/step-5.7-2026-10-05/REPORT.md) 和 [机器状态](validation/step-5.7-2026-10-05/status.json) 为准。2026-10-05 开始，至少七个连续香港日期有真实业务流程和备份，且从开始起经过至少六整天，才允许最终演练。最早可于 2026-10-11 开始时刻之后收口；漏跑不能补写旧日期。这里是每天开机运行的本地试运行，不是七天连续在线或生产 SLA。

## 环境与入口

- 试运行项目：`resolveflow-accounts-trial57`，入口 http://127.0.0.1:8057 。
- 原主环境与 8056 演示环境保留，不用来注入故障。8053/8054 历史环境继续停止。
- 沿用固定 `resolveflow:step56-capacity` 镜像，三 Worker、demo/BM25、合成订单、零外部模型调用和真实退款。
- 首次维护员密码在忽略目录 `work/resolveflow-accounts-trial57/bootstrap.txt`，自动验收账号密码在同目录的 `trial-accounts.json`。不要提交、贴到聊天或复制进报告。
- 新容量部署中 alpha 的 API/monitor 明确要求两个 Worker，beta 要求一个。旧 8056 配置没有自动改写；不要把试运行的部分 Worker 离线检测结果外推到旧配置。

## 每日与每小时检查

在仓库目录使用现有 `.venv\Scripts\python.exe`：

```powershell
.venv\Scripts\python.exe scripts/local_trial.py tick --work work/resolveflow-accounts-trial57
.venv\Scripts\python.exe scripts/local_trial.py status --work work/resolveflow-accounts-trial57
```

同一天重复 tick 不重新计天，也不重复当天业务。每天双工作区各创建一张可自动模拟退款的合成订单，验证自动流程、重复请求幂等、跨工作区拒绝、三个 Worker 在线、无活动告警，以及原待审批工单仍可读。任何提交结果未知均停止自动重发，须结合原 run_id/额度审计先核对。失败与不完整运行保留在 ticks/day-progress 中，不悄悄覆盖。

每天成功流程之后执行身份库与两个 PG 的联合备份；其他小时检查发现最近备份已满20小时会额外备份，以留出24小时 RPO余量。创建备份会有短暂维护窗口，服务暂停后恢复。已有备份及副本逐一重新校验，全部保留，不自动删除历史卷或备份。

源备份在 `work/resolveflow-accounts-trial57/backups/`；受保护独立目录副本在 `work/resolveflow-trial-backup-copies/trial57/`。两者目前同在 D 盘，**不算异地或整盘损坏保护**。只有完整成功并带 manifest 的备份计数；七天应至少保留七份，实际相邻备份超过24小时会记录缺口并阻止该窗口验收。

关闭电脑或 Docker 的时段如实保留为空白，自动任务不能在关机时执行。开机并启动 Docker 后，可显式恢复本试运行的已有容器：

```powershell
.venv\Scripts\python.exe scripts/local_trial.py resume --work work/resolveflow-accounts-trial57
```

resume 只启动现有试运行容器，先数据库再应用/Worker，不重跑初始化或迁移，不启动旧环境。其他维护或暂停应先暂停本聊天的自动检查，防止检查期间恢复试运行服务。

独立宿主观察进程每60秒检查试运行入口，在私有 observations 目录留时间戳；tick 导出脱敏汇总。相邻采样超过150秒记为“未观测”，不推断整段必定离线或在线。备份导致的短暂停机可与备份维护时间对照。容器 monitor 持续观察 Worker、队列、失败；仅本机事件，不发送外部通知，不宣称可观察整机断电。

## 恢复与回退演练

```powershell
.venv\Scripts\python.exe scripts/trial_drill.py --work work/resolveflow-accounts-trial57
```

每次从最新独立副本恢复到全新项目/新卷，8058为恢复候选、8059为快照回退目标。端口必须空闲。只在这些副本上停服务和注入告警夹具，不向原试运行库注入故障。

RPO以声明演练故障的时间减备份开始时间保守计算，要求≤24小时；备份之后的合成标记工单在恢复快照中应缺失，而源环境仍保留，用来说明允许损失窗口。RTO从声明恢复/回退开始，到入口可用、原审批/checkpoint恢复、再次审批及重复退款幂等核验完成，要求≤4小时。

演练记录客户端从8058切换到8059，使用同一冻结镜像和原始快照，是部署入口切换及数据快照回退；本步没有新的 schema 或版本升级，不能宣传跨版本不可逆迁移回滚。恢复候选上的新增测试写入不会带回源库。结束停止派生演练容器，保留全部卷。

实际测试入口离线、单个alpha Worker离线和告警恢复。队列延迟通过独立副本中的过期排队时间注入，连续失败告警通过明确标注的三个合成失败记录注入；这些记录不冒充三个真实 Worker 执行失败。原 SQL/整体任务超时真实故障证据仍参见5.6。

满足真实日期和备份闸门后，才能用最新备份执行最终演练：

```powershell
.venv\Scripts\python.exe scripts/trial_drill.py --work work/resolveflow-accounts-trial57 --final
```

--final 在缺少真实七天记录时拒绝执行。最终报告通过才更新 AC-04/10/11及5.7完成状态、停止自动检查并本地提交。5.8统一推送/同SHA CI仍需下一步授权；AC-07外部沙箱、4.4继续暂缓。
