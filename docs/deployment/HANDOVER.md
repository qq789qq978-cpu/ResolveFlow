# 本地部署运维手册

适用于个人账号、双工作区、合成订单与容量保护的本地演示版。安装命令见[快速启动](../../README.md#快速启动)，完整原理见[联合备份手册](../../LOCAL_DEPLOYMENT.md)。交付基线 `66b2aa4` 已通过完整 CI 与附件核验，见[验证成果](../RESULTS.md)。

## 环境与账号

下例使用新部署 `resolveflow-showcase`、入口 <http://127.0.0.1:8058>。对已有部署，将项目名、端口和私有目录替换为其实际配置。

- Gateway 管理个人账号、会话、审计、日额度与限流，只发布本机回环端口。
- alpha／beta 各有独立 PostgreSQL、API、合成订单源和只读 monitor；Worker 分配为 2／1。
- 首次维护员为 `maintainer`，随机密码保存在 `work/<项目>/bootstrap.txt`；维护员仅管理账号。已有恢复环境继续使用原账号密码。
- 最多 5 个有效账号含维护员；运营、审批和管理员的业务访问受工作区限制。账号创建、停用和会话撤销见[账号手册](../../ACCOUNTS.md)。

`work/` 包含私有配置、秘密和备份，不提交 Git。不要清空原审批、checkpoint 或退款台账来重演业务，使用新工单或独立合成环境。

## 启停与健康检查

启动 Docker 的 Linux 引擎，在仓库根目录设置目标项目：

```powershell
$rfWork = 'work/resolveflow-showcase'
$rfCompose = @('compose', '--env-file', "$rfWork/empty.env", '-p', 'resolveflow-showcase', '-f', "$rfWork/compose.json")
python scripts/local_backup.py resume --work $rfWork
docker @rfCompose ps
```

`resume` 按现有容器 ID 分层启动并等待健康，不执行初始化或迁移。启用 `--orders --capacity` 的部署有 12 个长期服务。`identity-init` 仅在首次安装执行，不能用于重启已有账号环境。

```powershell
docker @rfCompose stop
```

停止保留容器与命名卷。不执行 `down -v` 或全局清理。版本核对同时记录 Git SHA、镜像 ID 和实际配置；历史测试结果不能替代实时健康检查。

## 联合备份与独立恢复

重要演示或维护前，创建身份 SQLite＋双 PostgreSQL 的联合备份。该操作短暂停止写入，结束后恢复此前运行的服务。

```powershell
$rfBundle = "$rfWork/backups/manual-" + (Get-Date -Format 'yyyyMMddTHHmmss')
python scripts/local_backup.py create --work $rfWork --bundle $rfBundle
python scripts/local_backup.py verify --bundle $rfBundle
docker @rfCompose ps
```

保留完整备份目录和清单指定的应用镜像。备份含账号摘要与业务数据，应在受保护目录保存；SHA-256 检测意外损坏，不认证不可信来源。

恢复到全新项目、空闲端口和新卷：

```powershell
python scripts/local_backup.py restore --bundle $rfBundle --project resolveflow-showcase-recovered --port 8059
```

工具核对身份、结构、数据和 checkpoint，撤销旧会话。重新登录后核对原工单、审批、退款台账与告警；切换入口前停止来源 Worker，保留源环境以便回退。不得将两套环境并行接入同一真实业务。

单日演练的事故点备份年龄为 312.4 秒、恢复 89.6 秒、回退 95.8 秒，见[报告](../../validation/step-5.7-2026-10-05/REPORT.md)。验证的是同镜像快照恢复与入口回退，不包括跨版本 schema 降级或异地灾备。

## 故障处理

|现象|检查与处理|
|---|---|
|入口不可达|检查 Docker、目标 Compose 项目和 Gateway 健康，使用 resume 恢复|
|Worker 离线／队列增长|查看管理员告警与关联 run_id，核对 alpha 两个／beta 一个 Worker 及数据库健康|
|超时／连续失败|查看关联日志和任务状态；排除原因后由管理员重试，保留台账与 checkpoint|
|429／日额度满|等待限流窗口或香港自然日切换；额度为部署共享，审批恢复不占新工单额度|
|提交 503／结果未知|先查工单列表和关联日志，未知结果保守占用额度，避免反复提交|
|订单同步异常|检查工作区、订单版本、状态和源接口，使用[同步手册](../../ORDER_SYNC.md)|

详细期限与告警见 [RUNTIME_LIMITS.md](../../RUNTIME_LIMITS.md)、[OBSERVABILITY.md](../../OBSERVABILITY.md)。故障注入使用独立 demo 环境，避免影响已有数据。

## 验收与发布

[需求矩阵](REQUIREMENTS.md)记录本地版范围，[验收索引](../../VALIDATION.md)保留各阶段证据。外部支付沙箱、公网部署、长期在线 SLA 和异地备份不属于本地交付；单日会话验收不代表全天持续在线。

推送后运行 `python scripts/verify_delivery.py`，核对当前 HEAD、远程 main、CI 与实际附件。成功回执位于被忽略的 `validation/github-actions-local.json`。公开报告引用固定验证基线，后续变更须核验新的提交。
