import time

from django.db import DatabaseError

from .models import SystemPreference
from .themes import SAFE_DEFAULT_THEME_ID, get_theme_registry


def theme_context(request):
    """为所有页面提供不会因数据库或主题损坏而中断的主题上下文。"""
    defaults = {
        "active_theme_id": SAFE_DEFAULT_THEME_ID,
        "appearance_mode": SystemPreference.AppearanceMode.AUTO,
        "last_known_good_theme_id": SAFE_DEFAULT_THEME_ID,
        "reduce_motion": False,
        "show_theme_background": True,
    }
    try:
        preference = SystemPreference.objects.only(*defaults).get(pk=SystemPreference.SINGLETON_ID)
        values = {name: getattr(preference, name) for name in defaults}
    except (DatabaseError, SystemPreference.DoesNotExist):
        values = defaults

    registry = get_theme_registry()
    selection = registry.select(values["active_theme_id"], values["last_known_good_theme_id"])
    is_preview = False
    preview = request.session.get("theme_preview") if getattr(request, "user", None) else None
    if getattr(getattr(request, "user", None), "is_authenticated", False) and isinstance(
        preview, dict
    ):
        preview_theme = registry.get(preview.get("id", ""))
        if preview_theme is not None and preview.get("expires_at", 0) > int(time.time()):
            selection = registry.select(preview_theme.id, values["last_known_good_theme_id"])
            is_preview = True
        else:
            request.session.pop("theme_preview", None)
    theme_appearance = selection.theme.appearance
    preference_appearance = values["appearance_mode"]
    resolved_appearance = (
        preference_appearance.lower()
        if preference_appearance
        in {
            SystemPreference.AppearanceMode.DARK,
            SystemPreference.AppearanceMode.LIGHT,
        }
        else theme_appearance
        if theme_appearance in {"dark", "light"}
        else "light"
    )
    active_theme = {
        "cache_key": selection.theme.cache_key,
        "chart_theme": selection.theme.chart_theme,
        "fallback_reason": selection.fallback_reason,
        "id": selection.theme.id,
        "is_preview": is_preview,
        "name": selection.theme.name,
        "requested_id": selection.requested_id,
        "resolved_appearance": resolved_appearance,
        "revision": selection.theme.revision,
        "stylesheet_url": selection.theme.stylesheet_url,
        "version": selection.theme.version,
    }
    return {
        "active_theme": active_theme,
        "theme_preferences": {
            "appearance_mode": preference_appearance,
            "reduce_motion": values["reduce_motion"],
            "show_background": values["show_theme_background"],
        },
    }
