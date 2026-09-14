# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS dependencies
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

FROM python:3.14-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.dokploy DJANGO_READ_DOT_ENV_FILE=false \
    PGSSLROOTCERT=/etc/ssl/certs/aws-rds-global-bundle.pem PGSSLMODE=verify-full
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 ca-certificates curl \
    && curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
       https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
       -o /etc/ssl/certs/aws-rds-global-bundle.pem \
    && chmod 0644 /etc/ssl/certs/aws-rds-global-bundle.pem \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 opendb \
    && useradd --uid 10001 --gid 10001 --create-home opendb
COPY --from=dependencies /app/.venv /app/.venv
COPY config ./config
COPY opendb ./opendb
COPY locale ./locale
COPY manage.py ./
COPY compose/dokploy/start-web compose/dokploy/healthcheck.py compose/dokploy/model-init.py /app/deploy/
COPY --from=frontend /build/opendb/static/app /app/opendb/static/app
RUN mkdir -p /app/staticfiles /app/opendb/media /state/oauth /models \
    && chown -R 10001:10001 /app/staticfiles /app/opendb/media /state /models \
    && chmod 0700 /state/oauth && chmod 0755 /app/deploy/start-web
USER 10001:10001
CMD ["/app/deploy/start-web"]
