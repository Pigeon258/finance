# Personal Finance

面向单用户的个人财务管理系统。项目采用 Django 服务端渲染的模块化单体架构；需求和设计基线见：

- `docs/requirements.md`
- `docs/system-design.md`

当前完成阶段：任务 `00-project-foundation`，仅包含项目骨架和测试基础。

## 环境要求

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 17

## 本地开发

```powershell
uv sync --group dev
Copy-Item .env.example .env
```

在 `.env` 中配置本地 PostgreSQL 连接，然后执行：

```powershell
uv run python manage.py migrate
uv run python manage.py runserver
```

默认地址为 `http://127.0.0.1:8000/`。

## 质量检查

```powershell
uv run ruff check .
uv run pytest
uv run python manage.py check
```

测试默认使用 SQLite 内存数据库以保证基础测试可独立运行；开发和生产配置使用 PostgreSQL。

## 配置

- 默认设置：`config.settings.development`
- 测试设置：`config.settings.test`
- 生产设置：`config.settings.production`
- 数据库存储带时区时间，页面展示时区由 `APP_TIME_ZONE` 配置。

不得提交 `.env`、密钥、真实账单、数据库转储或生产备份。
