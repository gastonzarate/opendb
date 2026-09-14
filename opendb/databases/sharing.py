"""Read-only grants grouped in database-local application roles."""

import re
from contextlib import contextmanager
from uuid import UUID

import psycopg
from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.db import transaction
from psycopg import sql

from opendb.catalog.install import SCHEMA_LOCK_KEY

from .connections import connection_parameters
from .connections import privileged_connection
from .connections import require_owner
from .credentials import derive_database_password
from .exceptions import DatabaseAccessDenied
from .exceptions import DatabaseNotReady
from .lifecycle import database_lock
from .models import AccessRole
from .models import ObjectGrant
from .models import PersonalDatabase
from .models import RoleAssignment

MAX_ROLE_NAME = 100
MAX_ROLE_DESCRIPTION = 2000
_UNSET = object()


@contextmanager
def sharing_lock(database_id):
    if not transaction.get_autocommit():
        msg = "Sharing operations require control-database autocommit"
        raise RuntimeError(msg)
    with database_lock(database_id):
        db = PersonalDatabase.objects.get(pk=database_id)
        if db.status != "ready":
            msg = "Database is not ready"
            raise DatabaseNotReady(msg)
        with privileged_connection(database_id) as conn, conn.transaction():
            conn.execute("SET LOCAL lock_timeout = '5s'")
            key = UUID(str(database_id)).int % (2**63 - 1)
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
            yield conn


def object_identifier(name):
    if not isinstance(name, str) or not re.fullmatch(
        r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", name
    ):
        msg = "Invalid data object name"
        raise ValueError(msg)
    return sql.Identifier("data", name)


def _role(actor_id, role_id):
    role = AccessRole.objects.select_related("database").get(pk=role_id)
    require_owner(actor_id, role.database_id)
    return role


def _validate_description(description):
    if not isinstance(description, str) or len(description) > MAX_ROLE_DESCRIPTION:
        msg = "Role description must be text (maximum 2000 characters)"
        raise ValueError(msg)


def create_role(actor_id, database_id, name, description=""):
    db = require_owner(actor_id, database_id)
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_ROLE_NAME:
        msg = "Role name required (maximum 100 characters)"
        raise ValueError(msg)
    _validate_description(description)
    with sharing_lock(db.id) as conn:
        role, _ = AccessRole.objects.get_or_create(
            database=db, name=name.strip(), defaults={"description": description}
        )
        if not conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (role.pg_name,)
        ).fetchone():
            conn.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role.pg_name))
            )
            conn.execute(
                sql.SQL("COMMENT ON ROLE {} IS {}").format(
                    sql.Identifier(role.pg_name), sql.Literal(f"opendb:{db.id}")
                )
            )
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA data, public TO {}").format(
                sql.Identifier(role.pg_name)
            )
        )
    return role


def update_role(actor_id, database_id, role_id, *, name=_UNSET, description=_UNSET):
    require_owner(actor_id, database_id)
    role = _role(actor_id, role_id)
    if str(role.database_id) != str(database_id):
        msg = "Database unavailable"
        raise DatabaseAccessDenied(msg)
    fields = []
    if name is not _UNSET:
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_ROLE_NAME:
            msg = "Role name required (maximum 100 characters)"
            raise ValueError(msg)
        role.name = name.strip()
        fields.append("name")
    if description is not _UNSET:
        _validate_description(description)
        role.description = description
        fields.append("description")
    if fields:
        role.full_clean()
        role.save(update_fields=fields)
    return role


def grant_object(actor_id, role_id, object_name):
    role = _role(actor_id, role_id)
    identifier = object_identifier(object_name)
    with sharing_lock(role.database_id) as conn:
        row = conn.execute(
            "SELECT c.relkind, c.oid FROM pg_class c JOIN pg_namespace n ON "
            "n.oid=c.relnamespace WHERE n.nspname='data' AND c.relname=%s",
            (object_name,),
        ).fetchone()
        if not row or row[0] not in ("r", "v"):
            msg = "Only existing data tables and views can be shared"
            raise ValueError(msg)
        conn.execute(
            sql.SQL("GRANT SELECT ON {} TO {}").format(
                identifier, sql.Identifier(role.pg_name)
            )
        )
        ObjectGrant.objects.update_or_create(
            role=role, relation_oid=row[1], defaults={"object_name": object_name}
        )
    return {"role_id": str(role.id), "object_name": object_name}


def revoke_object(actor_id, role_id, object_name):
    role = _role(actor_id, role_id)
    object_identifier(object_name)
    with sharing_lock(role.database_id) as conn:
        # Resolve current names first; fall back to a saved pre-rename identity.
        rows = conn.execute(
            "SELECT c.oid, n.nspname, c.relname FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname='data' AND c.relname=%s",
            (object_name,),
        ).fetchall()
        if not rows:
            saved_oids = list(
                ObjectGrant.objects.filter(
                    role=role, object_name=object_name
                ).values_list("relation_oid", flat=True)
            )
            rows = conn.execute(
                "SELECT c.oid, n.nspname, c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid = ANY(%s)",
                (saved_oids,),
            ).fetchall()
        for oid, schema_name, current_name in rows:
            conn.execute(
                sql.SQL("REVOKE ALL ON {} FROM {}").format(
                    sql.Identifier(schema_name, current_name),
                    sql.Identifier(role.pg_name),
                )
            )
            ObjectGrant.objects.filter(role=role, relation_oid=oid).delete()
        if not rows:
            ObjectGrant.objects.filter(role=role, object_name=object_name).delete()
    return {"revoked": True}


def assign_role(actor_id, role_id, email):
    role = _role(actor_id, role_id)
    email = email.strip().lower()
    validate_email(email)
    with sharing_lock(role.database_id):
        RoleAssignment.objects.get_or_create(role=role, email=email)
    return {"role_id": str(role.id), "email": email}


def revoke_role(actor_id, role_id, email):
    role = _role(actor_id, role_id)
    email = email.strip().lower()
    # Same lock as membership synchronization: a stale role set cannot be regranted.
    with sharing_lock(role.database_id) as conn:
        for user in get_user_model().objects.filter(email__iexact=email):
            guest_role = _guest_name(role.database, user.pk)
            if conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (guest_role,)
            ).fetchone():
                conn.execute(
                    sql.SQL("REVOKE {} FROM {}").format(
                        sql.Identifier(role.pg_name), sql.Identifier(guest_role)
                    )
                )
        RoleAssignment.objects.filter(role=role, email=email).delete()
    return {"revoked": True}


def _guest_name(db, actor_id):
    return f"odb_guest_{db.id.hex}_{actor_id}"


@contextmanager
def guest_connection(actor_id, db):
    user = get_user_model().objects.get(pk=actor_id)
    guest = _guest_name(db, actor_id)
    identity = f"guest:{actor_id}"
    with sharing_lock(db.id) as conn:
        roles = list(
            AccessRole.objects.filter(
                database=db, roleassignment__email__iexact=user.email
            ).distinct()
        )
        if not roles or not user.is_active:
            msg = "No access to this database"
            raise DatabaseAccessDenied(msg)
        if not conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (guest,)
        ).fetchone():
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(
                    sql.Identifier(guest),
                    sql.Literal(derive_database_password(db.id, identity)),
                )
            )
            conn.execute(
                sql.SQL("COMMENT ON ROLE {} IS {}").format(
                    sql.Identifier(guest), sql.Literal(f"opendb:{db.id}")
                )
            )
        for (membership,) in conn.execute(
            "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON "
            "r.oid=m.roleid JOIN pg_roles g ON g.oid=m.member WHERE g.rolname=%s",
            (guest,),
        ):
            conn.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(membership), sql.Identifier(guest)
                )
            )
        for role in roles:
            conn.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(role.pg_name), sql.Identifier(guest)
                )
            )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(db.database_name), sql.Identifier(guest)
            )
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} SET search_path = pg_catalog, data, public").format(
                sql.Identifier(guest)
            )
        )
    with psycopg.connect(
        **connection_parameters(db, guest, identity), autocommit=True
    ) as conn:
        yield conn


def list_access(actor_id, database_id):
    require_owner(actor_id, database_id)
    result = []
    with sharing_lock(database_id) as conn:
        for role in AccessRole.objects.filter(database_id=database_id):
            objects = conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE n.nspname='data' "
                "AND c.relkind IN ('r','v') "
                "AND has_table_privilege(%s,c.oid,'SELECT') ORDER BY c.relname",
                (role.pg_name,),
            ).fetchall()
            result.append(
                {
                    "id": str(role.pk),
                    "name": role.name,
                    "description": role.description,
                    "objects": [row[0] for row in objects],
                    "emails": list(
                        role.roleassignment_set.values_list("email", flat=True)
                    ),
                }
            )
    return result
