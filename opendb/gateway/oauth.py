"""Fail-closed Google OAuth configuration with encrypted persistent state."""

import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import PermissionDenied
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.filetree import FileTreeStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from .identity import validate_google_claims

MIN_SIGNING_KEY_LENGTH = 32
MCP_ACCESS_TOKEN_EXPIRY_SECONDS = 15 * 24 * 60 * 60


def required_setting(name):
    value = getattr(settings, name, None)
    if not isinstance(value, str) or not value.strip():
        msg = f"{name} must be configured for Google MCP authentication."
        raise ImproperlyConfigured(msg)
    return value


def verified_token_claims(token):
    """Check upstream Google audience and identity after SDK token validation."""
    if (
        token is None
        or token.claims.get("aud") != required_setting("OPENDB_GOOGLE_CLIENT_ID")
        or token.expires_at is None
        or token.expires_at <= time.time()
    ):
        msg = "Google authentication required."
        raise PermissionDenied(msg)
    validate_google_claims(token.claims)
    return token.claims


class OpenDBGoogleProvider(GoogleProvider):
    """GoogleProvider additionally bound to OpenDB's OAuth client audience."""

    async def load_access_token(self, token):
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
