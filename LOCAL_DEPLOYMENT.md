# 本地演示部署与联合备份

5.8本地交付核对完成：400基础/166PG/27前端及31项保护检查通过。最新演示入口http://127.0.0.1:8057，见[运维交接](docs/deployment/HANDOVER.md)及[5.8报告](validation/step-5.8-2026-10-05/REPORT.md)。远程完成须有validation/github-actions-local.json的当前HEAD成功CI与附件核验回执；回执不存在或SHA不匹配即未完成。AC-07外部沙箱和4.4继续暂缓，5.7仅单日会话验收，非七天。

以下为历史步骤/使用说明，当前环境以第五阶段交接为准。

5.5本地支付契约准备完成：32项契约测试、15项独立无网络演练通过。用户确认无服务商沙箱账号，外部接入/验收暂缓，AC-07未通过；未接入现有业务退款链。见[沙箱准备手册](PAYMENT_SANDBOX.md)与[报告](validation/step-5.5-2026-10-05/REPORT.md)。本轮Docker未运行，旧入口健康未重新核验；仅本地提交，5.8统一推送/CI。

5.4可选合成订单接口已交付，启用方式、管理员导入命令、状态/审批保护、备份和回退边界见[订单同步手册](ORDER_SYNC.md)。本机新入口为http://127.0.0.1:8054，原8053部署保留；安装/恢复新环境请串行执行。

2026-10-03用户确认：第五阶段改为**0元新增支出的本地可交付演示版**。不申请云资源、不部署公网，不依赖域名、付费模型或真实退款。公开HTTPS、异地备份和整机离线的外部监控暂缓。新版部署使用个人账号、两个独立业务库、Worker和只读监控；既有主环境仍为step36，不自动升级或搬迁。

本机已创建并启动`resolveflow-accounts-local`（8053端口），不要对它重复执行下面的首次初始化；直接访问入口，停止/恢复按后面的命令。首次部署示例用于全新安装，已有同名目录时应选新名字。

## 安装和启动

需要Docker（Linux容器）和Python 3.12。在仓库根目录执行，首次镜像构建需要网络下载依赖，成功后默认demo/BM25运行无需模型网络调用。项目名和端口必须未使用；不要并行生成多套配置后同时启动（子网分配未跨进程预留）。测试机器和结果见[5.3报告](validation/step-5.3-2026-10-03/REPORT.md)，启用容量保护的新部署及本机实测见[容量手册](CAPACITY.md)与[5.6报告](validation/step-5.6-2026-10-05/REPORT.md)，旧部署未自动升级。

```powershell
docker build -t resolveflow:local-demo .
python scripts/local_stack.py --project resolveflow-accounts-local --image resolveflow:local-demo --port 8053
$rfWork = 'work/resolveflow-accounts-local'
$rfCompose = @('compose', '--env-file', "$rfWork/empty.env", '-p', 'resolveflow-accounts-local', '-f', "$rfWork/compose.json")
docker @rfCompose run --rm --no-deps -T identity-init
docker @rfCompose up -d --wait --wait-timeout 180
```

打开 `http://127.0.0.1:8053`。维护员名称为 `maintainer`，初始随机密码只在本机 `work/resolveflow-accounts-local/bootstrap.txt`，用本地编辑器读取，不粘贴到聊天或日志。创建业务账号后退出，以业务账号登录；角色和审计说明见[ACCOUNTS.md](ACCOUNTS.md)。镜像本身不包含生成的凭据，生成器不读取或覆盖 `.env`。

只有gateway发布到127.0.0.1；PostgreSQL、API、Worker和monitor均无宿主端口。HTTP仅用于本机回环连接，不把它改为0.0.0.0，不用临时隧道当作公网部署。展示时使用本机浏览器、屏幕共享或已有截图文档。当前没有新建公开静态网站，也没有本地TLS证书。

## 凭据和持久化

每个服务只挂载自己的只读secret文件；账号入口有工作区认证密钥，没有业务数据库连接；Worker没有账号服务密钥；monitor只有本工作区的只读数据库连接。Compose文件及Docker的`Config.Env`只存文件位置，启动包装器把秘密注入子进程环境。宿主管理员、Docker控制权及容器进程环境仍属于可信边界，这不是硬件密钥库。

`work/<项目>`是私有目录：POSIX为0700，Windows用`icacls`移除继承并仅授予当前用户完全控制。容器需要读secret文件，因此文件为0644，依靠父目录保护宿主访问；Compose本地secret不是加密存储。Windows可用`icacls work/resolveflow-accounts-local`核对ACL，复制到其他磁盘后须重新限制权限。不要把work、身份库、备份、bootstrap、会话或密码放进Git。

身份SQLite、两个PostgreSQL及Worker数据分别使用命名卷。停止和恢复：

```powershell
docker @rfCompose stop
docker @rfCompose start
docker @rfCompose ps
```

**只在第一次运行identity-init。** 不删除卷，不执行带`-v`的down，不用全局prune。容器重建可使用`up -d --no-deps --force-recreate --wait gateway alpha-api beta-api alpha-worker beta-worker alpha-monitor beta-monitor`；保留卷并在操作后检查健康。数据库和整个Docker重启的既有证据见5.2，本步另验证账号/业务运行容器重建及联合恢复。

## 联合备份

在空闲维护窗口执行，两个数据库必须运行。工具在私有目录建立互斥锁，暂停此前正在运行的非数据库服务；SQLite使用backup API导出而非直接复制WAL中的主文件；两个业务库保存完整PG归档、结构/每表指纹和checkpoint元数据。无论导出成功还是失败，都会尝试恢复此前运行的服务；只有完整导出并成功发出启动命令后才写manifest完成标记。启动命令成功不等于所有服务健康，操作者仍需检查`ps`和`/health`。服务恢复失败时先处理Docker状态，不能把残缺目录当成功备份。

```powershell
$rfStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
python scripts/local_backup.py create --work $rfWork --bundle "$rfWork/backups/$rfStamp"
python scripts/local_backup.py verify --bundle "$rfWork/backups/$rfStamp"
docker @rfCompose ps
```

备份包含身份账号密码摘要、权限审计、业务库、审批、退款和checkpoint；不复制旧数据库密码或服务密钥。它**未经加密**，只在私有目录保存；禁止上传到公开存储。SHA256校验只能检测意外损坏，不能认证来源，只恢复自己保管的可信备份。互斥锁因宿主强制退出而残留时先确认无维护进程，核对服务和备份状态后人工处理该锁；不要盲目解锁并同时运行维护任务。

运行期间每天及重要演示/升级前各执行一次，至少保留最近7份完整成功备份；工具保留全部备份，**没有自动清理**。本步不安装后台定时任务，不删除旧备份或历史卷。5.7已按用户授权完成单日流程及事故点RPO24h/RTO4h演练，见[报告](validation/step-5.7-2026-10-05/REPORT.md)；七天实际执行与跨日保留未验证。

可将完整目录复制到另一受保护目录后用`verify`验证；同盘副本只防误操作，不防磁盘损坏。本步实际从这种独立目录副本恢复，但不声称异地灾备。有现成移动硬盘/另一台电脑时再保存受保护副本；没有则明确保留设备故障风险，不要求为本项目购买设备。恢复还需要备份manifest指定的**原应用镜像ID**；勿清理该镜像，跨主机时用`docker image save`保存/导入相应镜像（镜像包不含卷），当前跨主机搬迁未验收。

## 独立恢复和升级回退

恢复必须选择全新项目名、新端口和新卷；工具拒绝已有配置、容器、网络或卷。不覆盖来源环境，不自动运行旧升级脚本，也不自动重播demo初始化。

```powershell
python scripts/local_backup.py restore --bundle "$rfWork/backups/$rfStamp" --project resolveflow-accounts-recovered --port 8054
```

工具使用原镜像，在新集群创建不同的数据库密码和服务认证密钥，恢复两个业务库并验证所有表、结构、索引、序列和可读checkpoint；恢复身份库并核对账号/审计指纹后撤销所有旧会话，然后启动服务。报告在新项目私有目录`restore-result.json`。**恢复保留原账号密码，新生成的bootstrap.txt不是已恢复维护员的密码。** 登录时使用原账号密码；遗失密码按账号手册的受控维护流程处理。

恢复包含尚未执行的队列时，新Worker会继续执行，因此来源和恢复环境均仅限demo合成数据；实际切换演示入口前先停来源Worker，不在两套环境并行接同一业务。未来真实支付接入需要另行设计切换互斥与外部幂等，当前工具不作为正式资金恢复方案。

升级前先保留旧镜像ID、完整联合备份与配置。在独立恢复项目验证新镜像和已审核迁移，再决定切换；本步没有新的业务schema迁移或身份schema升级。失败优先停止新环境，从备份再恢复另一全新项目，验证后使用旧镜像和原数据继续演示。未知版本、不可逆迁移按[MIGRATION_RECOVERY.md](MIGRATION_RECOVERY.md)处理，禁止把该工具用作原卷就地回滚；5.7已完成独立副本的入口切换/快照回退计时，未认证跨版本schema降级。

## 本地监控

每个工作区的monitor用只读连接观察Worker离线、任务执行过久、排队过久及连续失败，输出脱敏JSON状态变化；业务管理员也可在监控页查看。独立宿主进程检查入口（默认10秒一次，单次超时3秒）：

```powershell
python scripts/local_probe.py --url http://127.0.0.1:8053/health
docker @rfCompose logs --tail 100 alpha-monitor beta-monitor
```

探测器只接受本机HTTP地址，不跟随重定向、不读取系统HTTP代理、不发外部通知。`--once`返回0表示入口正常，2表示不可用。入口健康不代表所有工作区健康，要结合两套monitor/容器健康。它能发现入口容器停止；若整台电脑关机或Docker与观察进程一起停止，就无法产生外部事件。整机离线的外部监控明确暂缓；不把本地事件叫作邮件/短信告警。观察进程默认不注册开机启动，终端关闭后不再持续监控。

## 复验

```powershell
python scripts/local_deployment_qa.py --project resolveflow-accounts-localqa --image resolveflow:local-demo --report validation/localqa.json --port 8055
```

使用全新项目和报告路径，测试结束停止并保留卷。覆盖秘密不进入Compose/容器静态环境/日志、持久化、联合备份、导出失败恢复服务、损坏拒绝、独立恢复、旧会话撤销、原审批resume、退款幂等与本地故障事件。报告为脱敏证据；私有备份不纳入仓库。容量由5.6通过；5.5外部沙箱暂缓；5.7按用户授权完成单日试运行与演练，七天未验证。
