from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def home(request: HttpRequest):
    return render(request, "core/home.html")


@require_GET
def health_live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def health_ready(request: HttpRequest) -> JsonResponse:
    connection = connections["default"]
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        executor = MigrationExecutor(connection)
        has_unapplied_migrations = bool(
            executor.migration_plan(executor.loader.graph.leaf_nodes())
        )
    except Exception:  # Health checks must not expose database or migration details.
        return JsonResponse({"status": "unavailable"}, status=503)

    if has_unapplied_migrations:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
