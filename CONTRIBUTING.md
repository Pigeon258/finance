# Contributing

感谢你对 Personal Finance 的关注。

## 项目原则

这是一个自托管的单用户个人财务系统。修改时应优先保证财务正确性、安全边界和数据可恢复性：

- 金额计算使用 `Decimal`，不得引入二进制浮点金额；
- 账本事实保持可追溯，不破坏已经建立正式关系的数据；
- 涉及账务口径、备份格式或安全边界的修改需要说明影响并补充测试；
- 不提交真实财务数据、凭据、密钥、备份或敏感日志。

较大的功能建议先通过 GitHub Issue 说明目标、范围和兼容性影响。

## 开发环境

```bash
uv sync --group dev
cp .env.example .env
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py create_owner
uv run --env-file .env python manage.py runserver
```

## 提交前检查

```bash
uv run ruff check .
uv run pytest
uv run python manage.py check
uv run python manage.py check_financial_integrity
uv run python manage.py check_theme_integrity --strict
```

请保持改动聚焦，并在 Pull Request 中说明变更内容、验证结果以及可能影响的数据或部署行为。
