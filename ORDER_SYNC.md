# 合成订单接口与同步

5.4仅连接本地合成源，不是真实店铺、支付服务商或真实资金接口。默认demo/BM25、零新增付费调用。同步由当前工作区的业务管理员显式发起，不会自动扫描或覆盖旧订单。账号维护员、运营和审批员均无导入权限。

## 新部署与调用

沿用[本地部署手册](LOCAL_DEPLOYMENT.md)，为全新环境增加`--orders`：

```powershell
docker build -t resolveflow:orders-demo .
python scripts/local_stack.py --project resolveflow-accounts-orders --image resolveflow:orders-demo --port 8054 --orders
$rfOrdersWork = 'work/resolveflow-accounts-orders'
$rfOrdersCompose = @('compose', '--env-file', "$rfOrdersWork/empty.env", '-p', 'resolveflow-accounts-orders', '-f', "$rfOrdersWork/compose.json")
docker @rfOrdersCompose run --rm --no-deps -T identity-init
docker @rfOrdersCompose up -d --wait --wait-timeout 180
```

首次维护员`maintainer`的密码仅在新项目bootstrap.txt中，使用本机编辑器读取。通过网页创建本工作区的业务管理员账号；5个有效账号的限制继续包含维护员。旧项目同名目录存在时生成器会拒绝覆盖，不要删除旧卷来绕过。

导入示例（密码在终端隐藏输入，不通过命令参数、聊天或日志传递）：

```powershell
python scripts/order_sync_client.py --url http://127.0.0.1:8054 --username admina --order-id RF-2001
python scripts/order_sync_client.py --url http://127.0.0.1:8054 --username admina --history
```

客户端登录后调用接口并退出该临时会话。导入后在网页刷新订单列表，即可选择订单发起工单。默认合成源包含RF-2001（可自动退款）、RF-2002（自动拒绝）、RF-2003（运输中转人工）、RF-2004（等待人工审批）。旧RF-1001等种子订单仍保留，不能通过同步接口接管。

提供`POST /api/order-sync`，请求体仅`{"order_id":"RF-2001"}`，成功返回outcome、event_id、order_id、version；`GET /api/order-sync`返回本工作区最近50条同步事件及个人操作身份。地址、工作区、凭据与数据库连接都由部署配置决定，调用者不能在请求里选择URL、工作区或直接提交订单字段。未知请求字段拒绝。结果未知时先查询历史，再按相同订单重试；源版本可能已经前进，因此应核对返回的版本及事件ID。

每个工作区有独立的私有HTTP合成源，只读挂载`alpha-orders.json`/`beta-orders.json`，没有宿主端口。源接口`GET /orders/RF-xxxx`使用独立共享密钥；源没有业务数据库密码。管理员客户端不能直接修改源，维护者在受控文件中更新合成夹具后，管理员再发起同步。正常源不提供HTTP写入或故障注入接口。

## 来源契约与异常

完整快照包含event_id(UUID)、workspace、source=`synthetic-v1`、synthetic=`true`、version(正整数)、id(`RF-`加4位数字)、owner=`demo`、currency=`CNY`、amount(整数分，1–100000000)、days(整数0–36500)、used(布尔)、status(shipping/delivered/cancelled)。缺字段、多余字段、错误类型、非合成来源、外币或真实客户标识拒绝；布尔不能冒充整数，字符串不会自动转换。天数是合成源提供的观察值，不会在本地随日期自动递增；需要维护者发布新版本，不能据此声称真实物流时效。

|情况|处理|
|---|---|
|同event_id、同内容重试|duplicate，不重复写订单或事件|
|同event_id但内容改变|409 event_id_conflict，整笔拒绝|
|新的event_id、相同version及内容|unchanged，记录一次事件|
|相同version但内容不同|409 version_conflict|
|版本比当前旧|ignored_stale，记录事件但不回退事实|
|新版本与合法状态变化|applied，订单、版本和审计事件在同一事务提交|
|修改原金额/归属，已签收退回运输中，取消后重开，used真改假，天数倒退|409，拒绝危险回退；需要演示纠错时创建新合成订单|
|源归属或订单ID不匹配|403 source_scope_mismatch，不写其他工作区|
|旧订单存在但无同步版本记录|409 unmanaged_order_conflict，不能接管历史种子/业务订单|
|超时、断连、源异常、重定向、超大响应|504/502；不写入任何快照，已提交状态可查历史|
|源没有该订单|404，不删除本地已有订单|

HTTP连接超时1秒、读超时2秒、读取期间总预算5秒、最多64KiB。总预算检查在读取块之间执行，慢速分块还受每次读超时约束；不承诺任意DNS/网络情况的硬实时期限。固定源不跟随重定向、不读取环境代理；SQL锁等待最多1秒，单条SQL最多3秒。接口故障不会输出连接串、源密钥或原始供应商错误；错误状态可在个人权限审计中追踪，拒绝的源完整正文不保存。

事件ID和订单ID分别使用事务级锁，重复/并发/乱序由数据库事务和唯一键约束。没有后台自动同步、自动无限重试、分页增量游标或批量导入；本步提供的是可复现的单订单完整快照同步。真实店铺适配、分页认证和真实物流时间模型属于后续条件扩展。

## 权限、审批与幂等

数据库新增独立`rf_sync`角色：只可读写订单事实及同步版本，追加事件；不能写退款、审批、政策或checkpoint，不能建表。普通`rf_app`和Worker对订单表仍只读，Worker也没有同步DSN或源密钥。身份网关固定路由到账号所属工作区；两个工作区使用物理独立PG、独立源密钥和私有网络。

执行模拟退款前，事务会重新验证政策，再获取与订单同步相同的订单事务锁，读取当前订单并与调查时的id/owner/金额/签收天数/使用状态/订单状态比较。事实变化时转人工核查，不沿用旧审批执行退款。原审批和checkpoint保留，人工核查后如需再次申请，应对当前事实创建新工单；不改写旧审批决定。

此互斥约定适用于本项目同步入口和退款入口；拥有数据库维护权限的直接SQL不属于受控同步接口，应按维护窗口操作。订单同步不修改已有退款台账，重复退款仍由订单唯一台账拦截；已经提交的退款不会因为随后取消订单而自动撤销。

## 迁移、备份与回退

新功能是可选Alembic分支`orders@head`/`rf_orders_0001`，依赖冻结core基线，新增`rf_order_versions`和`rf_order_events`两表，不改变既有订单列。普通core/hybrid部署默认不启用；安装后可与vector分支同时存在。未知结构、缺少版本或不完整的同步表拒绝就绪；运行时不自动迁移。

新项目生成器显式执行`db_migrate.py prepare --orders`，创建新的受限导入凭据并授权。既有库若要升级，必须先保留完整联合备份，在独立恢复环境验证迁移和新权限，再安排维护窗口；不能只给旧容器打开环境变量、也不能盲目重跑历史升级脚本。本步不就地升级原step36主环境或原8053环境。

联合备份已扩展：启用同步时额外保存合成源快照，业务PG归档包含新版本/事件表。独立恢复重新生成导入密码及源密钥，恢复原身份/业务/事件/源快照，撤销旧会话；同源事件在恢复后重试仍是duplicate，原等待审批工单可继续原checkpoint。恢复工具拒绝覆盖现有卷，仍要求保留备份对应原镜像ID。

不要通过删除同步表回退；该分支downgrade明确拒绝丢弃同步审计。需要回退时停新环境，从升级前可信备份恢复到全新项目并核对，再用旧镜像继续演示；5.7仍需整套回退切换计时。本步新功能使用demo，不适用于真实资金或商家数据。

复验入口：`python scripts/order_sync_qa.py --project resolveflow-accounts-orderqa --image resolveflow:orders-demo --report validation/orderqa.json --port 8057`。必须新项目、新报告；结束停止并保留卷。只在该隔离QA配置开启`RF_SYNTHETIC_QA=1`以注入源延迟，正常部署不开启。结果见[5.4报告](validation/step-5.4-2026-10-03/REPORT.md)。

本次已创建独立交付项目`resolveflow-accounts-orders`，入口http://127.0.0.1:8054，使用`resolveflow:step54-orders`。11服务健康，初始备份在忽略的私有目录`work/backups/step54-initial`；未预先创建业务账号/导入订单，先用维护员创建本工作区管理员再按上文导入。已有8053和step36均保留。

同一Docker宿主上的新项目生成、首次启动和独立恢复应串行进行；当前子网选择不具备跨进程预留锁，同时创建可能选择同一未使用网段。本次并行启动碰到此情况，保留失败环境，随后使用全新项目串行复验通过。请勿删旧卷绕过。
