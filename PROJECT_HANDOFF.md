# ResolveFlow 维护指南

本地演示版已于 2026-10-06 完成交付，基线 `66b2aa4` 的 [CI](https://github.com/qq789qq978-cpu/ResolveFlow/actions/runs/37424944514) 与验收附件核验成功。公开文档以功能、运行方式与可追溯证据组织；日常使用从 [README](README.md) 和[运维手册](docs/deployment/HANDOVER.md)进入。

## 维护入口

|内容|资料|
|---|---|
|进程、事务和权限设计|[ARCHITECTURE.md](ARCHITECTURE.md)|
|新环境部署、账号与密钥|[LOCAL_DEPLOYMENT.md](LOCAL_DEPLOYMENT.md)、[ACCOUNTS.md](ACCOUNTS.md)|
|业务与身份联合恢复|[运维手册](docs/deployment/HANDOVER.md)|
|执行期限与监控|[RUNTIME_LIMITS.md](RUNTIME_LIMITS.md)、[OBSERVABILITY.md](OBSERVABILITY.md)|
|验证成果与历史报告|[docs/RESULTS.md](docs/RESULTS.md)、[VALIDATION.md](VALIDATION.md)|

## 数据与运行约定

- 在原检出目录维护，先查看 Git 状态，保留已有未提交修改。
- `.env`、私有配置、身份库和备份不入 Git；已有部署使用恢复命令，不重跑首次初始化或旧升级脚本。
- 数据库卷、审批、checkpoint、政策证据和退款台账需保留；变更前备份，恢复到独立项目与新卷核对。
- 故障注入在隔离 demo 环境执行，使用合成数据，不调用真实支付；默认不调用付费模型。
- SQL 延迟测试中，函数名与左括号保留空格，例如 `pg_sleep (5)`。
- 审批及后台权限由服务端核验；恢复与重放不得绕过当前订单事实、政策、授权和退款幂等。

## 变更验证

按变更范围执行相应本地检查，提交后核验同一 SHA 的 CI 和 `evaluation` 附件。发布工具 `scripts/verify_delivery.py` 生成本地回执，校验 HEAD 与远程 main 一致、工作区干净、测试及故障报告通过。旧版本验收是历史基线，不认证新提交。

验收报告按日期保留，包括失败、修复及最终结果；报告中当时的环境和阶段状态不代表当前运行健康。源码版本、Docker 镜像与运行环境分别核对。

## 版本范围

已完成个人账号、双工作区、合成订单同步、日额度、三 Worker 容量和单日恢复演练。外部服务商支付沙箱保留为独立扩展，现有支付实验仅为本地契约模拟。当前部署与证据范围见[需求基线](docs/deployment/REQUIREMENTS.md)。
