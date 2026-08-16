# Personal Finance

Personal Finance 是一个面向单用户、自托管场景的个人财务管理系统。项目使用 Django 服务端渲染的模块化单体架构，以复式账本为资金事实来源，重点保证金额计算、账户余额、信用卡负债、预算占用和备份恢复的一致性。

## 当前状态

当前稳定版本为 `v0.3.0`，已经提供完整的本地运行与 Docker Compose 部署能力。主要功能包括：

- 单用户登录、会话管理与安全设置；
- 资产账户、信用卡账户、分类和精确十进制账本；
- 收入、支出、转账、退款、作废、修正和余额核对；
- 信用卡账期、全额还款、商品分期和未来付款计划；
- 月度预算、预算项目、储蓄目标、固定支出和预计收入；
- 偿还能力分析、未来现金流预测、仪表盘和统计报表；
- 支付宝与微信账单文件的解析、映射、去重、人工确认和幂等入账；
- 理财账户、转入转出、手工估值、收益记录和可选收益率同步；
- CSV 导出、加密 `.pfbackup` 业务备份、事务恢复和财务完整性检查；
- 声明式主题包、主题预览与切换、安全导入和故障回退；
- Caddy、Gunicorn、PostgreSQL 和自动备份组成的 Docker Compose 部署。

## 设计边界

- 系统只面向单用户，不提供公开注册或多用户权限体系。
- 所有业务金额使用 `Decimal`；账户余额由账本条目计算，不维护可随意修改的缓存余额。
- 系统只记录和分析财务数据，不绑定支付账户，也不发起真实支付或转账。
- 核心流程使用服务端页面完成；HTMX 和主题系统只增强交互与外观。
- 正式关联的财务记录通过停用、作废、退款或反向修正处理，避免破坏性删除。

详细业务规则与系统结构见 [需求说明](docs/requirements.md) 和 [系统设计](docs/system-design.md)。

## 技术栈

- Python 3.13
- Django 5.2 LTS
- PostgreSQL 17
- Django Templates、HTMX、Bootstrap 5、Apache ECharts
- Gunicorn、Caddy、Docker Compose
- uv、pytest、Ruff

## Windows 本地运行

以下命令在项目根目录的 PowerShell 中执行。

### 1. 安装依赖

```powershell
uv sync --group dev
```

### 2. 创建 PostgreSQL 数据库

在 `psql` 或 pgAdmin 中执行：

```sql
CREATE USER finance WITH PASSWORD 'change-me';
CREATE DATABASE personal_finance OWNER finance;
```

如需使用其他数据库名称或账号，请同步修改下一步的本地配置。

### 3. 配置并初始化

```powershell
Copy-Item .env.example .env
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py create_owner
```

请先检查 `.env` 中的 `POSTGRES_*` 配置。`create_owner` 会交互式创建唯一所有者账号，密码至少 12 位并需通过 Django 密码校验。

### 4. 启动服务

```powershell
uv run --env-file .env python manage.py runserver
```

浏览器访问 <http://127.0.0.1:8000/>。按 `Ctrl+C` 停止服务。

## 建议试用顺序

1. 初始化账户和分类，并录入虚构的收入、支出与转账。
2. 配置信用卡资料，体验消费、出账和全额还款。
3. 设置月度预算、预算项目、储蓄目标与计划现金流。
4. 创建测试分期，查看未来预算占用和偿还能力预测。
5. 上传虚构的支付宝或微信账单，人工复核后再写入正式账本。
6. 导出 CSV，并使用单独口令演练 `.pfbackup` 备份与恢复。
7. 在主题库中预览内置主题；导入第三方主题前核对来源、许可和 SHA-256。

请只使用虚构测试数据进行试用。不要把 `.env`、密钥、真实账单、数据库转储、备份文件或敏感日志提交到仓库。

## 生产部署

生产环境需要 Linux、Docker Engine、Compose v2、域名和独立备份密钥。首次部署、升级、回滚、数据库恢复和故障处理见 [部署与恢复指南](docs/deployment.md)。

## 质量检查

```powershell
uv run ruff check .
uv run pytest
uv run python manage.py check
uv run python manage.py check_financial_integrity
uv run python manage.py check_theme_integrity --strict
```

测试默认使用 SQLite 内存数据库；本地开发和生产配置使用 PostgreSQL。

## 文档

- [需求说明](docs/requirements.md)：项目目标、业务规则、功能范围和安全要求。
- [系统设计](docs/system-design.md)：架构、数据模型、账务不变量和部署设计。
- [部署与恢复](docs/deployment.md)：自托管部署、备份、升级、回滚和恢复。
- [理财管理](docs/wealth-management-design.md)：理财账户、资金转换和收益口径。
- [主题制作](docs/theme-authoring.md)：声明式主题包的制作、校验和恢复。
- [主题包契约](docs/theme-package-contract.md) 与 [组件契约](docs/theme-component-contract.md)：主题格式和可用样式边界。
- [安全策略](SECURITY.md)：受支持版本和漏洞报告方式。

## 许可证与参与

项目采用 [MIT License](LICENSE)。贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
