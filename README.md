# Personal Finance

面向单用户的个人财务管理系统，采用 Django 服务端渲染的模块化单体架构。需求与设计基线见：

- `docs/requirements.md`
- `docs/system-design.md`
- `tasks/README.md`

## 当前进度

已完成任务 00～16 的代码实现、本地容器验收和生产上线基线固化，包括：

- 单用户登录、会话与系统设置；
- 账户、分类和 Decimal 核心账本；
- 收入、支出、转账、退款、作废、修正与余额核对；
- 信用卡账期、全额还款与退款抵扣；
- 商品分期、提前结清和分期退款；
- 月度预算、分类预算、储备资金、固定支出与预计收入；
- 信用卡偿还能力、未来现金流预测、分期预览与内部预警；
- 首页仪表盘、独立未来 30 天事项视图和第一版统计报表；
- 支付宝/微信 CSV、XLSX、单层 ZIP 的安全解析、映射去重、人工复核、信息安全合并与幂等入账；
- 历史交易复制、最近账户、常用交易模板和导入规则管理；
- 登录会话撤销、可配置安全/预算/大额消费阈值，以及基础手机访问和无 JavaScript 降级；
- UTF-8 CSV 导出、Scrypt + AES-256-GCM 加密业务备份、事务恢复和财务完整性检查。
- 固定版本的四服务 Docker Compose 部署、Caddy/Gunicorn、安全配置、加密 PostgreSQL 自动备份、轮换及部署/恢复 runbook。

当前版本已经可以在本地试用上述核心流程、账单导入和加密业务备份恢复，并已在 Ubuntu 22.04 / WSL 2 中通过四服务 Compose、网络隔离、安全配置、真实 PostgreSQL 加密备份与隔离恢复、业务恢复和 Docker Engine 重启验收。2026-08-03，生产环境已完成公网 HTTPS、防火墙、端口隔离、安全配置、生产检查和加密数据库备份验收，生产基线已固化为 `v0.1.0`。生产环境隔离恢复和整机重启演练仍需按 `docs/deployment.md` 在维护窗口定期执行。

生产基线之后已启动 `VISUAL-THEME` 视觉主题扩展：需求与安全架构基线已经确定，后续将依次完成组件系统、主题运行时、沉浸式内置主题、安全导入、质量收口和生产验收。具体状态与依赖见 `tasks/README.md`。

## 技术要求

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 17

## Windows 本地试运行

以下命令均在项目根目录的 PowerShell 中执行。

### 1. 安装依赖

```powershell
uv sync --group dev
```

### 2. 创建本地 PostgreSQL 数据库

在 PostgreSQL 的 SQL Shell（`psql`）或 pgAdmin Query Tool 中，以管理员身份执行：

```sql
CREATE USER finance WITH PASSWORD 'change-me';
CREATE DATABASE personal_finance OWNER finance;
```

如果已经存在同名用户或数据库，请复用现有对象，或同步修改下一步 `.env` 中的连接信息。

### 3. 配置环境变量

```powershell
Copy-Item .env.example .env
```

检查 `.env` 中的 `POSTGRES_*` 配置是否与本地数据库一致。`.env` 不会由 Django 自动加载，下面的命令统一通过 `uv run --env-file .env` 加载它。

### 4. 初始化数据库和唯一所有者

```powershell
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py create_owner
```

`create_owner` 会交互式要求输入两次密码。密码至少 12 位，并需要通过 Django 的常见密码等校验。系统只允许创建一个所有者，不提供注册入口。

### 5. 启动开发服务器

```powershell
uv run --env-file .env python manage.py runserver
```

浏览器访问 <http://127.0.0.1:8000/>，使用刚创建的所有者账号登录。按 `Ctrl+C` 停止服务器。

## 建议试用顺序

1. 在“账户”和“分类”页面查看初始化数据，并按需编辑账户初始余额。
2. 在“交易”页面录入收入、支出和账户转账，确认余额随账本条目计算。
3. 尝试对测试交易执行退款、作废、反向修正或账户余额核对。
4. 在“预算管理”中设置当月总预算、分类预算、储蓄目标和安全余量。
5. 在“信用卡”中配置唯一信用卡资料，再录入信用卡消费、出账和全额还款。
6. 在“分期”中创建测试分期，并查看未来月份预算占用。
7. 打开“风险预测”，检查偿还能力、逐月现金流、内部预警和新增分期只读预览。
8. 在“账单导入”中上传虚构的支付宝或微信账单，检查账户映射、分类推荐和重复候选，再人工确认写入正式账本。
9. 从交易列表复制一笔测试交易，或在“常用模板”中保存并套用常见收支；套用模板只预填表单，不会直接入账。
10. 在“系统设置”中调整阈值并查看登录会话；可用另一个浏览器隐私窗口登录后测试撤销其他会话。
11. 打开“未来事项”检查从当天到第 30 天（含边界）的待办，并缩窄浏览器窗口验证基础手机布局。
12. 在“导出与备份”中导出测试 CSV，再使用单独口令下载 `.pfbackup`；恢复演练前请先确认数据均为虚构测试数据，并妥善保存恢复前自动备份使用的同一口令。

建议仅使用虚构测试数据。业务备份文件默认保存在下载目录，恢复前自动备份保存在 `BUSINESS_BACKUP_DIR`（默认项目 `backups/`，已被 Git 忽略）。生产环境的自动数据库备份由 Compose 的 `backup` 服务执行，配置和恢复步骤见部署文档。

## 生产部署

目标 Linux 服务器需安装 Docker Engine 与 Compose v2，并准备域名、仅开放 80/443 的防火墙和独立备份主密钥。首次部署、升级、回滚、数据库恢复及验收命令见：

- `docs/deployment.md`
- `docs/acceptance.md`

2026-07-13 已在 Ubuntu 22.04 / WSL 2 对提交 `b91668c` 完成本地容器验收；2026-08-03 已完成目标服务器生产上线验收并固化 `v0.1.0` 基线。详细证据见 `docs/acceptance.md`。生产环境隔离恢复和整机重启演练继续按运维计划执行。

## 质量检查

```powershell
uv run ruff check .
uv run pytest
uv run python manage.py check
uv run python manage.py check_financial_integrity
```

测试默认使用 SQLite 内存数据库；本地开发配置使用 PostgreSQL。

## 配置说明

- 默认设置：`config.settings.development`
- 测试设置：`config.settings.test`
- 生产设置：`config.settings.production`
- 数据库存储带时区时间，应用时区由 `APP_TIME_ZONE` 配置。

不得提交 `.env`、密钥、真实账单、数据库转储、生产备份或敏感日志。不要通过 Django shell 或其他方式创建额外用户。
