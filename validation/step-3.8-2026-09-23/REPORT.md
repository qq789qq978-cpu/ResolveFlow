# 3.8 关联日志与运行告警验收

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

2026-09-23，从528a702接续，原目录完成本步实现、隔离验收和文档。主环境继续step36，未部署本步镜像。

## 交付行为

- API生成X-Request-ID，使用request_id/run_id连接请求与持久队列事件；Worker每次领取生成独立attempt_id并携带worker_id，任务进程的task_token关联PostgreSQL application_name。JSON事件只保留允许字段，不输出客户文本、查询参数、审批理由、授权码、连接串或异常正文。
- job_finished移到事务提交成功之后，提交/连接失败记录job_interrupted，避免尚未落库就声称完成。事务回滚重试保留连续running起点，避免反复刷新计时掩盖任务长期无进展。
- 新增管理员专属GET /api/alerts和告警视图：Worker数量不足、执行时间过长、可执行队列延迟、近期连续失败、重试耗尽。人工等待和尚未到期的退避任务不算停滞。
- 新增独立monitor常驻服务，使用rf_readonly，每5秒只读检查，即使浏览器关闭也记录firing/resolved事件。相同活动事件不重复触发；读取失败进入monitor_unavailable，并保留原活动告警，不能误报恢复。列表按类别截断时，仅保留无法确认的该类历史事件，Worker等完整类别仍能正常恢复。
- Compose增加日志轮转（API/Worker/monitor各3×10MiB）、监控健康检查及配置；没有数据库迁移、权限扩大、自动重试或退款行为。运行告警参数和操作说明见[手册](../../OBSERVABILITY.md)。

## 验证证据

|范围|结果|证据|
|---|---|---|
|完整基础回归|260通过|[regression-final/unit.xml](regression-final/unit.xml)|
|最终告警基础专项|8通过，其中7项与上项重叠；合计261条不同基础测试|[observability-final/unit.xml](observability-final/unit.xml)|
|完整PostgreSQL回归|147通过|[regression-final/postgres.xml](regression-final/postgres.xml)|
|最终告警PG专项|9通过，其中8项与上项重叠；合计148条不同PG测试|[observability-final/postgres.xml](observability-final/postgres.xml)|
|前端自动化|21通过|[frontend.log](frontend.log)|
|最终镜像真实双Worker与独立monitor|27项全部通过，结束时告警为空|[compose-release2.json](compose-release2.json)|
|主环境及镜像来源|主快照完全一致、受测运行源码一致|[verification.json](verification.json)|

专项覆盖：管理员权限、过期心跳恢复、持队列行锁时仍可只读观测running任务、人工等待/退避排除、连续失败被成功打断及时间窗口过期、真实延迟约束提交失败不输出完成日志、基础设施反复失败不重置计时、快照无业务写入、告警截断和去重边界、请求关联与脱敏。PG和基础测试在全新内部网络/独立数据库执行；数据库卷保留。

真实Compose验收对新隔离项目执行：

1. 两个受限Worker正常运行；管理员可读告警，运营/审批被403拒绝，monitor确认为rf_readonly。
2. 强杀一个Worker，不依赖浏览器触发轮询；心跳过期后独立monitor记录容量不足，启动后记录恢复；重复读取不重复发同一事件。
3. 停两个Worker，合成工单进入可执行队列延迟告警；启动后继续原工单。等待审批的旧时间记录不被误报为停滞。
4. 在该QA库退款插入前注入25秒延迟，两个Worker心跳仍正常；2秒测试阈值发现运行过久，独立monitor也记录该工单。12秒整体期限终止每次任务，三次失败后触发连续失败和任务耗尽告警，审批仍保存、退款表无部分写入。
5. 删除QA阻塞后，运营无权重试，管理员重试恢复原批准checkpoint并完成一次模拟退款。再开同订单例外工单仍需审批，执行返回already_refunded，退款表仍一条。
6. 停QA数据库，API返回503，monitor记录状态未知；恢复数据库后记录恢复、Worker重新在线、最终告警为空。
7. 实际服务日志关联API请求、工单、独立尝试和SQL token；检查整个API/Worker/monitor日志均不含合成凭据、客户文本和审批理由标记。

测试使用2秒运行/排队阈值、10秒心跳时限、1秒监控间隔、预期2个Worker、12秒任务期限；并非生产默认参数。运行过久只表示需要核查，不把所有超过阈值的正常慢任务判为死锁。

最终运行镜像为resolveflow:step38-release，ID记录在compose-release2.json及verification.json。9个运行源文件按统一换行的SHA与镜像逐项匹配；复验未替换resolveflow:local。CI配置已增加基础/PG用例及真实告警脚本；本次尚无远程CI结果。

## 失败和修正记录

- [compose-first.json](compose-first.json)：首次QA脚本误用启动期已经发生的旧worker_offline事件，尚未等到本次强杀心跳过期就作判断，失败即停机。修正为核对新增事件数量及当前在线数后，在新环境通过。
- [compose-final.json](compose-final.json)和[compose-verified.json](compose-verified.json)：中间运行镜像均完成27项；随后补充大列表截断时完整告警类别正常恢复的边界，用最终发布镜像再次验收。
- [compose-release.json](compose-release.json)：与另一套独立PG环境同时分配子网时撞上Docker地址池，项目尚未进入业务测试即失败。保留已建资源，改为顺序分配全新项目release2后通过。脚本首次错误详情保留在忽略目录work/resolveflow-qa-step38-release/last-error.log；复验不要并行分配这些QA子网。
- 首轮[regression-first](regression-first)为260基础/146PG通过；后续新增提交失败及分页测试，最终统计以上表为准。未抹掉早期结果，也未把多轮复跑相加当作不同测试。

## 数据保留与边界

[main-before.json](main-before.json)与[main-after.json](main-after.json)完全一致：20张非心跳数据表、7条向量、22表结构/版本、四个主服务镜像/启动时间/卷映射不变，服务healthy。本步所有QA容器停止，全部历史卷保留；未改.env、未付费调用、未真实退款，原延迟SQL两处空格保留。

告警当前只在管理员页面和本地JSON日志可见，没有邮件/短信/Webhook投递、持久告警确认或跨进程去重。monitor进程/宿主整体故障仍需外部平台观察；未验收大规模负载、网络分区或所有历史故障组合。本步没有实机浏览器截图验收，前端证据是21项自动化行为检查；独立完整验收归3.9。

RAG 标签及引用采用模型辅助核查口径，PDF仍为文本层维护导入，reranker实验未启用；本步不扩大这些能力声明。
