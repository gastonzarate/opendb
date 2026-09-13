"""Administrative provisioning, never an executor for caller SQL."""

import psycopg
from django.conf import settings
from psycopg import sql

from .connections import connection_parameters
from .credentials import derive_database_password
from .models import PersonalDatabase


def provision_personal_database(owner_id):
    db, _ = PersonalDatabase.objects.get_or_create(owner_id=owner_id)
    password = derive_database_password(db.id)
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        admin.execute("SELECT pg_advisory_lock(%s)", (db.id.int % (2**63 - 1),))
        try:
            db.refresh_from_db()
            if db.status == "ready":
                return db
            db.status, db.error_code = "provisioning", ""
            db.save(update_fields=["status", "error_code"])
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
        finally:
            admin.execute("SELECT pg_advisory_unlock(%s)", (db.id.int % (2**63 - 1),))
    return db
