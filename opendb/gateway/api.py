"""Same-origin session API; all writes retain Django CSRF protection.

Accepts either a Google login session, or (local development only, gated by
OPENDB_LOCAL_LOGIN_ENABLED) an email+password login session.
"""

import json
import logging

from allauth.account.authentication import get_authentication_records
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.http import RawPostDataException
from django.http import UnreadablePostError
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from opendb.users.models import PersonalAccessToken

from .contract import ACTIONS
from .contract import WEB_ONLY_ACTIONS
from .contract import dispatch
from .contract import validate_payload
from .tokens import generate_personal_access_token

logger = logging.getLogger(__name__)


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _error(code, message, status):
    return _json({"error": {"code": code, "message": message}}, status)


def _has_google_login_evidence(request, user):
    subjects = [
        record.get("uid")
        for record in get_authentication_records(request)
        if record.get("method") == "socialaccount"
        and record.get("provider") == "google"
    ]
    return SocialAccount.objects.filter(
        user=user, provider="google", uid__in=subjects
    ).exists()


def _has_local_password_login_evidence(request):
    if not getattr(settings, "OPENDB_LOCAL_LOGIN_ENABLED", False):
        return False
    return any(
        record.get("method") == "password"
        for record in get_authentication_records(request)
    )


def session_actor(request):
    """Require an active user and matching allauth login evidence."""
    user = request.user
    if not user.is_authenticated or not user.is_active:
        return None
    if not _has_google_login_evidence(request, user) and not (
        _has_local_password_login_evidence(request)
    ):
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


def _local_login_gate(request):
    """Personal access tokens are a local-development feature; hide it elsewhere."""
    if not getattr(settings, "OPENDB_LOCAL_LOGIN_ENABLED", False):
        return _error("not_found", "Unknown endpoint.", 404)
    return None


@require_GET
def personal_access_tokens(request):
    disabled = _local_login_gate(request)
    if disabled is not None:
        return disabled
    actor_id = session_actor(request)
    if actor_id is None:
        return _error("authentication_required", "Sign in.", 401)
    tokens = PersonalAccessToken.objects.filter(user_id=actor_id).values(
        "id", "name", "prefix", "created_at", "last_used_at", "revoked_at"
    )
    return _json({"tokens": list(tokens)})


@transaction.non_atomic_requests
@require_POST
@csrf_protect
def create_personal_access_token(request):
    disabled = _local_login_gate(request)
    if disabled is not None:
        return disabled
    actor_id = session_actor(request)
    if actor_id is None:
        return _error("authentication_required", "Sign in.", 401)
    try:
        payload = json.loads(request.body) if request.body else {}
    except (ValueError, UnicodeError, RawPostDataException, UnreadablePostError):
        return _error("invalid_payload", "Send a JSON object.", 400)
    if not isinstance(payload, dict) or not isinstance(payload.get("name", ""), str):
        return _error("invalid_payload", "name must be a string.", 400)
    token, raw_token = generate_personal_access_token(
        request.user, name=payload.get("name", "")
    )
    return _json(
        {
            "token": {
                "id": token.pk,
                "name": token.name,
                "prefix": token.prefix,
                "created_at": token.created_at,
            },
            "raw_token": raw_token,
        },
        status=201,
    )


@transaction.non_atomic_requests
@require_POST
@csrf_protect
def revoke_personal_access_token(request, token_id):
    disabled = _local_login_gate(request)
    if disabled is not None:
        return disabled
    actor_id = session_actor(request)
    if actor_id is None:
        return _error("authentication_required", "Sign in.", 401)
    updated = PersonalAccessToken.objects.filter(
        pk=token_id, user_id=actor_id, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())
    if not updated:
        return _error("not_found", "Token not found.", 404)
    return _json({"result": "revoked"})


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
    except (ValueError, UnicodeError, RawPostDataException, UnreadablePostError):
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
    except (KeyError, TypeError, ValidationError):
        return _error("invalid_operation", "Missing or invalid operation field.", 400)
    except Exception:
        logger.exception("Gateway service action failed: %s", action)
        return _error("service_error", "The operation could not be completed.", 500)
    return _json({"result": result})
