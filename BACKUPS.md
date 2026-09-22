# 数据库备份手册（3.4）

备份入口是 `scripts/database_backup.py`。宿主只需 Python 3.9+、Git、Docker；复用正在运行的 Compose API 内的 psycopg 和数据库容器的 PostgreSQL 工具，不需要宿主安装 PostgreSQL，也不读取或打印宿主 `.env`。需要 Docker 操作权限、正常运行的单个 API/Worker/DB，API 与 Worker 镜像必须相同。

## 创建与校验

在项目根目录执行（保持现有服务运行）：

```powershell
python scripts/database_backup.py create
```

默认写入 `work/backups/<UTC时间>-<随机ID>/`。成功退出0，输出备份目录、文件大小、SHA256；失败退出1。每次创建新目录，绝不覆盖、自动清理或删除历史备份。当前支持本项目标准Compose布局：数据库用户resolveflow、public应用schema、无额外用户schema。自定义库布局或多个Worker应先扩展并验收工具，不能直接套用。

备份包含三份文件：

|文件|内容|
|---|---|
|database.dump|完整单数据库的PostgreSQL custom归档，含业务、RAG、向量、checkpoint、版本表和序列|
|manifest.json|开始/完成UTC时间、快照观察时间、表结构/行数/内容指纹、迁移版本、扩展版本、PG与客户端版本、Python依赖版本、运行镜像ID、源码摘要|
|SHA256SUMS.json|归档和manifest的SHA256，检测文件损坏或意外修改|

按实际输出路径校验，例如：

```powershell
python scripts/database_backup.py verify work/backups/<实际备份目录名>
```

`verify`离线校验文件清单、大小、SHA256、归档标记和manifest状态，不访问数据库。示例中的尖括号必须替换。它不会重新解码归档，也不执行恢复。文件缺失、多出文件、内容被修改或`.partial`目录均拒绝；SHA256清单不是签名，不能证明恶意替换后的来源真实性。

可指定新备份根目录、Compose项目及每个外部操作的超时（10–3600秒，默认600秒）：

```powershell
python scripts/database_backup.py create --output D:/ResolveFlowBackups --project resolveflow --timeout 600
```

这是同步命令，不会创建定时任务。先保证足够磁盘空间；完整表排序、哈希和备份会增加数据库负载。本步按本机小型演示库验收，未做大数据量性能评估。

## 一致性与版本记录

1. API内建立只读REPEATABLE READ事务，先取得现有迁移入口使用的schema级互斥锁，阻止迁移/显式播种同时执行；业务可以继续写入。备份取得所有应用表的ACCESS SHARE锁，持有到dump完成。
2. 导出PostgreSQL快照，表指纹与`pg_dump --snapshot`读取同一事务快照，包括动态心跳表。指纹不是在dump之后另读一次在线库。
3. 指纹格式为`jsonb-text-utf8-C-sort-u64be-length-sha256-v1`：每行转PostgreSQL JSONB文本，以C排序、UTF-8编码，每行先写8字节大端长度再写内容，然后SHA256。行数和指纹可供3.5在匹配的PG主版本上恢复核对。不同于历史验收的Python JSON算法，不能跨算法直接比较哈希。
4. 保存应用`rf_schema_version`和LangGraph的完整迁移序列、扩展及依赖版本。API/Worker的不可变image ID表示实际运行版本；宿主`source_checkout.base_commit/dirty`只描述工具所在工作区，不能冒充容器代码提交。另记运行代码及备份工具源码SHA256。
5. 使用数据库容器的同主版本pg_dump/pg_restore，完整解码所有归档块到容器的`/dev/null`；不会把SQL执行到任何数据库。归档和清单写盘、校验成功后，才将`.partial`目录改名发布。

**序列不受MVCC快照约束。** manifest记录的是观察到的`last_value/is_called`，在线写入时可能与dump里的序列值不同；恢复不能强制按该观察值回退序列。3.5需要检查恢复后序列与业务主键的安全关系。快照时间是事务内观察时间，不承诺精确到该毫秒的所有提交边界；本工具不是时间点恢复（PITR）。

## 失败处理

失败只保留唯一`.partial`目录和安全错误记录`failure.json`，包括阶段、时间和错误类型，不将外部命令错误/SQL/连接凭据写入公开输出。再次运行生成另一份新备份；不要手工改名把失败目录变成成功目录。

磁盘空间不足、导出失败、解码损坏、客户端主版本不匹配、备份期间容器重建/重启、API/Worker镜像不一致、结构漂移或迁移正忙，均拒绝发布。进程被强制结束可能只留下不带failure.json的`.partial`，同样不能用于恢复。连接关闭或超时后PG释放快照和锁；发生网络异常时可能需要等待服务端超时，不应在锁仍存在时强行迁移。

## 保存范围与恢复边界

- `work/`和`backups/`已忽略，`*.dump`禁止默认纳入Git或镜像构建；备份内含业务信息，应放在仅本人/维护者可读的目录。Unix新目录权限0700；Windows继承父目录ACL，本工具不自动修改Windows权限。
- 本机同一磁盘的一份备份不能抵抗整盘损坏。另存受保护的独立磁盘/异地副本后再运行verify；本步未配置加密、异地同步、定时计划或保留周期。
- 单数据库归档不包含集群账号/密码、其他数据库、Docker镜像、`.env`、外部embedding模型文件或WAL。账号权限在3.6另行管理，镜像和模型需按版本另行保存。
- 恢复到新库时要预先准备匹配的PG主版本、扩展以及运行镜像，使用`pg_restore --no-owner --no-privileges --exit-on-error`，不要复用原环境所有权/授权，也不要覆盖原库。custom格式归档的所有权处理以恢复参数为准。
- **完整解码与文件校验不等于恢复验收。** manifest固定记录`restore_verified=false`；独立库恢复、数据/序列核对、待审批工单继续执行属于下一步3.5，本步没有提前执行。

本步报告：[3.4验收](validation/step-3.4-2026-09-22/REPORT.md)。迁移事故处理见 [MIGRATION_RECOVERY.md](MIGRATION_RECOVERY.md)。
