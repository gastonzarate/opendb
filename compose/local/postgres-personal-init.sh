#!/usr/bin/env bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', datname)
FROM pg_database \gexec
SQL
