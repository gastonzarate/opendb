"""Disposable PostgreSQL integration environment (docker-compose.test.yml)."""

import os

os.environ["DATABASE_URL"] = (
    "postgres://opendb_test:opendb_test@127.0.0.1:55439/opendb_test"
)
from .test import *  # noqa: F403

OPENDB_ADMIN_DSN = "postgres://opendb_test:opendb_test@127.0.0.1:55439/postgres"
OPENDB_DB_CREDENTIAL_KEY = "integration-only-deterministic-key-not-for-deployment"
OPENDB_TEST_CLUSTER = True
DATABASES["default"]["TEST"] = {"NAME": f"test_opendb_{os.getpid()}"}  # noqa: F405
