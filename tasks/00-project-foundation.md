# 00 项目骨架与测试基础

## 目标

建立可启动、可测试、可扩展的 Django 模块化单体骨架，不实现任何财务业务。

## 范围

- Python 3.13、Django 5.2、pytest/pytest-django、Ruff 和依赖锁定。
- `config.settings` 的 base/development/test/production 分层。
- 创建八个空 App、根路由、基础模板、静态目录、错误页与 live/ready 健康检查。
- PostgreSQL 开发/测试连接配置；Decimal 与时区通用约定。
- `.env.example`、安全 `.gitignore`、最小 README 开发命令。

## 不包含内容

- 业务模型、认证限流、正式页面、Docker 生产部署、HTMX/ECharts 功能。

## 涉及模块

`config`、`apps.core` 及其余 App 的空壳。

## 主要数据模型或接口

- 无业务模型。
- `GET /health/live` 不访问数据库。
- `GET /health/ready` 执行数据库连通性和迁移状态检查，响应不泄露内部信息。

## 实施步骤

1. 初始化 `pyproject.toml`、锁文件、Django 项目和 App 包。
2. 配置模板、静态文件、语言、`USE_TZ=True` 和可配置显示时区。
3. 设置测试数据库、pytest 标记、工厂目录和基础 fixtures。
4. 实现健康检查与基础布局；普通导航先保留占位。
5. 配置 Ruff、pytest 和本地质量命令。

## 测试要求

- Django system check、URL 反解、模板渲染测试。
- live 不查询数据库；ready 在数据库可用/不可用时返回正确且最小的信息。
- 测试设置禁止 `float` 金额 fixture 的约定检查可写为辅助断言。

## 完成标准

- 新环境按 README 可安装依赖、迁移并启动。
- `ruff check .`、`pytest`、`python manage.py check` 通过。
- 仓库不含密钥、真实账单或生产配置。

## 提交建议

单提交：`chore: bootstrap django project`
