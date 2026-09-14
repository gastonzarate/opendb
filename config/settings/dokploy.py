"""Production behind Dokploy's trusted TLS ingress and the private gateway."""

from django.core.exceptions import ImproperlyConfigured
from psycopg.conninfo import conninfo_to_dict
from psycopg.conninfo import make_conninfo

from .production import *  # noqa: F403
from .production import DATABASES
from .production import env

OPENDB_DOMAIN = env("OPENDB_DOMAIN")
if not OPENDB_DOMAIN or any(char in OPENDB_DOMAIN for char in "/:* ,\t\r\n"):
    message = "OPENDB_DOMAIN must be a single DNS hostname."
    raise ImproperlyConfigured(message)
DEBUG = False
ALLOWED_HOSTS = [OPENDB_DOMAIN]
CSRF_TRUSTED_ORIGINS = [f"https://{OPENDB_DOMAIN}"]
OPENDB_MCP_BASE_URL = f"https://{OPENDB_DOMAIN}"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
# ASGI requests should not retain synchronous connections between requests.
DATABASES["default"]["CONN_MAX_AGE"] = 0
RDS_CA = "/etc/ssl/certs/aws-rds-global-bundle.pem"
DATABASES["default"].setdefault("OPTIONS", {}).update(
    sslmode="verify-full",
    sslrootcert=RDS_CA,
    connect_timeout=10,
)
# These parameters also propagate to derived per-user database connections.
OPENDB_ADMIN_DSN = make_conninfo(
    env("OPENDB_ADMIN_DSN"),
    sslmode="verify-full",
    sslrootcert=RDS_CA,
)
if conninfo_to_dict(OPENDB_ADMIN_DSN).get("user") == DATABASES["default"]["USER"]:
    message = "Control and administrative database roles must differ."
    raise ImproperlyConfigured(message)
OPENDB_MCP_STATE_DIRECTORY = "/state/oauth"
