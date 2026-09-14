"""Serialize administrative lifecycle and sharing operations per database."""

from contextlib import contextmanager
from uuid import UUID

import psycopg
from django.conf import settings
from django.db import transaction


@contextmanager
def database_lock(database_id):
    if not transaction.get_autocommit():
        msg = "Database lifecycle operations require control-database autocommit"
        raise RuntimeError(msg)
    key = UUID(str(database_id)).int % (2**63 - 1)
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        admin.execute("SELECT pg_advisory_lock(%s)", (key,))
        try:
            yield admin
        finally:
            admin.execute("SELECT pg_advisory_unlock(%s)", (key,))
