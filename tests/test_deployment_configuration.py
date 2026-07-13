from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from scripts.backup_scheduler import next_occurrence, parse_clock

ROOT = Path(__file__).resolve().parents[1]


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
    assert any("/runtime" in row for row in services["backup"]["tmpfs"])
    for service in services.values():
        assert service["logging"]["options"] == {"max-size": "10m", "max-file": "5"}
        assert "no-new-privileges:true" in service["security_opt"]
    assert services["web"]["cap_drop"] == ["ALL"]
    assert services["backup"]["cap_drop"] == ["ALL"]


def test_container_images_are_pinned_and_web_runs_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose_text = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "python:3.13.9-slim-bookworm" in dockerfile
    assert "postgres:17.6-bookworm" in dockerfile
    assert "caddy:2.10.2-alpine" in dockerfile
    assert "postgres:17.6-alpine3.22" in compose_text
    assert ":latest" not in dockerfile + compose_text
    assert "USER 10001:10001" in dockerfile
    assert "headers={'Host':host,'X-Forwarded-Proto':'https'}" in dockerfile
    assert "Docker Socket" not in compose_text
    assert "privileged:" not in compose_text


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
    assert "respond @health 404" in caddyfile
    assert "\n  log" not in caddyfile
    assert "%(q)s" not in gunicorn
    assert "%(m)s %(U)s" in gunicorn


def test_backup_scheduler_uses_application_timezone_and_expected_boundaries():
    zone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 7, 13, 2, 30, tzinfo=zone)

    assert next_occurrence(now, parse_clock("02:30")) == datetime(2026, 7, 14, 2, 30, tzinfo=zone)
    assert next_occurrence(now, parse_clock("03:00"), weekday=6) == datetime(
        2026, 7, 19, 3, 0, tzinfo=zone
    )
