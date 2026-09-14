"""Administrative provisioning, never an executor for caller SQL."""

import psycopg
from django.db import transaction
from psycopg import sql

from .connections import connection_parameters
from .credentials import derive_database_password
from .lifecycle import database_lock
from .models import PersonalDatabase


def provision_personal_database(owner_id, *, recreate_deleted=False):
    if not transaction.get_autocommit():
        msg = "Provisioning requires control-database autocommit"
        raise RuntimeError(msg)
    db, _ = PersonalDatabase.objects.get_or_create(owner_id=owner_id)
    try:
        return _provision(db, recreate_deleted=recreate_deleted)
    except Exception:
        # Includes initial admin-connection failures; never overwrite a concurrent
        # worker's successful completion after it has released the advisory lock.
        PersonalDatabase.objects.filter(
            pk=db.pk, status__in=["pending", "failed"]
        ).update(status="failed", error_code="provision_failed")
        raise


def _grant_provisioner_membership(admin, owner_role):
    """RDS administrators need explicit owner privileges; never grant the reverse."""
    administrator, unrestricted = admin.execute(
        "SELECT current_user, rolsuper FROM pg_catalog.pg_roles "
        "WHERE rolname=current_user"
    ).fetchone()
    if not unrestricted:
        admin.execute(
            sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET TRUE").format(
                sql.Identifier(owner_role), sql.Identifier(administrator)
            )
        )


def _provision(db, *, recreate_deleted=False):  # noqa: C901 -- Explicit lifecycle states.
    password = derive_database_password(db.id)
    with database_lock(db.id) as admin:
        db.refresh_from_db()
        if db.status == "ready":
            return db
        if db.status in {"deleting", "delete_failed"}:
            if recreate_deleted:
                msg = "Database deletion must finish before creating a database"
                raise ValueError(msg)
            return db
        if db.status == "deleted" and not recreate_deleted:
            return db
        try:
            if db.status == "deleted":
                db.onboarding_completed = False
            db.status, db.error_code = "provisioning", ""
            db.save(update_fields=["status", "error_code", "onboarding_completed"])
            marker = f"opendb:{db.id}"
            role = admin.execute(
                "SELECT oid, shobj_description(oid, 'pg_authid') FROM pg_roles "
                "WHERE rolname=%s",
                (db.role_name,),
            ).fetchone()
            if role and role[1] != marker:
                msg = "Resource ownership conflict"
                raise ValueError(msg)
            if not role:
                with admin.transaction():
                    admin.execute(
                        sql.SQL(
                            "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                        ).format(sql.Identifier(db.role_name))
                    )
                    admin.execute(
                        sql.SQL("COMMENT ON ROLE {} IS {}").format(
                            sql.Identifier(db.role_name), sql.Literal(marker)
                        )
                    )
            _grant_provisioner_membership(admin, db.role_name)
            existing = admin.execute(
                "SELECT pg_get_userbyid(datdba), "
                "shobj_description(oid, 'pg_database') "
                "FROM pg_database WHERE datname=%s",
                (db.database_name,),
            ).fetchone()
            admin_user = admin.execute("SELECT current_user").fetchone()[0]
            if existing and (existing[0] != admin_user or existing[1] != marker):
                msg = "Resource ownership conflict"
                raise ValueError(msg)
            if not existing:
                admin.execute(
                    sql.SQL(
                        "CREATE DATABASE {} TEMPLATE template0 ALLOW_CONNECTIONS false"
                    ).format(sql.Identifier(db.database_name))
                )
                admin.execute(
                    sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                        sql.Identifier(db.database_name), sql.Literal(marker)
                    )
                )
            admin.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(db.database_name)
                )
            )
            admin.execute(
                sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(
                    sql.Identifier(db.database_name)
                )
            )
            with (
                psycopg.connect(**connection_parameters(db), autocommit=True) as conn,
                conn.transaction(),
            ):
                conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
                conn.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                        sql.Identifier(db.role_name)
                    )
                )
                conn.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS data AUTHORIZATION {}").format(
                        sql.Identifier(db.role_name)
                    )
                )
                conn.execute("CREATE SCHEMA IF NOT EXISTS opendb_catalog")
                conn.execute("REVOKE ALL ON SCHEMA opendb_catalog FROM PUBLIC")
                # Every backend module installs only its own protected objects.
                from opendb.catalog import install as install_catalog
                from opendb.vectors import install as install_vectors

                install_catalog(conn, db.role_name)
                install_vectors(conn, db.role_name)
            admin.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(db.database_name), sql.Identifier(db.role_name)
                )
            )
            admin.execute(
                sql.SQL(
                    "ALTER ROLE {} SET search_path = data, pg_catalog, public"
                ).format(sql.Identifier(db.role_name))
            )
            admin.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(db.role_name), sql.Literal(password)
                )
            )
            with psycopg.connect(**connection_parameters(db, db.role_name)) as conn:
                conn.execute("SELECT 1")
            db.status = "ready"
            db.save(update_fields=["status"])
        except Exception:
            db.status, db.error_code = "failed", "provision_failed"
            db.save(update_fields=["status", "error_code"])
            raise
    return db
