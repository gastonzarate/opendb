from contextlib import contextmanager

import psycopg
from django.conf import settings
from psycopg.conninfo import conninfo_to_dict

from .credentials import derive_database_password
from .exceptions import DatabaseAccessDenied
from .exceptions import DatabaseNotReady
from .models import PersonalDatabase


def require_owner(actor_id, database_id):
    try:
        return PersonalDatabase.objects.get(pk=database_id, owner_id=actor_id)
    except (PersonalDatabase.DoesNotExist, ValueError) as exc:
        msg = "Database unavailable"
        raise DatabaseAccessDenied(msg) from exc


def connection_parameters(db, role=None, identity="owner"):
    params = conninfo_to_dict(settings.OPENDB_ADMIN_DSN)
    params.update(dbname=db.database_name, connect_timeout=5)
    if role:
        params.update(user=role, password=derive_database_password(db.id, identity))
    return params


@contextmanager
def privileged_connection(database_id):
    db = PersonalDatabase.objects.get(pk=database_id)
    with psycopg.connect(**connection_parameters(db), autocommit=True) as conn:
        yield conn


@contextmanager
def owner_connection(owner_id, database_id):
    db = require_owner(owner_id, database_id)
    if db.status != "ready":
        msg = "Database is not ready"
        raise DatabaseNotReady(msg)
    with psycopg.connect(
        **connection_parameters(db, db.role_name), autocommit=True
    ) as conn:
        yield conn


@contextmanager
def data_connection(actor_id, database_id):
    db = PersonalDatabase.objects.get(pk=database_id)
    if db.owner_id == actor_id:
        with owner_connection(actor_id, database_id) as conn:
            yield conn
    else:
        from .sharing import guest_connection

        with guest_connection(actor_id, db) as conn:
            yield conn
