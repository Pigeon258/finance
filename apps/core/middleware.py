from django.contrib import auth
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from .services import get_display_time_zone, get_session_limits

SESSION_CREATED_AT = "owner_session_created_at"
SESSION_LAST_ACTIVITY_AT = "owner_session_last_activity_at"


class SingleUserSessionMiddleware:
    PUBLIC_PATH_NAMES = {"core:health-live", "core:health-ready", "core:login"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path_name = self._resolve_path_name(request.path_info)
        if path_name in self.PUBLIC_PATH_NAMES:
            return self.get_response(request)

        if not request.user.is_authenticated:
            return redirect(f"{reverse('core:login')}?next={request.get_full_path()}")

        if self._session_expired(request):
            auth.logout(request)
            return redirect(reverse("core:login"))

        timezone.activate(get_display_time_zone())
        return self.get_response(request)

    @staticmethod
    def _resolve_path_name(path_info):
        from django.urls import Resolver404, resolve

        try:
            return resolve(path_info).view_name
        except Resolver404:
            return None

    @staticmethod
    def _session_expired(request) -> bool:
        now_timestamp = int(timezone.now().timestamp())
        created_at = request.session.get(SESSION_CREATED_AT)
        last_activity_at = request.session.get(SESSION_LAST_ACTIVITY_AT)
        if created_at is None or last_activity_at is None:
            request.session[SESSION_CREATED_AT] = now_timestamp
            request.session[SESSION_LAST_ACTIVITY_AT] = now_timestamp
            return False

        idle_limit, absolute_limit = get_session_limits()
        if now_timestamp - int(last_activity_at) > idle_limit:
            return True
        if now_timestamp - int(created_at) > absolute_limit:
            return True

        request.session[SESSION_LAST_ACTIVITY_AT] = now_timestamp
        return False
