import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from scripts.backup_scheduler import next_occurrence, parse_clock

ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_synchronized():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version_module = (ROOT / "config" / "version.py").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert project["project"]["version"] == "0.2.0"
    assert 'APP_VERSION = "0.2.0"' in version_module
    assert compose.count(":0.2.0}") == 3


def test_compose_has_four_isolated_restartable_services():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert set(services) == {"caddy", "web", "db", "backup"}
    assert all(service["restart"] == "unless-stopped" for service in services.values())
    assert all("healthcheck" in services[name] for name in ("caddy", "db"))
    assert (ROOT / "Dockerfile").read_text(encoding="utf-8").count("HEALTHCHECK") == 2
    assert set(services["caddy"]["ports"]) == {"80:80", "443:443", "443:443/udp"}
    assert "ports" not in services["web"]
    assert "ports" not in services["db"]
    assert "ports" not in services["backup"]
    assert compose["networks"]["backend"]["internal"] is True
    assert set(services["web"]["networks"]) == {"edge", "backend"}
    assert set(services["db"]["networks"]) == {"backend"}
    assert set(services["backup"]["networks"]) == {"backend"}


def test_compose_uses_secrets_read_only_tmpfs_and_log_rotation():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["web"]["read_only"] is True
    assert services["backup"]["read_only"] is True
    assert {"django_secret_key", "database_password"} <= set(services["web"]["secrets"])
    assert "backup_master_key" in services["backup"]["secrets"]
    assert any("/app/runtime/imports" in row for row in services["web"]["tmpfs"])
    assert any("/app/runtime/theme-imports" in row for row in services["web"]["tmpfs"])
    assert any("/runtime" in row for row in services["backup"]["tmpfs"])
    for service in services.values():
        assert service["logging"]["options"] == {"max-size": "10m", "max-file": "5"}
        assert "no-new-privileges:true" in service["security_opt"]
    assert services["web"]["cap_drop"] == ["ALL"]
    assert services["backup"]["cap_drop"] == ["ALL"]


def test_runtime_theme_volume_is_persistent_and_read_only_for_caddy():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "theme_data" in compose["volumes"]
    assert "theme_data:/app/var/themes" in services["web"]["volumes"]
    assert "theme_data:/srv/themes:ro" in services["caddy"]["volumes"]
    assert services["web"]["environment"]["THEME_RUNTIME_DIR"] == "/app/var/themes"
    assert services["web"]["environment"]["THEME_IMPORT_TMP_DIR"] == (
        "/app/runtime/theme-imports"
    )


def test_web_receives_explicit_https_security_environment():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    environment = compose["services"]["web"]["environment"]

    assert "env_file" not in compose["services"]["web"]
    assert environment["DJANGO_SECURE_SSL_REDIRECT"] == "${DJANGO_SECURE_SSL_REDIRECT:-True}"
    assert environment["DJANGO_CSRF_COOKIE_SECURE"] == "${DJANGO_CSRF_COOKIE_SECURE:-True}"
    assert environment["DJANGO_SESSION_COOKIE_SECURE"] == "${DJANGO_SESSION_COOKIE_SECURE:-True}"
    assert environment["DJANGO_CSRF_TRUSTED_ORIGINS"] == "https://${APP_DOMAIN:?set APP_DOMAIN}"


def test_container_images_are_pinned_and_web_runs_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose_text = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "python:3.13.9-slim-bookworm" in dockerfile
    assert "postgres:17.6-bookworm" in dockerfile
    assert "caddy:2.10.2-alpine" in dockerfile
    assert "postgres:17.6-alpine3.22" in compose_text
    assert ":latest" not in dockerfile + compose_text
    assert "USER 10001:10001" in dockerfile
    assert "install -d -o finance -g finance -m 0755 /app/var/themes" in dockerfile
    assert "headers={'Host':host,'X-Forwarded-Proto':'https'}" in dockerfile
    assert "Docker Socket" not in compose_text
    assert "privileged:" not in compose_text
    assert "personal-finance-web:0.2.0" in compose_text
    assert "personal-finance-caddy:0.2.0" in compose_text
    assert "personal-finance-maintenance:0.2.0" in compose_text


def test_caddy_blocks_health_routes_and_sets_security_headers_without_request_logging():
    caddyfile = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    gunicorn = (ROOT / "gunicorn.conf.py").read_text(encoding="utf-8")

    for header in [
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
    ]:
        assert header in caddyfile
    assert "@health path /health/*" in caddyfile
    assert "handle @health {" in caddyfile
    assert "respond 404" in caddyfile
    assert "handle_path /themes/*" in caddyfile
    assert "handle_path /static/themes/*" in caddyfile
    assert "theme\\.css|preview\\.webp|assets/" in caddyfile
    assert caddyfile.count('Cache-Control "public, max-age=31536000, immutable"') == 2
    assert 'Cache-Control "public, max-age=3600, must-revalidate"' in caddyfile
    assert "\n  log" not in caddyfile
    assert "%(q)s" not in gunicorn
    assert "%(m)s %(U)s" in gunicorn


def test_deployment_verifier_checks_actual_docker_port_bindings():
    verifier = (ROOT / "deploy" / "verify-deployment.sh").read_text(encoding="utf-8")

    assert "docker compose port db 5432" not in verifier
    assert 'index .NetworkSettings.Ports "5432/tcp"' in verifier
    assert 'index .NetworkSettings.Ports "8000/tcp"' in verifier
    assert 'test "$db_bindings" = "null"' in verifier
    assert 'test "$web_bindings" = "null"' in verifier
    assert "check_theme_integrity --strict" in verifier
    assert 'eq .Destination "/app/var/themes"' in verifier
    assert 'eq .Destination "/srv/themes"' in verifier
    assert "/static/themes/aurora-ledger/theme.css" in verifier
    assert "/themes/not-installed/theme.css" in verifier


def test_backup_scheduler_uses_application_timezone_and_expected_boundaries():
    zone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 7, 13, 2, 30, tzinfo=zone)

    assert next_occurrence(now, parse_clock("02:30")) == datetime(2026, 7, 14, 2, 30, tzinfo=zone)
    assert next_occurrence(now, parse_clock("03:00"), weekday=6) == datetime(
        2026, 7, 19, 3, 0, tzinfo=zone
    )
