# 个人账号、权限审计与工作区隔离

个人账号版支持可选的[合成订单同步](ORDER_SYNC.md)、[容量保护](CAPACITY.md)和联合备份；新环境安装与恢复应串行执行。

个人身份入口为 `identity_gateway.py`，工作区业务接口为 `operations.py`。本地部署与联合备份见 [LOCAL_DEPLOYMENT.md](LOCAL_DEPLOYMENT.md)，容量与限流见 [CAPACITY.md](CAPACITY.md)。

## 隔离方式

统一登录入口管理个人账号，每个业务账号固定属于一个工作区。首版提供 alpha、beta 两套合成工作区，使用**不同 PostgreSQL 实例、密码、数据卷、API、Worker 和私有网络**。工作区是完整业务部署的边界：订单、政策、发布版本、任务、审批、退款台账和 LangGraph checkpoint 均在该区的数据库内。不同工作区可以使用相同订单编号，退款唯一性分别由各自数据库保证。

入口只有路由和账号凭据，没有业务数据库 DSN；路由由服务器保存的账号归属决定，不能由浏览器提供上游 URL。业务 API 每次请求还会向账号服务校验会话，使用仅属于自身工作区的服务密钥验证归属，禁止共享角色码绕过。Worker 没有账号管理凭据，只连接自身数据库；MCP 子进程继承该区只读 DSN，不能跨网络连接另一工作区的 PostgreSQL。

`scripts/accounts_stack.py` 生成受控的全新两工作区部署配置。入口独占接入网络，仅向宿主 `127.0.0.1` 发布一个端口；API 与入口连接该区 front 私网，API、数据库与 Worker 连接该区 data 私网。数据库和 Worker 不发布端口，不加入另一工作区的网络。入口为可信路由组件，拥有两个工作区 API 的网络可达性；宿主/Docker 管理员仍是可信维护边界，隔离不抵抗宿主权限失陷。

这种方案避免改写既有业务主键、外键和 checkpoint 结构，历史数据不需要批量重编号或重新播种。代价是两个工作区需要两组数据库和 Worker；是否能在零元资源上持续运行尚未验证，须在 5.3 实测，不能据本机验收宣称公网方案已落实。

## 人员与权限

|身份|工作区|允许操作|不允许操作|
|---|---|---|---|
|运营 operator|一个固定工作区|看订单/政策/工单、提交工单|审批、核查结案、重试、监控、账号管理|
|审批 reviewer|一个固定工作区|看订单/政策/工单、审批、核查结案|提交工单、重试、监控、账号管理|
|业务管理员 admin|一个固定工作区|上述业务操作、监控、失败重试、本区权限审计|其他工作区业务、平台账号管理、政策写入/发布、数据库迁移|
|账号维护员 manager|无业务工作区|受控创建/停用/启用账号、变更业务角色、重置密码、全局权限审计|业务订单/政策/工单/监控、把自己改为业务管理员|

维护员也是个人账号，计入**全局最多 5 个有效人员账号**，因此示例可同时启用维护员与 4 个业务账号。停用账号保留历史身份和审计但不占有效名额。创建与重新启用在 SQLite `BEGIN IMMEDIATE` 事务中串行检查上限，不能用并发请求绕过。账号名唯一，不回收历史名称；账号名为 3–40 位小写字母、数字或 `_.-`，以字母开头。

工作区归属创建后不可通过网页/API修改。需要调整时停用原账号、由维护员创建新的受邀身份，保留原操作归属。维护员具有授予他人业务权限的身份管理权，因此凭据仍须受控；“没有直接业务读取权限”不等于其是零信任的无权第三方。没有开放注册、SSO、邮件邀请发送或政策维护授权。

## 密码、会话与审计

- 密码 12–128 字符，随机独立盐，scrypt（N=16384、r=8、p=1）；数据库仅存密码摘要。账号连续失败 8 次后暂锁 15 分钟，未知账号返回相同登录提示。完整公网请求限流与抗滥用仍待 5.6。
- 会话为 256 位随机不透明令牌，数据库仅存 SHA-256 摘要，8 小时绝对过期，每账号最多保留 8 个活动会话。令牌仅存浏览器页面内存，不进入 URL、Cookie、localStorage 或 sessionStorage；刷新后重新登录。
- 退出撤销当前会话；停用、改角色、重置密码撤销该账号全部会话，重新启用也不恢复旧令牌。后端每次重新校验，不缓存角色。已获授权并提交的任务/审批继续完成，不因账号停用撤销既有业务事实；停用不是取消已在执行的任务。
- 账号服务不可用时，业务 API 返回 503，不降级接受共享口令。个人模式必须显式配置，缺失配置启动失败。既有本地 `compose.yaml` 保留 legacy 兼容路径，不能作为公网个人账号入口使用。
- 新业务记录的 `created_by`、审批、核查和 `rf_audit.actor` 保存 `account:<不可变UUID>`；后台续跑仍沿用原工单和审批。历史 `operator`/`reviewer` 等标记原样保留，不臆造操作者姓名。账号列表可将 UUID 对应到用户名。
- 权限审计保存登录成功/失败、退出、账号变更、工作区拒绝、业务访问及其状态；请求开始先记录 intent，响应后补状态。若只有 102 开始事件，没有结果事件，应先查询业务记录，不盲目重放写入。业务事务内另保留原 `rf_audit`。
- 审计不存密码、会话、工单正文、查询文本或审批理由；业务审计中的理由仍按既有业务数据规则保存。账号维护员查看全局权限审计，业务管理员仅能查看本区；每页最多 100 条，以 `before` 向前翻页。没有自动删除审计的任务；30 天运行保留目标仍需 5.7 试运行记录证明。

## 全新本地隔离部署

5.3之后优先使用[本地部署手册](LOCAL_DEPLOYMENT.md)的`local_stack.py`：凭据按服务挂载，包含双工作区monitor和联合备份恢复。以下保留5.2兼容生成器的历史命令，其compose文件含凭据。

前提：Docker 可用，在仓库根目录执行；Python 3.12 可用。以下命令不读取 `.env`，不连接主项目。项目名必须全新，生成器拒绝覆盖 `work` 下已有目录。不要并行生成多套网络配置后同时启动，以免未保留的空闲子网被其他验收占用。

```powershell
docker build -t resolveflow:accounts-demo .
python scripts/accounts_stack.py --project resolveflow-accounts-demo --image resolveflow:accounts-demo --port 8052
docker compose --env-file work/resolveflow-accounts-demo/empty.env -p resolveflow-accounts-demo -f work/resolveflow-accounts-demo/compose.json run --rm --no-deps -T identity-init
docker compose --env-file work/resolveflow-accounts-demo/empty.env -p resolveflow-accounts-demo -f work/resolveflow-accounts-demo/compose.json up -d --wait --wait-timeout 180
```

打开 `http://127.0.0.1:8052`，初始维护员名称 `maintainer`，随机密码在本机受控文件 `work/resolveflow-accounts-demo/bootstrap.txt`。用本地编辑器读取，不粘贴到聊天/日志/仓库；生成的 `compose.json` 同样含部署凭据，必须留在忽略目录。Linux 文件以 0600/目录 0700 创建；Windows 还须确认该目录 NTFS ACL 只允许受信人员读取，5.3 部署时使用宿主密钥管理方式。生成器不买资源、不发布公网、不发邀请。

维护员登录后创建账号并选择工作区和角色，再退出，以新账号登录体验业务。修改账号角色、停用或密码后，旧客户端下一次请求会失效并回到登录页。身份与业务审计有独立面板。个人密码不可通过 API 查询；验证错误响应也不回显被拒绝的密码。

后续重启只使用 `up`，**不要再次运行 `identity-init`**：初始化只接受不存在的身份数据库，拒绝覆盖。SQLite 身份数据库版本固定为 1，账号服务不自动建库/升级，不接受未知版本。身份卷只挂载入口和显式维护任务，业务 API/Worker 不挂载。

维护员遗失密码时，在停入口或确认维护窗口后使用同一受控配置运行：

```powershell
docker compose --env-file work/resolveflow-accounts-demo/empty.env -p resolveflow-accounts-demo -f work/resolveflow-accounts-demo/compose.json run --rm --no-deps identity-init python identity.py recover-manager
```

该配置中的 `RF_BOOTSTRAP_PASSWORD` 会作为恢复密码；如要更换，应先用受控本地编辑器更新该维护任务的环境值，或移除此字段后在交互式终端输入。该命令撤销维护员全部旧会话并记录恢复事件，不删除账号或业务数据。不能通过重建空身份库替代密码恢复。

停止而保留全部数据：

```powershell
docker compose --env-file work/resolveflow-accounts-demo/empty.env -p resolveflow-accounts-demo -f work/resolveflow-accounts-demo/compose.json stop
```

禁止 `down -v`，禁止把测试配置的连接串指向主库。不要把原库以同一套账号密码同时充当多个工作区，也不要将其他工作区网络接入 Worker。

## 历史数据及后续运维

5.2 没有新增业务 Alembic revision，也没有自动迁移主环境。原业务库、审批、台账和 checkpoint 保持原结构。若未来切换既有环境，应先按既有备份/独立恢复流程验证，将整套数据库和 Worker 指定为一个工作区，保留所有原 ID；不能直接将不同工作区业务合并到一套表，也不能在旧库运行 demo 初始化。主环境切换未在本步执行。

账号库是新增的独立持久资产。原 PostgreSQL 备份工具**不包含**身份 SQLite；需要一致性 SQLite backup API 导出及受控密钥/路由配置副本，禁止运行中仅复制主 `.sqlite3` 而遗漏 WAL。5.3新增[联合备份与独立恢复](LOCAL_DEPLOYMENT.md)，核对账号、审计、两业务库并撤销旧会话；5.7仍需实际试运行、保留策略和回退演练。5.2的重建持久化证据本身不等于跨环境恢复。也不能直接对这套多数据库服务调用只支持既有服务名的备份工具。

## 验证入口

```powershell
python -m pytest test_identity.py -q
node --test test_frontend.cjs test_identity_frontend.cjs
python scripts/accounts_qa.py --image resolveflow:accounts-demo --project resolveflow-accounts-qa01 --report validation/accounts-qa01.json --port 8052
```

真实验收使用新项目和新报告文件，成功/失败都停止服务并保留卷；不得复用旧 QA 数据使结果看似通过。测试覆盖账户上限竞争、撤销、角色、工作区、后台 MCP、网络、审批恢复和退款幂等。自动化已纳入 CI，发布结果见[验证成果](docs/RESULTS.md)。

账号首次失败与最终证据见 [5.2 报告](validation/step-5.2-2026-10-03/REPORT.md)。100 工单/日与 3 执行槽另见[容量验收](CAPACITY.md)，单日恢复见[演练手册](TRIAL.md)。外部支付、公网和长期可靠性不属于本地版交付范围。
