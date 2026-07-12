# 15 部署、自动备份、完整性检查与整体验收

## 目标

将完整第一版以安全、可恢复的 Compose 形态部署，并用账务完整性命令和端到端验收收口。

## 范围

- 多阶段 Dockerfile、固定镜像版本、Gunicorn、Caddy、四服务 Compose 和 secrets。
- 仅 Caddy 暴露 80/443；backend 内网；Web 只读、非 root、tmpfs、日志轮换。
- 加密 `pg_dump --format=custom`、每日/每周轮换、部署前/恢复前备份和验证。
- `check_financial_integrity` 管理命令覆盖设计 14 项检查。
- 清理会话/临时文件、升级/回滚脚本、生产安全配置和部署文档。
- 全部需求验收、核心集成测试和一次真实恢复演练。

## 不包含内容

- 云供应商代购、Kubernetes、高可用、对象存储、Docker Socket、Redis/Celery。

## 涉及模块

`core` 管理命令、`scripts`、`config.settings.production`、根目录部署文件及全模块集成测试。

## 主要数据模型或接口

- `BackupRun` 按设计第 40 节。
- `python manage.py check_financial_integrity`。
- `/health/live`、`/health/ready` 仅供容器检查，不经 Caddy 公网路由。

## 实施步骤

1. 构建非 root 镜像、生产设置、静态文件和 Gunicorn 启动。
2. 配置 Caddy 安全头、本地静态资源、内部网络、secrets 和只读/tmpfs。
3. 实现备份、加密、轮换、验证及调度；失败非零退出并安全记录。
4. 实现完整性命令与故意损坏 fixture 测试。
5. 编写首次部署、升级、回滚和灾难恢复 runbook。
6. 执行全套单元/集成/安全/备份恢复/Compose 验收。

## 测试要求

- `ruff check .`、全量 `pytest`、Django check 与 deploy check。
- `docker compose config`、容器健康、重启恢复、仅 80/443 暴露。
- 账本、信用卡、分期、退款、导入幂等、风险、业务恢复高优先级集成测试。
- 运维备份存在、解密、SHA-256、`pg_restore --list` 及临时库真实恢复。
- 恶意 ZIP、敏感日志、Cookie、CSRF/CSP 和未登录访问安全测试。

## 完成标准

- `docker compose up -d` 后服务健康，HTTPS 环境完成需求 15 章验收。
- 服务器重启自动恢复；数据库和 8000 不暴露公网。
- 至少一次业务备份恢复和一次运维数据库恢复有可复核记录。
- 完整性命令对正常库成功、对每类构造异常能失败并说明类别但不泄露财务数据。

## 提交建议

建议紧密三提交：容器/安全配置；运维备份与完整性命令；端到端验收与 runbook。
