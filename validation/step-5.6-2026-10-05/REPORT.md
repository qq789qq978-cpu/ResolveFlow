# 5.6 容量、限流、隔离与安全验收

2026-10-05，本地完成，不推送；基线d5f5354。本报告不代替5.7七天试运行或5.8同SHA CI。AC-07服务商沙箱继续按用户授权暂缓。

## 结果

|项目|样本/结果|门槛|
|---|---|---|
|提交入队|100条，p95 0.562s，最大0.906s|p95≤2s|
|已登录列表/详情|100条、5并发读取，p95 0.328s，最大0.375s|p95≤1s|
|空闲执行名额下自动流程|30条，p95 12.157s|p95≤30s|
|日额度|两工作区合计100条接收，第101条两边均429；重复新工单计数|不超额、不静默丢单|
|执行|实测3槽占用且至少7条排队；所有接收工单可查询并完成|alpha2+beta1；等待审批不占槽|
|执行尝试|101次，失败0、超时0|含满额时审批恢复，不删除失败样本|

明细与全部HTTP样本：[capacity-complete.json](capacity-complete.json)。首轮[capacity-a.json](capacity-a.json)读取p95=1.485s未通过，其他已执行指标未因此抹除；定位认证每请求新建httpx客户端，改为API生命周期内复用连接，仍逐次验证会话/工作区。重新建立全新卷、同样数据规模与5并发读取验收通过，未放宽阈值。中途cap56b按用户要求暂停于18条额度，见[暂停记录](PAUSED.json)；最终使用新目标cap56c，分段进度只供恢复，不作为完成报告。

## 范围与机器

主机Intel i7-10870H，8核16线程、约16GiB内存；Docker Desktop Linux为16逻辑CPU、7.66GiB内存、Docker 29.7.2。详细[cpu.json](cpu.json)、[memory.json](memory.json)。测试时原step36继续运行，其他压力/故障QA停止后才启动最终容量负载。

每工作区1004订单、1000历史工单、107知识片段，实际数量以报告fixtures为准。历史工单为合成容量夹具（含待审批/已退款状态），不是1000次真实Agent执行；另有100条真实新工单与原审批恢复。额外100知识片段与原政策一起发布，规则绑定保持，实际Worker使用该检索负载。仅demo/BM25，模型API调用0，真实退款0。

每个库默认100连接，API池8，每Worker主池4+心跳1，每执行任务池4+checkpoint1+MCP1+清理2；alpha保守预算约40、beta约25。采样连接峰值alpha=18、beta=11，采样不是绝对瞬时上界。首次执行排队p95=28.192s、最大41.924s；包含突发、等待Worker启动和按限流节奏发送的整体吞吐约9.69条/分钟，不能据此声称满载极限。见[metrics-summary.json](metrics-summary.json)，队列等待按事务领取时间减创建时间计算，审批恢复不混入首次排队指标。

## 回归与证据

|验证|结果|证据|
|---|---|---|
|完整基础/契约/限流及HTTP认证|377通过，另35契约子用例|[unit.xml](regression-final/unit.xml)|
|真实PostgreSQL/槽竞争/期限/权限|166通过|[postgres.xml](regression-final/postgres.xml)|
|前端既有交互回归|27通过；本轮无新增浏览器截图验收|[frontend.txt](frontend.txt)|
|双工作区账号、安全和网络隔离|33通过|[accounts.json](accounts.json)|
|合成订单、事实变化、原审批和联合恢复|24通过|[orders.json](orders.json)|
|实际非MCP SQL超时、长事务、整体任务期限及恢复|23通过|[runtime-verified.json](runtime-verified.json)|
|默认未启用容量配置的本地部署兼容|27通过|[local-compat.json](local-compat.json)|
|容量/额度/恢复验收|19通过|[capacity-complete.json](capacity-complete.json)|

专项57项属于基础回归的重叠子集，不额外累计。Windows初次pytest分别遇到跨盘收集路径和临时目录权限错误，原失败证据[focused.xml](focused.xml)、[focused-cwd.xml](focused-cwd.xml)保留（focused-cwd.xml仅移除行尾空白，原/新哈希见[evidence-formatting.json](evidence-formatting.json)）；最终使用隔离Linux容器，不能把Windows失败称为业务通过。首轮回归regression-a为修复期间参考，最终证据使用regression-final与最终镜像。期限故障测试两次未观察到预期SQL延迟，第二次已无其他QA负载，不能仅归因于竞争；任务在进入退款SQL前就被六秒期限终止且清理（[首次报告](runtime.json)、[单独复测](runtime-final.json)、[Worker事件](runtime-first-worker.jsonl)）。修正冷启动夹具：可配置注入期限，默认12秒，仍短于25秒SQL延迟与30秒语句期限；在新目标重新实测整体期限先终止任务、清理及恢复，以runtime-verified为最终证据。生产默认180秒未变，没有抹除两次观察失败。

安全验证包括运营越权审批/重试、维护员业务拒绝、跨工作区工单/订单/知识/MCP/checkpoint/退款拒绝，网络隔离、停用/角色变更/退出会话撤销，以及身份服务故障关闭。容量专项包含并发额度竞争、香港午夜、登记重放、来源头伪造、登录/提交短期计数持久化、直接后端绕过拒绝。槽竞争额外连接不会领取排队任务，关闭连接释放槽；没有把互联网渗透审计或任意分区无限扩容算作已验证。

## 交付与保留

新干净入口 http://127.0.0.1:8056 ，12服务健康，维护员密码仅在忽略目录work/resolveflow-accounts-capacity/bootstrap.txt，初始额度0、联合私有备份完成。见[delivery.json](delivery.json)与[容量手册](../../CAPACITY.md)。QA结束全部停止、卷保留，未将已用满100额度的测试数据作为日常演示入口。

67个镜像运行文件与源码一致；跨暂停前后原主环境20表、结构、容器ID/镜像/卷一致；本轮重新启动Docker导致启动时间更新，本轮main-resume-before至结束启动时间一致；8053/8054维持停止，不为核验启动它们，也不声称本轮重新读取了它们的数据库内容。.env、原延迟SQL空格文件、engine/storage/迁移源码哈希保持。见[verification.json](verification.json)、[main-before.json](main-before.json)、[恢复基线](main-resume-before.json)、[main-after.json](main-after.json)。

## 有效边界与下一步

100/日由共享身份库原子登记，业务库提交前登记；跨库不做分布式事务，未知结果保守占用额度，不自动返还。固定三个单任务Worker，槽固定alpha2/beta1，不跨库借用；连接强杀会释放锁，不能宣传任意额外Worker/网络分区下仍有无限扩容的严格全局并发保证。短期限流为固定窗口，不是公网DDoS防护。

旧环境未自动升级，新功能通过--capacity显式启用；旧身份库缺容量表时拒绝启动而非自动迁移。外部支付沙箱、真实订单/资金、公网/HTTPS、异地备份、整机离线外部监控、人工RAG语义认证均未补记通过。5.7连续7天、每日备份、RPO/RTO尚未开始；收到指令后再做。4.4仍暂缓，5.8统一推送及同SHA CI，本步没有新增远程CI认证。
