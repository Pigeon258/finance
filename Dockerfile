ARG PYTHON_IMAGE=python:3.13.9-slim-bookworm
ARG POSTGRES_IMAGE=postgres:17.6-bookworm
ARG CADDY_IMAGE=caddy:2.10.2-alpine

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY docker-wheels/uv-0.8.22-*.whl /tmp/

RUN pip install --no-cache-dir /tmp/uv-0.8.22-*.whl \
    && rm -f /tmp/uv-0.8.22-*.whl
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS assets
ENV PATH=/opt/venv/bin:$PATH \
    DJANGO_SETTINGS_MODULE=config.settings.test \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY . .
RUN python manage.py collectstatic --noinput

FROM ${PYTHON_IMAGE} AS production
ENV PATH=/opt/venv/bin:$PATH \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 finance \
    && useradd --uid 10001 --gid finance --home-dir /app --shell /usr/sbin/nologin finance
COPY --from=builder /opt/venv /opt/venv
COPY --from=assets --chown=finance:finance /app /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import os, urllib.request; host=os.environ['DJANGO_ALLOWED_HOSTS'].split(',')[0]; request=urllib.request.Request('http://127.0.0.1:8000/health/ready', headers={'Host':host,'X-Forwarded-Proto':'https'}); raise SystemExit(0 if urllib.request.urlopen(request, timeout=3).status == 200 else 1)"
CMD ["gunicorn", "--config", "gunicorn.conf.py", "config.wsgi:application"]

FROM ${POSTGRES_IMAGE} AS maintenance
ENV PATH=/opt/venv/bin:$PATH \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 finance \
    && useradd --uid 10001 --gid finance --home-dir /app --shell /usr/sbin/nologin finance
COPY --from=production /usr/local /usr/local
COPY --from=production /opt/venv /opt/venv
COPY --from=production --chown=finance:finance /app /app
ENTRYPOINT []
USER 10001:10001
HEALTHCHECK --interval=5m --timeout=5s --retries=2 \
    CMD python -c "from pathlib import Path; raise SystemExit(0 if Path('/tmp/backup-scheduler-heartbeat').exists() else 1)"
CMD ["python", "scripts/backup_scheduler.py"]

FROM ${CADDY_IMAGE} AS caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=assets /app/staticfiles /srv/static
