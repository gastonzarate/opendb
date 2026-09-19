# Dokploy production deployment

## Packaging and rollout status

Use `docker-compose.dokploy.yml` alone from the repository root. This packages
one Node 24 + uv/Python 3.14 image, reused by web, MCP, indexer and initialization
jobs. The frontend is built in an isolated stage and copied into Django static
assets. `compose/dokploy/Dockerfile.dockerignore` allowlists the build context;
explicit COPY instructions exclude local `.envs`, `.models`, media and caches.
No deployment credentials, signed URLs or model bytes are baked into the image.

**Deployment is blocked (operator report, 2026-09-14):** the target's 150 GB root
filesystem is full and Dokploy's Swarm manager cannot initialize because its WAL
reports unexpected EOF. Existing Macaco Compose applications remain running.
Do not build remotely, restart Docker or reinitialize Swarm until the responsible
operator resolves disk capacity and authorizes recovery. The main agent reports RDS PostgreSQL 17.11 available with vector 0.8.2,
verified TLS through an SSH tunnel and completed control-role bootstrap.
Application migrations/provisioning remain to be smoke-tested by the main agent. Domain choice/DNS are pending; `opendb.macaco.ai` is
only a candidate, never a configured default. The main agent has prepared an ignored mode-0600 production environment with
the provisional domain and verified private model URL. Packaging validation does
not read this file. These files do not deploy anything.

Implementation sequence: inspect existing app contracts; package shared image
and isolated services; enforce TLS/settings/model integrity; validate locally with
dummy inputs; hand runtime prerequisites to the deployment owner. All packaging
changes stay in `compose/dokploy/`, the standalone Compose file, the Dokploy
settings module and this document. No changes to existing deployment variants.

## Topology and startup

- Graviton m6g.xlarge: all services select `linux/arm64`. Web uses two Gunicorn
  ASGI workers on 8000; MCP uses one Uvicorn process on 8001; indexer runs the
  existing continuous worker loop. Embeddings use four CPU threads.
- Only gateway joins external `dokploy-network`. Every service joins the private
  project `app` bridge. This bridge intentionally is **not** `internal: true`:
  outbound DNS, HTTPS (Google/model download) and RDS connectivity must work.
  No service publishes host ports. Other containers on `dokploy-network` are
  trusted ingress peers; do not attach Django or MCP directly to that network.
- nginx listens on 8080, preserves the host and trusted ingress HTTPS scheme,
  routes `/mcp`, `/.well-known`, `/authorize`, `/token`, `/register`, `/consent`
  and `/auth/callback` (including subpaths) to MCP, and everything else to web.
  Proxy buffering and OAuth access logging are disabled. Docker DNS is resolved
  dynamically so web/MCP replacement does not leave stale nginx upstream IPs.
- Web starts with `migrate`, then `collectstatic`, then Gunicorn. Redis must be
  healthy first. Web health requires an actual 200 from `/api/bootstrap/`, using
  the configured Host and HTTPS proxy header; redirects are rejected. Existing
  `ATOMIC_REQUESTS` means bootstrap also needs a functioning control database.
- MCP and indexer wait for healthy web. A root initialization job with only volume
  ownership capabilities prepares UID/GID 10001 ownership and OAuth mode 0700.
  All Python application processes and model download run as UID/GID 10001.
- Model initialization verifies existing bytes or downloads a direct HTTPS URL
  into a temporary file, verifies SHA256 and atomically renames it. Redirects
  are rejected, failures are redacted, and partial downloads are removed. An
  expired URL fails closed unless the volume already contains the valid model.
  Replace/refresh the URL and rerun initialization when needed.
- Embeddings and indexer both wait for successful model initialization and mount
  the model volume read-only. The indexer additionally waits for embeddings
  `/health` and independently verifies `--model-file
  /models/granite-97m-r2-q8_0.gguf`. Redis is an ephemeral cache capped at 256 MB.

## Environment contract

Set values in the new application's Dokploy environment. Do not copy another
application's environment, commit a populated env file or print resolved Compose
configuration with real values. Both DSNs must use the RDS DNS endpoint (not an IP)
and URI-encoded credentials; PostgreSQL keywords are also accepted for admin DSN.

| Variable | Contract |
| --- | --- |
| `OPENDB_DOMAIN` | Required single DNS hostname, no scheme/path/port. Drives allowed Host, HTTPS CSRF origin, MCP public origin and health Host. |
| `DJANGO_SECRET_KEY` | Required strong persistent Django secret. |
| `DATABASE_URL` | Required control-role PostgreSQL URL for the precreated OpenDB control database; this role owns migrations, not personal database administration. |
| `OPENDB_ADMIN_DSN` | Required separate administrative role/database DSN used for personal provisioning; must differ from the control role. |
| `OPENDB_DB_CREDENTIAL_KEY` | Required stable random secret of at least 32 characters. Used to derive per-user role passwords; rotation needs a credential migration. |
| `OPENDB_GOOGLE_CLIENT_ID`, `OPENDB_GOOGLE_CLIENT_SECRET` | Required Google OAuth web client credentials. |
| `OPENDB_MCP_JWT_SIGNING_KEY` | Required stable random signing secret of at least 32 characters. |
| `OPENDB_MCP_STORAGE_ENCRYPTION_KEY` | Required stable Fernet key (URL-safe base64 of 32 random bytes). Back up with MCP state; losing it makes state unreadable. |
| `OPENDB_MCP_ALLOWED_CLIENT_REDIRECT_URIS` | Required comma-separated client callback allowlist. Select approved clients; localhost wildcard callbacks are supported by existing app validation. Hosted Claude surfaces (claude.ai web, Desktop, mobile, Cowork) additionally need the exact `https://claude.ai/api/mcp/auth_callback`, otherwise their dynamic registration fails with `invalid_redirect_uri`. |
| `OPENDB_MODEL_URL` | Private presigned direct HTTPS URL required on first model initialization or invalid/missing cache. May be empty once the valid named volume exists. Supply through Dokploy, never a build argument or command line. Main agent supplies it independently. |
| `OPENDB_IMAGE` | Optional local image tag, default `opendb-dokploy:local`; use a unique release tag when building. All app services reuse it with `pull_policy: never`. |
| `DJANGO_ADMIN_URL` | Optional admin path, default `admin/`. |
| `WEB_CONCURRENCY` | Optional Gunicorn worker count, default 2; keep a single web service replica for startup migrations. |

The Compose file fixes `DJANGO_SETTINGS_MODULE=config.settings.dokploy`, disables
reading dotenv files, uses Redis at `redis://redis:6379/0`, and sets embeddings to
`http://embeddings:8080`. State is fixed at `/state/oauth`. Proxy trust applies only
inside the private app bridge. Production settings force HTTPS redirects, secure
cookies and certificate verification regardless of DSN query overrides. ASGI
request connections use `CONN_MAX_AGE=0`. HSTS remains 60 seconds, without subdomain
coverage or preload; Django deployment checks therefore report W005 and W021.

Both control and admin/per-user connections force `sslmode=verify-full` and
`sslrootcert=/etc/ssl/certs/aws-rds-global-bundle.pem`. The image downloads the
public [official AWS RDS CA bundle](https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem)
over verified HTTPS during build. Refresh the image for CA updates. The bundle is
public trust material, not a secret.

## Model and ARM64 provenance

The exact local artifact was hashed during packaging validation:

```text
granite-97m-r2-q8_0.gguf
d0aefc589e25df26b75d45eaa3b7205cad850ceeaaef82c6b98b04b166b992d4
```

The [official llama.cpp CPU server documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md)
lists ARM64 support. Registry inspection on 2026-09-14 confirmed that the existing
pinned index **is multi-platform**, not amd64-only:

```text
image: ghcr.io/ggml-org/llama.cpp
index: sha256:cbcdcb52d484e08e23bfc0135afa5beadd2d540513bbb7c65b233231fa033ff4
linux/arm64: sha256:47ed45d994cc4f568eda73c328b3d4eae6974ab30395e2fd334369925aa5f96b
version: b10920
revision: eafe15a5e3d87dd68ae33acf6a7cbd9415a0ac5e
entrypoint: /app/llama-server
```

The ARM64 image config includes curl for the healthcheck. The Compose index pin
plus `platform: linux/arm64` selects that exact child. Actual Graviton execution,
model inference and capacity under concurrent user load remain runtime checks.

## Operator steps after blockers are resolved

1. Finish RDS PostgreSQL 17 bootstrap: control database/role, separate admin role,
   private routing/security groups, CA verification and pgvector availability.
   Prove app provisioning against RDS: role/database creation, ownership comments,
   role membership/SET ROLE as needed by PG17, schema ownership, vector extension,
   user credentials and deletion. RDS privileges differ from a local superuser.
2. Set the environment above. Configure Google callbacks at
   `https://<OPENDB_DOMAIN>/accounts/google/login/callback/` and
   `https://<OPENDB_DOMAIN>/auth/callback`. Confirm the actual domain/DNS/TLS first.
3. Use the standalone Compose file in Dokploy **Compose mode**, with repository
   build context at the root. This uses Compose dependency completion semantics;
   do not substitute `docker stack deploy`. Build only `web` once using
   `docker compose -f docker-compose.dokploy.yml build web`, then start the same
   project with `docker compose -f docker-compose.dokploy.yml up -d --no-build`.
   Dokploy must preserve the locally built image tag for the reuse services.
4. Add the [Dokploy Compose domain](https://docs.dokploy.com/docs/core/docker-compose/domains)
   with `serviceName=gateway`, `port=8080`, path `/`, HTTPS enabled, and
   `host=<OPENDB_DOMAIN>`. Do not add domains or published ports to backend services.
   Confirm generated routing attaches only gateway to `dokploy-network`.
5. Verify migrations, health, public bootstrap/static files, Google login, MCP
   discovery/consent/token flow, persistent state after recreation and indexing
   of a provisioned personal database. Check dimension 384 with mean pooling and
   context 512. Validate the presigned model download in the new deployment.

Keep one web, one MCP and one indexer replica initially. Compose startup ordering
is not ongoing dependency supervision: unhealthy dependencies do not automatically
restart dependents. Monitor restart loops, model/indexing lag, RDS connections,
CPU/RAM and disk capacity. Indexer output provides per-database progress/errors.

Back up RDS, `mcp_state` and stable encryption/signing/credential keys together.
The `models` volume is reproducible from the exact artifact and checksum. Keep the
Compose project name stable so named volumes survive release updates. Never use
`down -v` during normal deployment. Django media is not persisted by this packaging;
add an explicit storage plan before introducing uploads. SMTP delivery is not
configured here; Google login is the intended authentication path.

## Local validation

```sh
python3 compose/dokploy/validate.py
sh -n compose/dokploy/start-web
docker build --check --platform linux/arm64 -f compose/dokploy/Dockerfile .
docker build --platform linux/amd64 -t opendb-dokploy:packaging-check -f compose/dokploy/Dockerfile .
python3 compose/dokploy/validate.py opendb-dokploy:packaging-check
```

The validation script supplies dummy environment values, ignores Compose `.env`,
and checks network isolation, no published ports, one build/shared image,
initialization dependencies, read-only model mounts, syntax, model cache/download,
checksum failure cleanup and URL redaction. Optional image validation runs with
`--network none`, checks the actual production settings/CA/nonroot UID, collects
static assets and runs Gunicorn with a temporary SQLite override for HTTP checks.
This override exists only in the disposable validation container, never production.
It checks 200 bootstrap/HTML/JS and rejection of invalid Host. No pytest runs.

Local x86_64 image build, ARM64 Dockerfile checks, nginx syntax and offline checks
were executed during packaging. They do not prove an ARM64 runtime build, RDS TLS
handshake/migrations/provisioning, external OAuth, signed-URL download, Dokploy
routing or persistence across actual host failures. Those remain rollout checks.
