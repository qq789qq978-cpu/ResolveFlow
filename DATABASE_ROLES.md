# 数据库角色隔离（3.6）

Compose 已将数据库连接分成迁移、业务和只读三类。它们与网页的运营/审批员/管理员授权码是两套不同的权限。当前本机已经部署，实测见 [3.6报告](validation/step-3.6-2026-09-22/REPORT.md)。

## 权限与连接

|数据库角色|使用者|权限|
|---|---|---|
|`resolveflow`|DB初始化、一次性`db-roles`|集群管理员；安装扩展、创建角色及接管对象所有权|
|`rf_migrator`|一次性`migrate`、显式维护命令|拥有应用schema、22张表及3条序列；迁移、政策发布/审核/回滚、向量构建|
|`rf_app`|API、Worker、LangGraph checkpoint|读全部应用表，仅写下述允许的业务表；不能DDL、改政策、订单、向量、迁移版本|
|`rf_readonly`|MCP调查查询|SELECT表和序列，默认只读事务；关闭只读设置后仍由ACL拒绝写入|

三个受管角色均没有superuser、CREATEDB、CREATEROLE、REPLICATION、BYPASSRLS或角色成员关系；不能通过SET ROLE提升身份。仅有数据库CONNECT权限，没有CREATE/TEMP。

`rf_app`写权限逐表授权：`rf_runs/rf_jobs`可INSERT/UPDATE；审批、退款、复核、审计、任务尝试表仅INSERT；心跳与三张checkpoint数据表可INSERT/UPDATE/DELETE。只有审计/任务尝试序列可nextval，不允许setval、TRUNCATE或删除退款/审计。`checkpoint_migrations`仅可读。

迁移账号将来创建的表/序列默认只给业务与只读账号SELECT，新业务写入须显式更新`db_roles.WRITES`或序列允许清单，并经测试；新函数默认不向PUBLIC开放EXECUTE。重复provision会重新收紧表级和列级授权，但拒绝接管无项目标记的同名角色或异常角色成员关系。

## 新装与已有库升级

先按README创建`.env`并设置管理员数据库密码及网页角色码，再运行：

```powershell
python scripts/setup_db_credentials.py
docker compose up --build -d --wait --wait-timeout 180
docker compose ps -a
```

脚本只为缺失的`RF_MIGRATOR_PASSWORD`、`RF_APP_PASSWORD`、`RF_READONLY_PASSWORD`追加随机48位十六进制密码，不输出密码、不替换已有值。三者必须互不相同，不能复用管理员密码，限定24–128位URL-safe字符。存在但为空或不合法的值需在本地修正。不要提交`.env`。

启动顺序：DB healthy → db-roles成功退出 → migrate成功退出 → API healthy → Worker。两个一次性任务Exited (0)正常。管理员凭据只进入DB/provision服务，迁移凭据只进入migrate；API/Worker只持有业务与只读连接。容器停止后Docker元数据仍可包含环境变量，Docker主机管理权限仍须信任；这不是密钥托管系统。

已有库先备份、停止接单并确认任务空闲，再停API/Worker：

```powershell
python scripts/database_backup.py create
python scripts/setup_db_credentials.py
docker compose build
docker compose stop resolveflow worker
docker compose run --rm --no-deps db-roles
docker compose run --rm --no-deps migrate python db_migrate.py prepare
docker compose up -d --wait --wait-timeout 180
```

无版本旧库用`adopt`替代`prepare`，按[MIGRATIONS.md](MIGRATIONS.md)核对基线。provision事务包含权限与所有权变更，使用相同迁移互斥锁、锁等待5秒；不新建业务表、不清空数据、不播种。此次不新增Alembic revision，hybrid版本仍为`rf_vector_0001`。重跑不会重置业务内容。恢复旧应用也必须使用已限制的业务连接或经过明确维护恢复方案，不能仅换回镜像就假定账号契约兼容。

## 政策锁与维护

退款事务需要对政策表持有SHARE锁；PostgreSQL直接LOCK需要额外写权限。因此使用无参数、固定SQL的`rf_lock_policy_refund()`，由迁移账号拥有、SECURITY DEFINER、固定`search_path=pg_catalog`，只锁完全限定的政策表。仅业务账号可执行，不接受表名/SQL输入；不会赋予业务账号政策写权限。真实并发测试证明退款持锁时政策修改等待，事务结束后才继续。旧未配置角色的测试/历史库保留原LOCK兼容路径。

查看政策状态可继续使用业务连接；发布、回滚、审核、原始导入和向量构建必须通过维护连接。示例：

```powershell
docker compose exec -T resolveflow python policy_releases.py status
docker compose run --rm --no-deps migrate python db_migrate.py check
# hybrid需要embedding已启动。输出报告保留到宿主work目录。
docker compose run --rm --no-deps -v "${PWD}/work:/reports" migrate python scripts/build_vector_index.py --report /reports/vector-build-001.json
```

政策发布与候选文件只读挂载见[POLICY_RELEASES.md](POLICY_RELEASES.md)。迁移容器已连接embedding内部网络。不要把管理员或迁移凭据临时塞进普通API/Worker来执行维护。

## 本机Python、备份与恢复

Compose自行组装容器连接，不使用宿主`.env`里的`DATABASE_URL`。便携PG尚未自动升级，容器升级不改变便携数据库。非Docker运行须在目标库显式用管理员连接执行`db_roles.py provision`，为该进程传入三个角色密码；然后换成迁移连接执行prepare/adopt。`db_roles.py`不自动加载`.env`，维护进程需要明确注入环境变量，避免将管理员连接保存到长期运行配置。

维护后将本地`.env`的`DATABASE_URL`配置为目标库的`rf_app`，`READONLY_DATABASE_URL`配置为同库的`rf_readonly`。`run_local.py`启动前检查业务账号、只读连接配置并启用`RF_ENFORCE_DB_ROLES=1`。MCP在严格模式下缺少只读连接直接拒绝启动，不退回业务连接。便携PG端口/库名根据自己的安装填写，示例默认55432/postgres；本次未对便携PG执行provision或启动验收。

备份使用应用的只读数据权限导出快照，归档不保存集群账号密码/ACL。恢复匹配新版镜像时，工具在全新隔离库恢复后、数据检查前执行同版provision，生成独立随机账号密码：`admin.env`用于配置角色，`maintenance.env`用于维护，`runtime.env`仅保留业务和只读连接。旧镜像不包含角色模块时保留历史恢复契约；报告`database_role_isolation`明确记录分支。不把原环境凭据复制进恢复库，不自动迁移或播种。见[BACKUPS.md](BACKUPS.md)、[RESTORE.md](RESTORE.md)。

## 验证和边界

232项基础检查、120项PG检查通过；新装与旧库接管分别实测44项越权拒绝，覆盖DDL、TEMP、修改政策/订单/迁移版本、TRUNCATE、序列重置和提权。待审批旧checkpoint接管后可继续退款；新版备份再恢复后同意/拒绝、重复审批409及退款幂等通过。破坏性权限探针只在隔离QA执行，主库只核对身份、权限目录、数据和只读API。

这一步限制数据库账号能力，不提供逐用户/逐租户RLS。业务账号仍能修改被允许的业务对象，维护账号仍能改政策和历史数据；应用业务校验、访问码权限、主机权限和运维审计仍有各自作用。连接池、整体任务超时和连接耗尽留到3.7；本步不更改模型、退款规则或真实支付边界。
