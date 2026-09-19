# Identity, API and MCP gateway

The gateway is a separate authenticated FastMCP service plus a Django session API.
It imports `opendb.databases.services.dispatch(actor_id, action, payload)` only at
request time. No gateway models or migrations are needed: identity is stored in
allauth's existing `SocialAccount` and `EmailAddress` tables.

## Parent integration checklist

### Dependencies

Tested against Python 3.14, Django 6.0.8, django-allauth 65.19.2,
FastMCP 3.4.7 and MCP SDK 1.30.0. Add/pin `fastmcp==3.4.7` and
`py-key-value-aio[filetree,wrappers-encryption]==0.4.5`; declare `cryptography`
as a direct dependency since the gateway imports Fernet. Existing `uvicorn`,
`asgiref`, and Django are used. Enable allauth's `socialaccount` extra alongside
`mfa`, or otherwise include its Google provider dependencies (`requests`, PyJWT,
cryptography). Tests use `pytest`, `pytest-django`, `httpx`, and AnyIO's pytest
plugin (included with FastMCP); `pytest-asyncio` is not required.

### Shared Django settings

Add `allauth.socialaccount.providers.google` to `INSTALLED_APPS`. Keep existing
allauth middleware, authentication backends and account/socialaccount apps.
The gateway itself need not be in `INSTALLED_APPS`.

```python
OPENDB_GOOGLE_CLIENT_ID = env("OPENDB_GOOGLE_CLIENT_ID", default="")
OPENDB_GOOGLE_CLIENT_SECRET = env("OPENDB_GOOGLE_CLIENT_SECRET", default="")
OPENDB_MCP_BASE_URL = env("OPENDB_MCP_BASE_URL", default="")
OPENDB_MCP_JWT_SIGNING_KEY = env("OPENDB_MCP_JWT_SIGNING_KEY", default="")
OPENDB_MCP_STORAGE_ENCRYPTION_KEY = env("OPENDB_MCP_STORAGE_ENCRYPTION_KEY", default="")
OPENDB_MCP_STATE_DIRECTORY = env("OPENDB_MCP_STATE_DIRECTORY", default="")
OPENDB_MCP_MAX_CONCURRENT_OPERATIONS = env.int(
    "OPENDB_MCP_MAX_CONCURRENT_OPERATIONS", default=4,
)
OPENDB_MCP_ALLOWED_CLIENT_REDIRECT_URIS = env.list(
    "OPENDB_MCP_ALLOWED_CLIENT_REDIRECT_URIS",
    default=["http://localhost:*/*", "http://127.0.0.1:*/*"],
)

SOCIALACCOUNT_ADAPTER = "opendb.gateway.adapters.GoogleSocialAccountAdapter"
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": OPENDB_GOOGLE_CLIENT_ID,
            "secret": OPENDB_GOOGLE_CLIENT_SECRET,
            "key": "",
        },
        "SCOPE": ["openid", "email", "profile"],
        "AUTH_PARAMS": {"access_type": "online"},
        "EMAIL_AUTHENTICATION": False,
    },
}
```

Do not simultaneously configure a second Google `SocialApp` in the database for
the same site/client. Keep `ACCOUNT_ALLOW_REGISTRATION` as the signup switch.
The gateway adapter rejects other social providers and all account-connect flows;
the API also rejects password-only sessions even when the user has a linked Google
account. If the entire website should be Google-only, parent can additionally set
`SOCIALACCOUNT_ONLY = True` and use an allauth Google login form on the root page.

For production HTTPS, set `SESSION_COOKIE_SECURE = True`, `CSRF_COOKIE_SECURE = True`,
keep HttpOnly cookies, configure `ALLOWED_HOSTS`, trusted proxy HTTPS headers and
`CSRF_TRUSTED_ORIGINS` for the actual web origin. The API is same-origin and does not
enable wildcard CORS. Allauth sign-in is POST with CSRF protection.

### URLs and launch commands

Retain `path("accounts/", include("allauth.urls"))`. Add:

```python
path("api/", include("opendb.gateway.urls")),
```

The Django app continues to use its existing ASGI/WSGI entrypoint. MCP runs separately:

```sh
DJANGO_SETTINGS_MODULE=config.settings.local python -m opendb.gateway --host 0.0.0.0 --port 8001
# Equivalent:
DJANGO_SETTINGS_MODULE=config.settings.local uvicorn opendb.gateway.asgi:create_app --factory --host 0.0.0.0 --port 8001
```

The module is `opendb.gateway`, **not** `opendb.gateway.server`. Standalone endpoints:
`/mcp`, `/auth/callback`, plus the SDK's OAuth discovery, registration, authorization,
consent, token and revocation routes. Use a separate origin for MCP and forward the
entire origin to this service, including `/.well-known/*`. The configured base URL
must be an origin without a path/query/userinfo; HTTPS is required except loopback.

### Local Compose addition

Adapt the existing Django image/env files to add the service below. Both services
need the same **control** database settings. `OPENDB_ADMIN_DSN` points to the separate
customer database cluster (`data-postgres`), never to the control database by default.
The gateway passes provisioning through the parent's services and does not construct
or override this DSN. Run Django migrations in the main web startup before using MCP.

```yaml
volumes:
  opendb_mcp_oauth: {}

services:
  mcp:
    image: opendb_local_django
    build:
      context: .
      dockerfile: ./compose/local/django/Dockerfile
    command: ["python", "-m", "opendb.gateway", "--host", "0.0.0.0", "--port", "8001"]
    environment:
      DJANGO_SETTINGS_MODULE: config.settings.local
      OPENDB_MCP_STATE_DIRECTORY: /var/lib/opendb/oauth/state
    env_file:
      - ./.envs/.local/.django
      - ./.envs/.local/.postgres
    volumes:
      - /app/.venv
      - .:/app:z
      - opendb_mcp_oauth:/var/lib/opendb/oauth
    ports:
      - "127.0.0.1:8001:8001"
    depends_on:
      - postgres
      - data-postgres
```

Put the required OpenDB settings in the env file/secret manager. The persistent
volume root must be writable by the service UID; the gateway creates its `state`
subdirectory as mode `0700` and refuses an existing group/world-accessible directory.
For a non-root production image initialize volume ownership for that UID. Keep this
MVP service to **one process/replica**: it uses local file storage and the OAuth SDK's
multi-step token/state operations have not been validated for concurrent replicas.

Within that process, MCP identity resolution, synchronous service dispatch and
contract-file reads use AnyIO's worker threads with a shared per-server capacity
limit (`OPENDB_MCP_MAX_CONCURRENT_OPERATIONS`, default **4**, positive integer).
A slow embedding request no longer serializes unrelated users' operations through
a single Django thread. Saturated capacity queues new work; this is not priority
scheduling for revocations. ORM connections are checked before work and closed in
the worker's `finally` block, including service errors. AnyIO's normal cancellation
shield keeps a running synchronous call attached until it finishes.

Generate a stable signing secret with `secrets.token_urlsafe(48)` and a separate
storage encryption key with `cryptography.fernet.Fernet.generate_key()`. Store both
outside source control and preserve them with the state volume across restarts.
Changing the signing key invalidates client tokens; changing the encryption key
makes existing OAuth state unreadable. Back up keys and encrypted state together.
Only values are encrypted; filenames/metadata live within the private directory.
The JSON file storage does not deserialize Python pickles. SDK client registrations,
transactions, codes and upstream tokens all use the supplied encrypted store. The
FastMCP-issued access JWT lasts **15 days**. It is only a reference token: every
request still validates the stored Google token and transparently refreshes it when
Google's shorter access token expires. If Google did not issue an upstream refresh
token, FastMCP caps the client-facing lifetime to the upstream expiry instead of
pretending the session can be renewed. FastMCP's local refresh-token fallback remains
one year, while Google revocation or expiry remains authoritative.

## API contract

`GET /api/session/` returns `{user: {id, email}, csrf_token}` for an active session
whose allauth Google authentication record matches that user's Google subject.
It returns JSON 401 otherwise. Responses use `Cache-Control: no-store`.

`POST /api/actions/<action>/` accepts the payload object directly as JSON and returns
`{result: ...}`. Send the session cookie and `X-CSRFToken` from `/api/session/`.
The action view opts out of `ATOMIC_REQUESTS` with `transaction.non_atomic_requests`:
services coordinate control and customer database changes and own their explicit
transaction boundaries. In particular, a sharing assignment must commit before
the service releases its sharing lock; an outer request transaction would delay it.
All fourteen original actions in `contracts.md` plus the parent's
`ingestion_history` action are exposed (15 tools). Identity fields `actor_id`,
`user_id` and `owner_id` are forbidden at the top level. The current actor is always
derived from the authenticated session. SQL parameters use `$1`, `$2`, etc.:

```json
{"database_id": "<uuid>", "sql": "SELECT $1::text AS value", "parameters": ["hello"]}
```

Authentication/permission errors are 401/403, unsupported action is 404, bad JSON
or service validation is 400, wrong content type is 415. Django's CSRF and method
errors may return its standard HTML response. Unexpected service failures return
a generic JSON 500; protected object names are not included in permission errors.

## Google OAuth setup and client connection

Create a Google OAuth **Web application** client and consent screen. Configure both
exact redirect URIs on that client:

- `http://localhost:8000/accounts/google/login/callback/` for the local web app.
- `http://localhost:8001/auth/callback` for the local MCP service.

Use corresponding HTTPS origins when deployed. Set the MCP base URL to the URL
the assistant actually reaches. Add test users to Google's consent screen when
the app is in testing mode. A remote assistant's own OAuth callback must also be
listed in `OPENDB_MCP_ALLOWED_CLIENT_REDIRECT_URIS`; that is distinct from the
Google callback above. CIMD fetching is disabled; SDK dynamic client registration
is enabled. The SDK's authorization-consent screen remains enabled.

Local clients such as Kiro, Claude Code or the FastMCP client use RFC 8252
loopback callbacks on ephemeral ports, which the localhost wildcards already
cover. The hosted Claude surfaces (claude.ai web, Claude Desktop, mobile and
Cowork) register from Anthropic's cloud with the single exact callback
`https://claude.ai/api/mcp/auth_callback`, per the
[connector authentication reference](https://claude.com/docs/connectors/building/authentication).
Without that entry `/register` answers `400 invalid_redirect_uri` and Claude only
reports that it could not register with the sign-in service, so keep it listed
whenever those clients must connect.

Connect a FastMCP client using `Client("http://localhost:8001/mcp", auth="oauth")`,
or configure the assistant's remote Streamable HTTP MCP URL with OAuth. Tool
instructions explain catalog-first modeling, structured ingestion, provenance,
unknown data, grants, and asynchronous vector status. There is no auth-off flag.

Every MCP tool returns the same `{result: ...}` envelope as the API. With the
FastMCP client, read `response.data["result"]`, including for list/null results.

Read `opendb://guides/ingestion` before ingesting: it provides a structural JSON
Schema, exact required field names, the peer validator's additional semantic rules,
and the normalized meeting example with meetings, people, participants and turns.
The resource directly calls `opendb.ingestion.operation_schema()` and
`opendb.ingestion.example_operation("meeting", "0" * 64)`; the gateway keeps no
independent schema or example. Speaker labels do not establish a person's identity:
the example's participants keep `person_id` NULL, preserving known turn order and
the one timestamp explicitly present in the source. Replace
the example fingerprint with `catalog`'s `result.fingerprint` and use a fresh
idempotency key for new content. The example is exercised through actual dispatch
against PostgreSQL, including idempotent retry. The peer's
`opendb.ingestion.validation.validate_operation` remains authoritative for parsed
types, cross-record references, limits and SQL policy; the schema is not an
independent authorization or SQL validator.

`ingestion_history` accepts `{database_id, operation_id?: UUID}`. The parent service
restricts it to the owner. Listing returns recent operation IDs, source names and
results; requesting an ID returns its original source for provenance review.

The implementation is based on the official
[FastMCP GoogleProvider guide](https://gofastmcp.com/integrations/google),
[OAuth proxy documentation](https://gofastmcp.com/servers/auth/oauth-proxy), and
[allauth configuration](https://docs.allauth.org/en/latest/socialaccount/configuration.html),
with installed SDK source inspected for the pinned versions.

## Verification and limits

Run the owned tests with the parent-provided disposable PostgreSQL environment:

```sh
/tmp/opendb-mvp-venv/bin/pytest --ds=config.settings.integration tests/test_gateway*.py -q -p no:cacheprovider
```

Verification on 2026-09-13: 67 gateway tests passed against the parent runner;
Ruff check and format passed for all owned Python files. Coverage includes identity
collisions and inactive accounts, API CSRF/session enforcement, the real allauth
Google callback using mocked HTTP responses, actual SDK tools/resources,
unauthenticated HTTP rejection and discovery, encrypted OAuth registration
persistence, ORM work off the async thread, shared real service dispatch,
cross-owner denial, and the authoritative guide's normalized ingestion/retry
example. Regression tests cover revocation for another actor during slow work,
bounded worker concurrency, connection cleanup on success/error, contract loading
off the event loop, and API autocommit with `ATOMIC_REQUESTS=True`.

Tests use fabricated Google claims and local dependency overrides; no real Google
secrets are needed. Live Google/browser consent and real assistant connectivity
have **not** been verified. They require user-provided Google OAuth credentials,
registered callback URLs, consent-screen access, persistent keys/storage and a
reachable service. Passing tests do not establish that unconfigured Google login
works. Email changes on an existing Google subject do not silently update the
local email; account-linking and email-change recovery are outside this MVP.

All changes stay under `opendb/gateway/**`, `tests/test_gateway*.py`, and this document.
No commits or shared configuration changes are made by this worker.
