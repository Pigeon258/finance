# 第一版验收记录

本文记录任务 15 在开发工作站完成的自动化验收、WSL 2 本地容器验收，以及必须在目标 Linux 服务器补做的生产环境验收。记录不得包含实际金额、密码、密钥、Cookie、账单行或数据库连接串。

## 当前生产状态

截至 2026-08-16，生产版本为 `v0.3.0`（Git 标签 `v0.3.0`）；活动主题为 `aurora-ledger`，last-known-good 为 `safe-default`；四服务均 healthy。`v0.2.0` 之后的维护升级历史与部署备份清单见 `docs/maintenance-history.md`，本节后续为逐次验收记录。

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

本节只证明隔离本地生产 Compose。临时验收密钥、备份、容器和卷在验收后删除，不能作为生产备份。目标服务器发布结果见下一节。

## v0.2.0 视觉主题生产发布验收

- 发布确认日期：2026-08-05
- 发布版本：Git 标签 `v0.2.0`
- 已部署实现提交：`3935f862353c57c9650505dc673dbe6da7f8071e`
- 主题格式版本 / 组件契约版本：`1 / 1`
- 回滚目标：`v0.1.0`（`8c5e13bfcf375d33eba38dccb9b7a56a8b0d171b`）
- 部署前加密数据库备份：`db-deployment-20260804T172216Z-29.dump.enc`

| 检查项 | 结论 |
|---|---|
| `deploy/upgrade.sh` | 通过；构建、备份、维护模式、迁移、完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；应用 `core.0005`、`core.0006` |
| 三组应用镜像 | 通过；Web、Caddy、maintenance 均为 `0.2.0` |
| 四服务健康与端口隔离 | 通过；仅 Caddy 发布 80/443，Web 8000 与 PostgreSQL 5432 无宿主机绑定 |
| `deploy/verify-deployment.sh` | 通过 |
| Django deploy check 与财务完整性 | 通过 |
| 严格主题完整性 | 通过；`active=aurora-ledger`、`resolved=aurora-ledger`、格式/契约 `1/1` |
| 主题卷边界与静态资源 | 通过；Web 读写、Caddy 只读，Aurora CSS 返回 200、正确 MIME、CSP、`nosniff` 和一年 immutable 缓存 |
| 公网路由 | 通过；HTTP 308、未登录 HTTPS 302、`/health/live` 404、未知主题资源 404 |
| 发布后错误日志筛查 | 通过；Web、Caddy、backup 近 20 分钟未匹配异常或 5xx |

生产发布结论：**通过**。本次升级没有改变财务事实来源或统计口径。生产环境隔离恢复和整机重启演练仍按 `docs/deployment.md` 安排维护窗口执行，不以本次在线升级验收替代。

## QUICK-ITERATION-01 生产快速发布验收

- 发布确认日期：2026-08-05
- 已部署提交：`cb3b0c5a1eb0d9b1bb780a4aa5f124f0ee10afd5`
- 发布方式：`deploy/quick-upgrade.sh`，只重建并热替换 Web/Caddy，无迁移、无维护模式
- 部署前加密数据库备份：`db-deployment-20260804T175534Z-30.dump.enc`
- 回滚镜像标签：`quick-rollback-20260804T175518Z`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；Ruff、`324 passed`、Windows PowerShell 窄测试 `16 passed`、Django 普通/生产检查、迁移检查、Compose 配置与 shell 语法检查 |
| 快速发布身份与工作区 | 通过；完整 SHA 匹配，生产跟踪文件发布前后均干净 |
| 备份与回滚 | 通过；加密部署备份完成内置验证，旧 Web/Caddy/maintenance 镜像均可由统一时间标签解析 |
| Web/Caddy 热替换 | 通过；收集 146 个静态文件，Web 与 Caddy healthy，数据库与 backup 服务未替换 |
| 应用完整性 | 通过；Django deploy check、财务完整性和严格主题完整性检查通过 |
| 修复产物 | 通过；运行中 Web 模板包含移动导航 `hidden`，Caddy `app.css` 包含窄屏恢复规则 |
| 公网与静态资源 | 通过；HTTP 308、未登录 HTTPS 302、`app.css` 200 且为 `text/css`、`/health/live` 404 |
| `deploy/verify-deployment.sh` | 通过；四服务 healthy，端口、主题卷和安全响应头符合生产基线 |

生产快速发布结论：**通过**。本任务未改变财务事实、统计口径、数据库结构、凭据、网络暴露、备份格式或安全边界；若页面壳层出现回归，使用上述三组同时间标签镜像执行 `deploy/rollback.sh`。

## 2026-08-15 页面状态本地化与主题跨文件系统发布生产升级验收

- 发布确认日期：2026-08-15
- 发布方式：`deploy/upgrade.sh` 完整升级，无数据库迁移
- 已部署提交：`e638dbcf8d7e3a41a4d4dfda496e67c6790e65af`
- 发布内容：页面状态中文标签与组件统一；主题导入在 `THEME_IMPORT_TMP_DIR` 与 `THEME_RUNTIME_DIR` 跨文件系统时安全回退发布
- 部署前加密数据库备份：`db-deployment-20260815T103131Z-43.dump.enc`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `331 passed`、Django check 通过 |
| 生产工作区与版本 | 通过；`/opt/personal-finance` 检出完整 SHA `e638dbc`，升级前跟踪文件干净 |
| `deploy/upgrade.sh` | 通过；构建、部署前加密备份、维护模式、迁移检查、三项完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；无需应用迁移 |
| `deploy/verify-deployment.sh` | 通过；四服务运行、端口隔离、主题卷读写边界、安全头、静态资源和健康端点隔离符合基线 |
| 财务与主题完整性 | 通过；财务完整性检查通过，严格主题完整性 `active=aurora-ledger resolved=aurora-ledger schema=1 contract=1` |
| 部署产物核验 | 通过；Web 镜像包含状态标签映射、中文主题库文案、跨文件系统发布回退和修复后的页面模板 |
| 公网冒烟 | 通过；HTTP 308、HTTPS 登录页 200、`app.css` 200 且为 `text/css`、`/health/live` 404 |
| 发布后日志筛查 | 通过；Web 与 Caddy 近期日志未发现 error/traceback/exception/critical |

生产发布结论：**通过**。本轮只修改页面展示、状态组件和主题文件发布方式，未改变账务事实、统计口径、数据库结构、凭据、网络暴露或备份格式。生产环境隔离恢复和整机重启演练继续按 `docs/deployment.md` 维护窗口执行。

## 2026-08-15 交易表单辨识与页面间距生产升级验收

- 发布确认日期：2026-08-15
- 发布方式：`deploy/upgrade.sh` 完整升级，无数据库迁移
- 已部署提交：`5e0d740c9f0d3ba8208c13d7481694b6d8b9a7e5`
- 发布内容：收入/支出等手工交易表单增加操作徽章、说明和差异化提交按钮；预算筛选、次级操作和全局控件间距统一优化
- 部署前加密数据库备份：`db-deployment-20260815T125741Z-44.dump.enc`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `334 passed`、Django check 通过 |
| 生产工作区与版本 | 通过；`/opt/personal-finance` 检出完整 SHA `5e0d740`，升级前跟踪文件干净 |
| `deploy/upgrade.sh` | 通过；构建、部署前加密备份、维护模式、迁移检查、三项完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；无需应用迁移 |
| `deploy/verify-deployment.sh` | 通过；四服务运行、端口隔离、主题卷读写边界、安全头、静态资源和健康端点隔离符合基线 |
| 财务与主题完整性 | 通过；财务完整性检查通过，严格主题完整性 `active=aurora-ledger resolved=aurora-ledger schema=1 contract=1` |
| 部署产物核验 | 通过；Web 镜像包含交易操作差异化元数据、预算月份筛选面板和全局间距样式 |
| 公网冒烟 | 通过；HTTP 308、HTTPS 登录页 200、`app.css` 200 且为 `text/css`、`/health/live` 404 |
| 发布后日志筛查 | 通过；Web 与 Caddy 近期日志未发现 error/traceback/exception/critical |

生产发布结论：**通过**。本轮只修改页面表单辨识和视觉间距，未改变账务事实、统计口径、数据库结构、凭据、网络暴露或备份格式。

## 2026-08-15 标签按收入/支出类型隔离生产升级验收

- 发布确认日期：2026-08-15
- 发布方式：`deploy/upgrade.sh` 完整升级
- 已部署提交：`294c97473d3a92f12dd70991f6069d8498aa649e`
- 发布内容：交易标签增加“收入/支出”适用类型；“冲动消费”仅可用于支出；收入、支出表单和服务层均按类型过滤并校验标签
- 部署前加密数据库备份：`db-deployment-20260815T131435Z-45.dump.enc`
- 数据库迁移：应用 `ledger.0006_tag_applies_to`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `336 passed`、Django check 与迁移检查通过 |
| 生产工作区与版本 | 通过；`/opt/personal-finance` 检出完整 SHA `294c974`，升级前跟踪文件干净 |
| `deploy/upgrade.sh` | 通过；构建、部署前加密备份、维护模式、迁移、三项完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；`ledger.0006_tag_applies_to` 已应用，存量“冲动消费”标签归入支出类型 |
| `deploy/verify-deployment.sh` | 通过；四服务运行、端口隔离、主题卷读写边界、安全头、静态资源和健康端点隔离符合基线 |
| 财务与主题完整性 | 通过；财务完整性检查通过，严格主题完整性 `active=aurora-ledger resolved=aurora-ledger schema=1 contract=1` |
| 部署产物核验 | 通过；生产 IncomeForm 不再提供“冲动消费”，ExpenseForm 正常提供；服务层类型校验已生效 |
| 公网冒烟 | 通过；HTTP 308、HTTPS 登录页 200 |
| 发布后日志筛查 | 通过；Web 与 Caddy 近期日志未发现 error/traceback/exception/critical |

生产发布结论：**通过**。本轮改变标签可用范围并增加数据库约束，但未改变账务金额事实、统计口径、凭据、网络暴露或备份格式。

## 2026-08-15 标签管理页面生产发布验收

- 发布确认日期：2026-08-15
- 发布方式：`deploy/upgrade.sh` 完整升级，无新增数据库迁移
- 已部署提交：`9e14d2eda271ec91c8e126b94ddbb185ad0de4e7`
- 发布内容：新增标签管理页面；支持按收入/支出类型新建、编辑、启用/停用和删除未使用标签；已用于交易的标签禁止删除和变更类型
- 部署前加密数据库备份：`db-deployment-20260815T143728Z-46.dump.enc`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `340 passed`、Django check 与迁移检查通过 |
| 生产工作区与版本 | 通过；`/opt/personal-finance` 检出完整 SHA `9e14d2e`，升级前跟踪文件干净 |
| `deploy/upgrade.sh` | 通过；构建、部署前加密备份、维护模式、迁移检查、三项完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；无新增迁移 |
| `deploy/verify-deployment.sh` | 通过；四服务运行、端口隔离、主题卷读写边界、安全头、静态资源和健康端点隔离符合基线 |
| 财务与主题完整性 | 通过；财务完整性检查通过，严格主题完整性 `active=aurora-ledger resolved=aurora-ledger schema=1 contract=1` |
| 部署产物核验 | 通过；Web 镜像包含 `/ledger/tags/` 路由、标签管理模板、新增/编辑/停用/删除视图和侧栏“标签管理”入口 |
| 公网冒烟 | 通过；HTTP 308、HTTPS 登录页 200、`/ledger/tags/` 未登录 302 跳转登录 |
| 发布后日志筛查 | 通过；Web 与 Caddy 近期日志未发现 error/traceback/exception/critical |

生产发布结论：**通过**。本轮新增标签管理 UI，不改变账务事实、统计口径、数据库结构、凭据、网络暴露或备份格式。

## 2026-08-15 分类预算编辑与分类过滤生产发布验收

- 发布确认日期：2026-08-15
- 发布方式：`deploy/upgrade.sh` 完整升级，无新增数据库迁移
- 已部署提交：`5a076c8ce357facf8408e7278b1496d6c9667fe1`
- 发布内容：分类预算批量编辑页面；分类删除按钮与保护；固定支出/预计收入创建时按方向过滤分类
- 部署前加密数据库备份：`db-deployment-20260815T151135Z-47.dump.enc`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `346 passed`、Django check 与迁移检查通过 |
| 生产工作区与版本 | 通过；`/opt/personal-finance` 检出完整 SHA `5a076c8`，升级前跟踪文件干净 |
| `deploy/upgrade.sh` | 通过；构建、部署前加密备份、维护模式、迁移检查、三项完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；无新增迁移 |
| `deploy/verify-deployment.sh` | 通过；四服务运行、端口隔离、主题卷读写边界、安全头、静态资源和健康端点隔离符合基线 |
| 财务与主题完整性 | 通过；财务完整性检查通过，严格主题完整性 `active=aurora-ledger resolved=aurora-ledger schema=1 contract=1` |
| 部署产物核验 | 通过；分类预算批量编辑路由和页面存在，分类删除路由存在，计划现金流页包含固定支出/预计收入两个入口且分类方向过滤生效 |
| 公网冒烟 | 通过；HTTP 308、HTTPS 登录页 200，新页面未登录均 302 跳转登录 |
| 发布后日志筛查 | 通过；Web 与 Caddy 近期日志未发现 error/traceback/exception/critical |

生产发布结论：**通过**。本轮只增加预算配置 UI 和分类维护能力，并修正计划分类选择范围，未改变账务事实、统计口径、数据库结构、凭据、网络暴露或备份格式。


## 2026-08-15 预算项目明细化生产发布验收

- 发布确认日期：2026-08-15
- 发布方式：`deploy/upgrade.sh` 完整升级
- 已部署提交：`bb79f80af0e917a9966cfaecc95d142574506fab`
- 发布内容：预算项目明细化，项目金额自动汇总为月度总预算；存量分类预算迁移为预算项目
- 部署前加密数据库备份：`db-deployment-20260815T154343Z-48.dump.enc`
- 数据库迁移：应用 `budgets.0002_budget_item_name`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `346 passed`、Django check 与迁移检查通过 |
| 生产工作区与版本 | 通过；`/opt/personal-finance` 检出完整 SHA `bb79f80`，升级前跟踪文件干净 |
| `deploy/upgrade.sh` | 通过；构建、部署前加密备份、维护模式、迁移、三项完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；`budgets.0002_budget_item_name` 已应用，生产环境当前无存量预算数据，迁移结果为空 |
| `deploy/verify-deployment.sh` | 通过；四服务运行、端口隔离、主题卷读写边界、安全头、静态资源和健康端点隔离符合基线 |
| 财务与主题完整性 | 通过；财务完整性检查通过，严格主题完整性 `active=aurora-ledger resolved=aurora-ledger schema=1 contract=1` |
| 部署产物核验 | 通过；预算项目列表、新增、编辑、删除路由和模板存在，自动汇总逻辑在测试中覆盖 |
| 公网冒烟 | 通过；HTTP 308、HTTPS 登录页 200，预算项目页面未登录 302 跳转登录 |
| 发布后日志筛查 | 通过；Web 与 Caddy 近期日志未发现 error/traceback/exception/critical |

生产发布结论：**通过**。本轮改变预算明细配置方式并自动汇总总预算，不改变账务金额事实、统计口径、凭据、网络暴露或备份格式。


## 2026-08-16 计划事项与交易项目名称生产发布验收

- 发布确认日期：2026-08-16
- 发布方式：`deploy/upgrade.sh` 完整升级
- 已部署提交：`8c1a3f5959789c1dce07398366774f49cb4d9049`
- 发布内容：周期计划自动延伸到未来 12 个月；交易和 CSV 增加项目名称；预算 100% 阈值语义修正
- 部署前加密数据库备份：`db-deployment-20260816T133313Z-51.dump.enc`
- 数据库迁移：应用 `ledger.0007_transaction_item_name`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `349 passed`、Django check 与迁移检查通过 |
| 生产工作区与版本 | 通过；`/opt/personal-finance` 检出完整 SHA `8c1a3f5`，升级前跟踪文件干净 |
| `deploy/upgrade.sh` | 通过；构建、部署前加密备份、维护模式、迁移、三项完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；`ledger.0007_transaction_item_name` 已应用 |
| `deploy/verify-deployment.sh` | 通过；四服务运行、端口隔离、主题卷读写边界、安全头、静态资源和健康端点隔离符合基线 |
| 财务与主题完整性 | 通过；财务完整性检查通过，严格主题完整性 `active=aurora-ledger resolved=aurora-ledger schema=1 contract=1` |
| 部署产物核验 | 通过；Web 镜像包含未来事项自动生成逻辑、交易项目字段和详情/列表展示 |
| 公网冒烟 | 通过；HTTP 308、HTTPS 登录页 200 |
| 发布后日志筛查 | 通过；Web 与 Caddy 近期日志未发现 error/traceback/exception/critical |

生产发布结论：**通过**。本轮不改变账务金额事实、统计口径、凭据、网络暴露或备份格式。


## 2026-08-16 首页月度预算状态修复生产发布验收

- 发布确认日期：2026-08-16
- 发布方式：`deploy/upgrade.sh` 完整升级，无数据库迁移
- 已部署提交：`8a66e61b6a8e2d26c23b2e7045efb288f83f76d8`
- 发布内容：首页“月度预算”风险状态与预算项目/分类阈值保持一致
- 部署前加密数据库备份：`db-deployment-20260816T134819Z-52.dump.enc`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `350 passed`、Django check 与迁移检查通过 |
| `deploy/upgrade.sh` | 通过；构建、部署前加密备份、维护模式、迁移检查、三项完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；无新增迁移 |
| `deploy/verify-deployment.sh` | 通过 |
| 财务与主题完整性 | 通过 |
| 公网冒烟 | 通过；HTTP 308、HTTPS 登录页 200 |

生产发布结论：**通过**。


## 2026-08-16 首页可分配预算资金生产发布验收

- 发布确认日期：2026-08-16
- 发布方式：`deploy/upgrade.sh` 完整升级，无数据库迁移
- 已部署提交：`15d8ce40174008432e7a60bddc3c4dd15363b876`
- 发布内容：首页新增“可分配预算资金”，用于显示尚未被预算占用的资金
- 部署前加密数据库备份：`db-deployment-20260816T141038Z-54.dump.enc`
- 前置提交：`2775f80`（初步增加预算剩余指标，随后由本提交修正口径）

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `350 passed`、Django check 通过 |
| `deploy/upgrade.sh` | 通过 |
| 数据库迁移 | 通过；无新增迁移 |
| `deploy/verify-deployment.sh` | 通过 |
| 财务与主题完整性 | 通过 |
| 公网冒烟 | 通过；HTTP 308、HTTPS 登录页 200 |

生产发布结论：**通过**。


## 2026-08-16 储蓄拆分与结转生产发布验收

- 发布确认日期：2026-08-16
- 发布方式：`deploy/upgrade.sh` 完整升级
- 已部署提交：`fb533dabc3a72559a2c0fb06db576be72fb382f7`
- 发布内容：储蓄目标不再占用消费预算；新增上月储蓄实际金额确认结转
- 部署前加密数据库备份：`db-deployment-20260816T143734Z-55.dump.enc`
- 数据库迁移：应用 `budgets.0003_savings_settlement`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `351 passed`、Django check 与迁移检查通过 |
| `deploy/upgrade.sh` | 通过；构建、备份、维护模式、迁移、三项完整性检查和退出维护模式均完成 |
| 数据库迁移 | 通过；`budgets.0003_savings_settlement` 已应用 |
| `deploy/verify-deployment.sh` | 通过 |
| 财务与主题完整性 | 通过 |
| 公网冒烟 | 通过 |

生产发布结论：**通过**。本轮改变预算展示口径和储蓄结转流程，不改变账务金额事实、统计口径、凭据、网络暴露或备份格式。


## 2026-08-16 理财管理第一版生产发布验收

- 发布确认日期：2026-08-16
- 发布方式：`deploy/upgrade.sh` 完整升级
- 已部署提交：`8481f8dab0d564949c0c769b893488082f2afa80`
- 前置功能提交：`e72e195`
- 发布内容：理财账户、转入转出、手工估值、理财收益、余额宝收益率同步、首页理财概览
- 部署前加密数据库备份：`db-deployment-20260816T152129Z-57.dump.enc`
- 数据库迁移：`accounts.0004_account_type_wealth`、`wealth.0001_initial`

| 检查项 | 结论 |
|---|---|
| 提交前质量门槛 | 通过；`ruff check .`、`pytest` 全量 `356 passed`、Django check 与迁移检查通过 |
| `deploy/upgrade.sh` | 通过 |
| 数据库迁移 | 通过；`accounts.0004`、`wealth.0001` 已应用 |
| `deploy/verify-deployment.sh` | 通过 |
| 财务与主题完整性 | 通过 |
| 公网冒烟 | 通过 |

生产发布结论：**通过**。本模块不改变既有账务统计口径、网络暴露或备份格式；余额宝同步为服务端可选功能。
