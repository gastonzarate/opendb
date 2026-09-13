import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

MIN_KEY_LENGTH = 32


def derive_database_password(database_id, identity="owner"):
    key = settings.OPENDB_DB_CREDENTIAL_KEY
    if not key or len(key) < MIN_KEY_LENGTH:
        msg = "OPENDB_DB_CREDENTIAL_KEY must contain at least 32 characters"
        raise ImproperlyConfigured(msg)
    return hmac.new(
        key.encode(), f"{database_id}:{identity}".encode(), hashlib.sha256
    ).hexdigest()
