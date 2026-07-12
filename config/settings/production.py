import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403


def required_setting(name: str) -> str:
    value = os.environ.get(name)
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
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
