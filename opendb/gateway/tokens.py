"""Personal access tokens: local-login-only bearer credentials for MCP.

An alternative to Google OAuth for the MCP gateway, gated end-to-end by
OPENDB_LOCAL_LOGIN_ENABLED. Only a salted hash is ever persisted; the raw
value is generated here and returned to the caller exactly once.
"""

import hashlib
import secrets

from django.utils import timezone

from opendb.users.models import PersonalAccessToken

PAT_PREFIX = "odbpat_"
PAT_SECRET_BYTES = 32
PAT_PREFIX_DISPLAY_LENGTH = len(PAT_PREFIX) + 6
PAT_CLAIM = "opendb_personal_access_token_user_id"


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_personal_access_token(user, name=""):
    """Create and persist a new token for user; return (token row, raw value)."""
    raw_token = PAT_PREFIX + secrets.token_urlsafe(PAT_SECRET_BYTES)
    token = PersonalAccessToken.objects.create(
        user=user,
        name=name[:100] if isinstance(name, str) else "",
        token_hash=_hash_token(raw_token),
        prefix=raw_token[:PAT_PREFIX_DISPLAY_LENGTH],
    )
    return token, raw_token


def resolve_personal_access_token(raw_token):
    """Resolve a bearer string to an active user id, or None. Synchronous ORM access."""
    if not isinstance(raw_token, str) or not raw_token.startswith(PAT_PREFIX):
        return None
    token = (
        PersonalAccessToken.objects.select_related("user")
        .filter(token_hash=_hash_token(raw_token), revoked_at__isnull=True)
        .first()
    )
    if token is None or not token.user.is_active:
        return None
    PersonalAccessToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
    return token.user_id
