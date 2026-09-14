"""Same-origin Google session API; all writes retain Django CSRF protection."""

import json
import logging

from allauth.account.authentication import get_authentication_records
from allauth.socialaccount.models import SocialAccount
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.http import RawPostDataException
from django.http import UnreadablePostError
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from .contract import ACTIONS
from .contract import WEB_ONLY_ACTIONS
from .contract import dispatch
from .contract import validate_payload

logger = logging.getLogger(__name__)


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _error(code, message, status):
    return _json({"error": {"code": code, "message": message}}, status)


def session_actor(request):
    """Require an active user and matching allauth Google login evidence."""
    user = request.user
    if not user.is_authenticated or not user.is_active:
        return None
    subjects = [
        record.get("uid")
        for record in get_authentication_records(request)
        if record.get("method") == "socialaccount"
        and record.get("provider") == "google"
    ]
    if not SocialAccount.objects.filter(
        user=user, provider="google", uid__in=subjects
    ).exists():
        return None
    return user.pk


@require_GET
def session(request):
    if session_actor(request) is None:
        return _error("authentication_required", "Sign in with Google.", 401)
    return _json(
        {
            "user": {"id": request.user.pk, "email": request.user.email},
            "csrf_token": get_token(request),
        }
    )


@transaction.non_atomic_requests
@require_POST
@csrf_protect
def action(request, action):  # noqa: PLR0911 -- Explicit HTTP failure responses.
    actor_id = session_actor(request)
    if actor_id is None:
        return _error("authentication_required", "Sign in with Google.", 401)
    if action not in ACTIONS and action not in WEB_ONLY_ACTIONS:
        return _error("unknown_action", "Unknown action.", 404)
    if request.content_type != "application/json":
        return _error("invalid_content_type", "Send application/json.", 415)
    try:
        payload = json.loads(request.body)
        validate_payload(payload)
    except ValueError, UnicodeError, RawPostDataException, UnreadablePostError:
        return _error(
            "invalid_payload", "Send a JSON object without actor identity fields.", 400
        )
    try:
        result = dispatch(actor_id, action, payload)
    except PermissionDenied:
        return _error(
            "permission_denied", "You do not have permission for this operation.", 403
        )
    except ObjectDoesNotExist:
        return _error("not_found", "Requested resource was not found.", 404)
    except ValueError as exc:
        return _error("invalid_operation", str(exc), 400)
    except KeyError, TypeError, ValidationError:
        return _error("invalid_operation", "Missing or invalid operation field.", 400)
    except Exception:
        logger.exception("Gateway service action failed: %s", action)
        return _error("service_error", "The operation could not be completed.", 500)
    return _json({"result": result})
