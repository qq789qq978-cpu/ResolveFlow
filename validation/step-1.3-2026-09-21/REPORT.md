# 步骤 1.3 — 管理员浏览器验收

> 以下为对应日期和配置的验证记录；当前版本成果见[验证汇总](../../docs/RESULTS.md)。

日期：2026-09-21。结论：**通过**。

## 环境与验收方法


- 复用 `resolveflow-qa-step11` 独立Compose项目、数据库与命名卷，访问8004。真实 `.env` 未修改。
- 管理员登录、创建样例、查看监控/失败工单、点击重试、双页面重复重试、运营/审批角色控件对照及重新登录，均通过真实浏览器操作完成。
- API权限检查是浏览器验收的补充：直接请求被隐藏控件对应的接口，验证后端也拒绝越权。成功的管理员重试由浏览器完成，没有用脚本代替。

## 失败样例边界

工单 `6cd74078-2752-43ae-b4e1-f2bbb5e203ef` 由管理员页面创建，关联合成订单RF-1002。准备期间正常停止独立QA Worker，以免它提前消费样例。辅助脚本调用实际 `jobs.process_one`，将调查执行器替换为固定抛出TimeoutError的夹具，零等待完成三次失败，得到 `retrying → retrying → failed`；第四次调用不再消费失败任务。

没有直接更新数据库状态，没有修改运行中应用文件。错误正文中的测试标记不写入工单/任务记录，只保留异常类型。该方法验证失败耗尽与管理员重试流程，不代表真实模型超时、Worker强杀、数据库断网或已有checkpoint中断恢复已经验收。样例在首次调查前失败；正常Worker恢复后才生成其checkpoint。

准备样例时曾提前停止仍在等待健康检查的QA Worker，导致那一次Compose启动命令返回退出错误；随后显式管理QA Worker的停止/启动，并在业务验收结束核对全部服务健康。没有清理卷，也没有影响8003主环境。

## 浏览器结果

|检查|实际结果|
|---|---|
|管理员登录|显示管理员角色、创建工单和运行监控|
|运行监控|展示在线Worker、执行/失败尝试、平均/P95耗时、当前失败工单数；与API/数据库一致|
|失败筛选和详情|运行异常列表出现新工单，显示“调查失败”、TimeoutError、已完成尝试3次和“重试任务”按钮|
|运营角色对照|可以查看失败详情；看不到运行监控和重试按钮|
|审批角色对照|可以查看失败详情；看不到运行监控、重试按钮或创建工单面板|
|管理员重试|点击后工单重新入队，离开运行异常列表，尝试计数重置为0|
|双页面重复重试|第二个旧页面仍尝试提交时被拒绝，提示“只能重试已耗尽自动重试次数的任务”，HTTP409|
|正常Worker恢复|重新启动QA Worker，完成调查，最终为“自动拒绝退款”，说明为两项退款条件均不满足|
|完成后的控制|失败列表为空，重试按钮不再显示；直接再次请求重试返回409|
|重新登录|刷新回登录页，重新授权后原工单完成状态与监控仍可查看|

监控变化：基线8次执行/0次失败/0个失败工单；准备后11次执行/3次失败/1个失败工单；恢复后12次执行/3次失败/0个失败工单，在线Worker为1，平均566ms、P95约1110ms。历史失败尝试保留是预期行为。单工单任务详情的attempts表示本轮重试尝试次数，恢复后为1，并不包含重置前的3次失败。

## 后端权限与数据核对

重试前12项、完成后13项HTTP检查通过，共25项：

|请求|未登录/无效授权码|运营|审批|管理员|
|---|---|---|---|---|
|运行监控|401|403|403|200|
|失败工单重试|401|403|403|浏览器202；重复/完成后409|

补充验证审批角色创建工单返回403；运营角色提交审批、人工核查返回403。各轮被拒请求前后工单完整详情不变。

数据库核对：新工单由admin创建，最终 `auto_rejected`；任务 `done`，错误已清空。尝试历史恰为3次TimeoutError失败和1次成功；审计恰为admin/create及admin/retry。共7条demo工单、2条审批、1条核查、2条模拟退款，与1.2相比仅新增1条工单，没有退款/审批/核查增量；原退款内容核对一致。全部工单模型调用和token为0。

## 证据

- [浏览器场景记录](browser-evidence.json)
- [失败样例生成结果](failure-fixture.json)
- [重试前HTTP权限检查](permissions-before.json)、[重试后HTTP权限检查](permissions-after.json)
- [数据库只读核对](database-evidence.json)、[重试HTTP访问日志](retry-http.txt)
- [初始页面截图](01-monitor-baseline.jpg)、[失败详情整页](02-failed-admin.jpg)
- [恢复结果整页](03-retry-completed.jpg)、[失败列表清空](04-failed-list-cleared.jpg)

## 复验

先用 `scripts/operator-qa.ps1 -Action Start` 启动独立QA并等待完成。正常停止QA Worker后，通过管理员浏览器新增一条以“步骤1.3”开头、关联RF-1002的工单，记录其新UUID。

```powershell
# 在项目目录运行；仅作用于公开demo夹具环境，不使用主环境容器。
docker stop resolveflow-qa-step11-worker-1
$qaRunId = '替换为新工单UUID'
Get-Content -Raw -Encoding utf8 scripts/seed_admin_qa_failure.py | docker exec -i resolveflow-qa-step11-resolveflow-1 python - $qaRunId
.\.venv\Scripts\python.exe scripts/verify_admin_qa_permissions.py before $qaRunId --report validation/recheck-1.3-before.json
# 在浏览器中查看失败工单并点击“重试任务”，然后恢复正常Worker。
docker start resolveflow-qa-step11-worker-1
# 等待浏览器显示最终结果后运行：
.\.venv\Scripts\python.exe scripts/verify_admin_qa_permissions.py after $qaRunId --report validation/recheck-1.3-after.json
```

辅助脚本拒绝非demo配置、非本步骤工单或已经处理的工单。复验须使用新工单，不能将本次已完成工单强改为失败。

## 收尾与后续

验收期间主环境8003保持运行。结束时主环境三服务healthy，独立QA容器停止、卷和工单保留，临时浏览器角色退出并关闭测试标签。辅助脚本通过语法检查与本次实际运行，未改业务代码，因此没有重跑不相关的完整业务测试集或远程CI。

本步未发现阻断缺陷。1.2已记录的底部提示滞后仍存在：即使工单完成，提示可能仍为“已重新入队，将从检查点恢复”；以详情状态和监控为准。
