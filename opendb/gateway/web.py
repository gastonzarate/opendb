"""Public React shell and same-origin browser bootstrap."""

from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def app(request):
    return render(request, "pages/app.html")


@require_GET
def bootstrap(request):
    response = JsonResponse(
        {
            "csrf_token": get_token(request),
            "google_configured": bool(
                settings.OPENDB_GOOGLE_CLIENT_ID
                and settings.OPENDB_GOOGLE_CLIENT_SECRET
            ),
            "local_login_enabled": bool(settings.OPENDB_LOCAL_LOGIN_ENABLED),
            "mcp_url": settings.OPENDB_MCP_BASE_URL.rstrip("/") + "/mcp",
        }
    )
    response["Cache-Control"] = "no-store"
    return response
