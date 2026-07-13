# 第一版验收记录

本文记录任务 15 在开发工作站完成的自动化验收，以及必须在目标 Linux 服务器补做的运行环境验收。记录不得包含实际金额、密码、密钥、Cookie、账单行或数据库连接串。

## 开发工作站验收

验收日期：2026-07-13。

| 检查项 | 命令或证据 | 结论 |
|---|---|---|
| 代码规范 | `ruff check .` | 通过 |
| 全量自动测试 | `pytest` | 通过；250 项 |
| Django 系统检查 | `python manage.py check` | 通过 |
| 生产安全检查 | `python manage.py check --deploy --settings=config.settings.production` | 通过 |
| 迁移完整性 | `python manage.py makemigrations --check --dry-run --settings=config.settings.test` | 通过 |
| Compose 静态约束 | `tests/test_deployment_configuration.py` | 通过；验证四服务、网络、端口、secrets、只读/tmpfs、健康检查、日志轮换与固定版本 |
| 运维备份逻辑 | `apps/core/tests/test_database_operations.py` | 通过；覆盖流式加解密、篡改拒绝、轮换、校验、失败脱敏与恢复参数 |
| 业务备份恢复 | 任务 14 全量测试 | 通过；覆盖加密业务备份、恢复前备份、恶意文件拒绝与事务回滚 |
| 财务完整性 | `python manage.py check_financial_integrity` 及异常 fixture | 正常测试库通过，构造异常能按类别失败 |

本工作站未安装 Docker Engine、`pg_dump` 或 `pg_restore`，因此这里的 Compose 与 PostgreSQL 运维备份结果属于静态配置和受控替身测试，不能代替真实服务器演练。

## 目标服务器待验收

上线前必须在目标 Linux 服务器按 `docs/deployment.md` 留存一次不含敏感信息的记录：

1. `docker compose config`、镜像构建和 `docker compose up -d` 成功，四服务均为 healthy；
2. 宿主机只监听 80/443，5432 和 8000 不对公网发布；HTTPS、跳转、安全头及公网 `/health/*` 返回 404；
3. 重启服务器后四服务自动恢复，登录和核心页面可用；
4. 创建一份真实 custom-format 运维备份，完成解密、SHA-256 和 `pg_restore --list` 校验；
5. 将该备份恢复到隔离临时 PostgreSQL，运行迁移和 `check_financial_integrity`，再按 runbook 核对各类记录关系；
6. 使用虚构数据完成一次 `.pfbackup` 业务恢复，确认恢复前自动备份存在且可恢复；
7. 记录日期、Git 提交、镜像标识、备份文件名、加密文件 SHA-256、非金额类记录数和结论。

以上服务器项目全部通过后，才可将本记录状态更新为“生产验收通过”。当前状态：**实现完成，目标服务器验收待执行**。
