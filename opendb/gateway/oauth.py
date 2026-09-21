"""Fail-closed Google OAuth configuration with encrypted persistent state."""

import time
from pathlib import Path
from urllib.parse import urlsplit

from anyio import to_thread
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections
from django.db import connections
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.filetree import FileTreeStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from .identity import validate_google_claims
from .tokens import PAT_CLAIM
from .tokens import PAT_PREFIX
from .tokens import resolve_personal_access_token

MIN_SIGNING_KEY_LENGTH = 32
MCP_ACCESS_TOKEN_EXPIRY_SECONDS = 15 * 24 * 60 * 60
# Personal access tokens are revoked in the database, not by expiry; this only
# bounds how long a single fastmcp AccessToken object stays valid in memory.
PAT_ACCESS_TOKEN_TTL_SECONDS = 15 * 24 * 60 * 60


def required_setting(name):
    value = getattr(settings, name, None)
    if not isinstance(value, str) or not value.strip():
        msg = f"{name} must be configured for Google MCP authentication."
        raise ImproperlyConfigured(msg)
    return value


def verified_token_claims(token):
    """Check upstream Google audience and identity after SDK token validation."""
    if token is None or token.expires_at is None or token.expires_at <= time.time():
        msg = "Google authentication required."
        raise PermissionDenied(msg)
    if PAT_CLAIM in token.claims:
        # Already resolved to a concrete, non-revoked local user by
        # _load_personal_access_token; no Google-specific claim to check.
        return token.claims
    if token.claims.get("aud") != required_setting("OPENDB_GOOGLE_CLIENT_ID"):
        msg = "Google authentication required."
        raise PermissionDenied(msg)
    validate_google_claims(token.claims)
    return token.claims


def _resolve_personal_access_token_sync(raw_token):
    close_old_connections()
    try:
        return resolve_personal_access_token(raw_token)
    finally:
        connections.close_all()


async def _load_personal_access_token(token):
    """Local-login-only alternative to Google for the MCP bearer token."""
    if not getattr(settings, "OPENDB_LOCAL_LOGIN_ENABLED", False):
        return None
    if not isinstance(token, str) or not token.startswith(PAT_PREFIX):
        # Avoid a thread-pool round trip for every ordinary Google token.
        return None
    user_id = await to_thread.run_sync(_resolve_personal_access_token_sync, token)
    if user_id is None:
        return None
    return AccessToken(
        token=token,
        client_id="opendb-personal-access-token",
        scopes=[],
        expires_at=int(time.time()) + PAT_ACCESS_TOKEN_TTL_SECONDS,
        claims={PAT_CLAIM: user_id},
    )


class OpenDBGoogleProvider(GoogleProvider):
    """GoogleProvider additionally bound to OpenDB's OAuth client audience.

    Also accepts a personal access token as a non-Google bearer credential,
    but only when OPENDB_LOCAL_LOGIN_ENABLED is set; production deployments
    never enable that flag, so this stays Google-only there.
    """

    async def load_access_token(self, token):
        pat_access_token = await _load_personal_access_token(token)
        if pat_access_token is not None:
            return pat_access_token
        validated = await super().load_access_token(token)
        try:
            verified_token_claims(validated)
        except PermissionDenied:
            return None
        return validated


def build_state_store():
    """Persist OAuth values as encrypted JSON under a private mounted directory."""
    try:
        fernet = Fernet(required_setting("OPENDB_MCP_STORAGE_ENCRYPTION_KEY").encode())
    except (ValueError, TypeError) as exc:
        msg = "OPENDB_MCP_STORAGE_ENCRYPTION_KEY must be a Fernet key."
        raise ImproperlyConfigured(msg) from exc
    directory = Path(required_setting("OPENDB_MCP_STATE_DIRECTORY"))
    if not directory.is_absolute() or directory.is_symlink():
        msg = "OPENDB_MCP_STATE_DIRECTORY must be an absolute non-symlink path."
        raise ImproperlyConfigured(msg)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        msg = "OPENDB_MCP_STATE_DIRECTORY must have mode 0700."
        raise ImproperlyConfigured(msg)
    return FernetEncryptionWrapper(
        key_value=FileTreeStore(data_directory=directory),
        fernet=fernet,
    )


def build_google_provider():
    """Construct the production auth provider; no missing-config fallback exists."""
    client_id = required_setting("OPENDB_GOOGLE_CLIENT_ID")
    client_secret = required_setting("OPENDB_GOOGLE_CLIENT_SECRET")
    base_url = required_setting("OPENDB_MCP_BASE_URL").rstrip("/")
    parsed = urlsplit(base_url)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
        or not (
            parsed.scheme == "https"
            or (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        msg = "OPENDB_MCP_BASE_URL must be an HTTPS origin (HTTP only on loopback)."
        raise ImproperlyConfigured(msg)
    signing_key = required_setting("OPENDB_MCP_JWT_SIGNING_KEY")
    if len(signing_key) < MIN_SIGNING_KEY_LENGTH:
        msg = "OPENDB_MCP_JWT_SIGNING_KEY must contain at least 32 characters."
        raise ImproperlyConfigured(msg)
    redirects = getattr(
        settings,
        "OPENDB_MCP_ALLOWED_CLIENT_REDIRECT_URIS",
        [
            "http://localhost:*/*",
            "http://127.0.0.1:*/*",
        ],
    )
    if (
        not isinstance(redirects, list)
        or not redirects
        or any(
            not isinstance(uri, str) or not uri.startswith(("http://", "https://"))
            for uri in redirects
        )
    ):
        msg = "OPENDB_MCP_ALLOWED_CLIENT_REDIRECT_URIS must be a nonempty URI list."
        raise ImproperlyConfigured(msg)
    return OpenDBGoogleProvider(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        redirect_path="/auth/callback",
        required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
        jwt_signing_key=signing_key,
        client_storage=build_state_store(),
        allowed_client_redirect_uris=redirects,
        require_authorization_consent=True,
        fastmcp_access_token_expiry_seconds=MCP_ACCESS_TOKEN_EXPIRY_SECONDS,
        enable_cimd=False,
    )
