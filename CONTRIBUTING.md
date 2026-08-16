# Contributing

Thanks for your interest in Personal Finance.

## Project scope

This is a self-hosted, single-user personal finance system. The first priority is financial correctness: amounts must stay in `Decimal`, ledger facts must remain traceable, and destructive deletion of formally related records is not allowed.

Please open an issue before implementing large features, especially anything that changes accounting semantics, backup format, budget statistics, or security boundaries.

## Development

Requirements:

- Python 3.13
- uv
- PostgreSQL 17 for development; tests run on SQLite

```bash
uv sync --group dev
cp .env.example .env
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py create_owner
uv run --env-file .env python manage.py runserver
```

## Quality checks

```bash
uv run ruff check .
uv run pytest
uv run python manage.py check
uv run python manage.py check_financial_integrity
uv run python manage.py check_theme_integrity --strict
```

## Branches and releases

- Use a focused `fix/*` or `feat/*` branch.
- Every change must include regression tests when it affects financial calculations or page behavior.
- After several changes are stable, a maintainer creates a `release/vX.Y.Z` branch, updates version files, tags the release, and publishes the tag.
