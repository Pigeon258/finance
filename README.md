# Personal Finance

面向单用户的个人财务管理系统，采用 Django 服务端渲染的模块化单体架构。需求与设计基线见：

- `docs/requirements.md`
- `docs/system-design.md`
- `tasks/README.md`

## 当前进度

已完成任务 00～12，包括：

- 单用户登录、会话与系统设置；
- 账户、分类和 Decimal 核心账本；
- 收入、支出、转账、退款、作废、修正与余额核对；
- 信用卡账期、全额还款与退款抵扣；
- 商品分期、提前结清和分期退款；
- 月度预算、分类预算、储备资金、固定支出与预计收入；
- 信用卡偿还能力、未来现金流预测、分期预览与内部预警；
- 首页仪表盘、未来 30 天事项和第一版统计报表。
- 支付宝/微信 CSV、XLSX、单层 ZIP 的安全解析、映射去重、人工复核与幂等入账。

当前版本已经可以在本地试用上述核心流程和账单导入，但导出与加密备份、Docker Compose 生产部署尚未实现。请勿把当前版本直接作为生产服务暴露到公网。

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

建议仅使用虚构测试数据。当前尚未实现完整业务备份与恢复，重要数据不要只保存在本地试运行数据库中。

## 质量检查

```powershell
uv run ruff check .
uv run pytest
uv run python manage.py check
```

测试默认使用 SQLite 内存数据库；本地开发配置使用 PostgreSQL。

## 配置说明

- 默认设置：`config.settings.development`
- 测试设置：`config.settings.test`
- 生产设置：`config.settings.production`
- 数据库存储带时区时间，应用时区由 `APP_TIME_ZONE` 配置。

不得提交 `.env`、密钥、真实账单、数据库转储、生产备份或敏感日志。不要通过 Django shell 或其他方式创建额外用户。
