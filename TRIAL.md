# 5.7 单日试运行与恢复演练

2026-10-05用户明确将“连续七天试运行”调整为“单日试运行＋恢复演练”。本步已按新范围完成，见[报告](validation/step-5.7-2026-10-05/REPORT.md)及[授权记录](validation/step-5.7-2026-10-05/scope-revision.json)。仅证明当天会话内运行与演练，不是24小时连续运行、七天可靠性或七份跨日保留。

## 环境与查看

入口 http://127.0.0.1:8057 ，项目`resolveflow-accounts-trial57`。原8056和主环境保留，历史8053/8054继续停止。沿用固定5.6镜像、三Worker、demo/BM25及合成订单，无外部模型调用或真实退款。

维护员密码仅在忽略目录`work/resolveflow-accounts-trial57/bootstrap.txt`；验收账号密码在同目录`trial-accounts.json`。不要提交或贴到聊天。新配置明确alpha监控期望2个Worker，beta期望1个；旧8056未自动升级。

在仓库目录查看固定验收结果：

```powershell
.venv\Scripts\python.exe scripts/local_trial.py status --work work/resolveflow-accounts-trial57
```

完成后的tick不再自动创建新业务或备份。七天自动任务`resolveflow-5-7`已暂停；宿主采样进程通过私有`observer-stop`标记停止。容器内monitor继续运行，不发送外部通知。无需继续开机积累七天。

## 开机后使用和备份

启动Docker后，可恢复已有容器；先数据库再应用/Worker，不执行初始化或迁移：

```powershell
.venv\Scripts\python.exe scripts/local_trial.py resume --work work/resolveflow-accounts-trial57
```

已有联合备份在`work/resolveflow-accounts-trial57/backups/`，受保护独立副本在`work/resolveflow-trial-backup-copies/trial57/`。两者同在D盘，不是异地或整盘损坏保护。全部保留，无自动删除。公开Git只存脱敏回执，不包含数据库备份。

重要演示或升级前可按[本地手册](LOCAL_DEPLOYMENT.md)另做联合备份，选择未使用的目录。长期使用仍建议每日备份、保留至少7份；该长期建议未在本次单日验收中验证。

## 演练范围

已从最新独立副本恢复到全新项目/新卷，记录8058恢复候选→8059原快照回退的客户端入口切换。验证身份与双业务库指纹、旧会话撤销、原审批/checkpoint恢复、再次审批与退款台账幂等。需审批订单即使曾退款，新请求仍先审批，不能绕过。

RPO用声明演练事故时间减备份开始时间保守计算，目标≤24小时，本次约312秒。备份后合成标记在恢复快照中缺失、在源库保留，明确展示允许损失窗口。恢复RTO约90秒，回退RTO约96秒，包含业务核验，均≤4小时。只认证该次演练，不认证连续多天备份频率。

只在派生副本上实际停入口和单个alpha Worker；队列年龄、连续失败告警使用标注清楚的合成夹具。派生容器结束后停止、卷保留，源工单不变。没有原卷就地恢复、跨版本schema降级或真实资金操作。

如以后另行要求演练，可使用下列命令；它创建新派生项目，8058/8059必须空闲，不修改原验收报告：

```powershell
.venv\Scripts\python.exe scripts/trial_drill.py --work work/resolveflow-accounts-trial57
```

旧七天闸门默认保留，单日通过需有显式范围授权。不能改日期或重复当天伪装成七天；将来若需要长期运行，应另建记录并明确新范围。

5.7当时仅本地提交；现纳入[5.8交付](docs/deployment/HANDOVER.md)，远程完成按当前SHA CI与附件回执判定。AC-07外部沙箱、4.4继续暂缓。
