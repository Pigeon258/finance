import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403


def required_setting(name: str) -> str:
    value = os.environ.get(name)
    if not value and os.environ.get(f"{name}_FILE"):
        value = Path(os.environ[f"{name}_FILE"]).read_text(encoding="utf-8").strip()
    if not value:
        raise ImproperlyConfigured(f"Missing required environment setting: {name}")
    return value


SECRET_KEY = required_setting("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = [host.strip() for host in required_setting("DJANGO_ALLOWED_HOSTS").split(",")]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_COOKIE_HTTPONLY = True

FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
