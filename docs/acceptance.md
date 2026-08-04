# 第一版验收记录

本文记录任务 15 在开发工作站完成的自动化验收、WSL 2 本地容器验收，以及必须在目标 Linux 服务器补做的生产环境验收。记录不得包含实际金额、密码、密钥、Cookie、账单行或数据库连接串。

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

上述自动化检查最初在 Windows 开发环境执行；Compose 与 PostgreSQL 命令部分随后在以下 WSL 2 容器环境完成真实验收。

## WSL 2 本地容器验收

- 验收日期：2026-07-13
- 验收环境：WSL 2，Ubuntu 22.04，Linux containers
- 验收基线提交：`b91668c`
- 执行方式：由系统所有者按照本仓库本地容器验收流程亲自执行并确认结果

| 检查项 | 结论 |
|---|---|
| `docker compose config`、固定版本镜像构建及首次部署 | 通过 |
| `caddy`、`web`、`db`、`backup` 四服务健康检查 | 通过 |
| 仅 Caddy 发布 80/443，Web 8000 与 PostgreSQL 5432 不发布 | 通过 |
| HTTP 到 HTTPS、Caddy 本地 HTTPS、安全响应头及静态资源 | 通过 |
| 公网 `/health/*` 返回 404，容器内部 readiness 返回 200 | 通过 |
| production deploy check 与正常数据库财务完整性检查 | 通过 |
| 登录、CSRF、未登录跳转及核心业务浏览器冒烟 | 通过 |
| 真实 `pg_dump --format=custom`、加密、SHA-256、解密及 `pg_restore --list` | 通过 |
| 明文临时转储清理、每日/每周轮换相关容器配置 | 通过 |
| 隔离临时 PostgreSQL 真实恢复、迁移、记录数核对及完整性检查 | 通过 |
| `.pfbackup` 业务恢复及恢复前自动业务备份 | 通过 |
| 密钥、查询字符串和敏感请求信息不进入容器日志 | 通过 |
| 容器异常退出、Compose 重启及 Docker Engine 重启恢复 | 通过 |

本地容器验收结论：**通过**。该结果证明当前提交能够在 WSL 2 的真实 Linux 容器、PostgreSQL 工具链和恢复流程中运行，不再仅依赖替身测试。

## 目标服务器验收清单

上线前必须在目标 Linux 服务器按 `docs/deployment.md` 留存一次不含敏感信息的记录：

1. `docker compose config`、镜像构建和 `docker compose up -d` 成功，四服务均为 healthy；
2. 宿主机只监听 80/443，5432 和 8000 不对公网发布；HTTPS、跳转、安全头及公网 `/health/*` 返回 404；
3. 重启服务器后四服务自动恢复，登录和核心页面可用；
4. 创建一份真实 custom-format 运维备份，完成解密、SHA-256 和 `pg_restore --list` 校验；
5. 将该备份恢复到隔离临时 PostgreSQL，运行迁移和 `check_financial_integrity`，再按 runbook 核对各类记录关系；
6. 使用虚构数据完成一次 `.pfbackup` 业务恢复，确认恢复前自动备份存在且可恢复；
7. 记录日期、Git 提交、镜像标识、备份文件名、加密文件 SHA-256、非金额类记录数和结论。

上述清单的生产执行结果见下一节；隔离恢复和整机重启演练继续作为上线后的定期运维项目。

## 生产上线验收

- 上线确认日期：2026-08-03
- 正式域名：`finance.example.com`
- 生产基线：Git 标签 `v0.1.0`
- 系统所有者确认：项目已验收上线

| 检查项 | 结论 |
|---|---|
| 公网 DNS、80/443 与 HTTP 到 HTTPS 跳转 | 通过 |
| Let's Encrypt 正式证书和安全响应头 | 通过；证书域名为 `finance.example.com` |
| 四服务健康与 Docker 端口隔离 | 通过；5432、8000 无宿主机绑定 |
| Django deploy check、迁移与财务完整性检查 | 通过；无待应用迁移 |
| Web 容器 HTTPS/CSRF/Cookie 实际配置 | 通过 |
| 登录页、CSRF Cookie 与静态资源 | 通过；未使用或记录用户凭据 |
| 公网健康端点隔离 | 通过；`/health/*` 返回 404 |
| 部署前与上线后加密数据库备份 | 通过；备份文件已生成并完成内置验证 |
| 容器错误日志筛查 | 通过；未发现应用错误或敏感数据泄露 |

生产上线验收结论：**通过**。首次生产环境隔离恢复演练和整机重启演练仍按 `docs/deployment.md` 安排维护窗口执行，不以本次在线检查替代。

## v0.2.0 视觉主题本地发布验收

- 验收日期：2026-08-04
- 验收环境：Windows 11、Docker Desktop 29.6.1、Linux containers
- 隔离 Compose 项目：`pf-theme-acceptance`
- 应用版本：`0.2.0`
- 主题格式版本 / 组件契约版本：`1 / 1`

| 检查项 | 结论 |
|---|---|
| Ruff、全量 pytest、Django check 与迁移检查 | 通过；最终自动化数量以本节后续发布记录为准 |
| `docker compose config` 与 Caddy 2.10.2 配置校验 | 通过 |
| 0.2.0 Web、Caddy、maintenance 镜像构建 | 通过；`collectstatic` 收集 146 个文件 |
| PostgreSQL 17 迁移、deploy check 与财务完整性 | 通过 |
| 严格主题完整性 | 通过；`active=aurora-ledger`、`resolved=aurora-ledger`、格式/契约 `1/1` |
| 主题卷权限 | 首轮发现新卷为 root 所有、UID 10001 无法导入；在镜像预创建 `/app/var/themes` 并赋权后复测通过 |
| 主题卷持久化与只读边界 | 通过；Web `RW=true`、Caddy `RW=false`，Web/Caddy 强制重建后测试 CSS 仍存在 |
| Caddy 主题静态响应 | 通过；CSS 200、`text/css`、CSP、`nosniff`、一年 immutable 缓存；未知主题和运行时 JSON 均 404 |
| 端口隔离 | 通过；Web 8000 和 PostgreSQL 5432 无宿主机绑定，仅 Caddy 发布 80/443 |
| 部署前加密数据库备份 | 通过；隔离备份完成内置解密、SHA-256 与 `pg_restore --list` 校验 |
| 数据库恢复演练 | 通过；先创建安全备份，单事务恢复后无迁移、财务和主题完整性检查通过，HTTPS 返回登录跳转 302 |
| 应用升级序列 | 通过；维护模式启用 → 迁移 → 三项完整性检查 → 关闭维护模式 |

本节只证明隔离本地生产 Compose。临时验收密钥、备份、容器和卷在验收后删除，不能作为生产备份。`v0.2.0` 目标服务器发布仍需创建真实部署前备份、执行 `deploy/upgrade.sh` 与 `deploy/verify-deployment.sh`，并单独记录公网结果。
