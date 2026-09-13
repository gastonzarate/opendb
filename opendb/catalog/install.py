"""Administrative bootstrap; personal roles never own internal objects."""

from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

SCHEMA_LOCK_KEY = 6847392015641
FUNCTION_SIGNATURES = (
    "begin_ingestion(text,text,jsonb)",
    "finish_ingestion(uuid,jsonb)",
    "annotate(uuid,text,text,text,jsonb)",
    "read_annotations()",
)


class CatalogError(ValueError):
    """A catalog failure whose message contains no database diagnostics."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def install(conn, owner_role):
    if not isinstance(owner_role, str) or not owner_role or "\x00" in owner_role:
        msg = "catalog_install_failed"
        raise CatalogError(
            msg,
            "Catalog installation requires a valid owner role.",
        )
    try:
        with conn.transaction(), conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,)
            )
            cur.execute(
                "SELECT n.nspowner = r.oid, r.rolname <> current_user, NOT r.rolsuper "
                "FROM pg_catalog.pg_namespace n CROSS JOIN pg_catalog.pg_roles r "
                "WHERE n.nspname='data' AND r.rolname=%s",
                (owner_role,),
            )
            if cur.fetchone() != (True, True, True):
                msg = "catalog_install_failed"
                raise CatalogError(
                    msg,
                    "Catalog installation requires the provisioner and data owner.",
                )
            cur.execute(
                "SELECT pg_catalog.pg_get_userbyid(nspowner)=current_user "
                "FROM pg_catalog.pg_namespace WHERE nspname='opendb_catalog'",
            )
            existing = cur.fetchone()
            if existing is not None and existing != (True,):
                msg = "catalog_install_failed"
                raise CatalogError(
                    msg,
                    "Catalog schema ownership does not match the provisioner.",
                )
            if existing is None:
                cur.execute("CREATE SCHEMA opendb_catalog")
            # Peer read helpers may deliberately grant schema USAGE to guests.
            cur.execute("REVOKE CREATE ON SCHEMA opendb_catalog FROM PUBLIC")
            cur.execute(
                sql.SQL("REVOKE ALL ON SCHEMA opendb_catalog FROM {}").format(
                    sql.Identifier(owner_role)
                )
            )
            cur.execute(
                sql.SQL("GRANT USAGE ON SCHEMA opendb_catalog TO {}").format(
                    sql.Identifier(owner_role)
                )
            )
            cur.execute(Path(__file__).with_name("schema.sql").read_text())
            for table in ("ingestion_operations", "annotations"):
                cur.execute(
                    sql.SQL(
                        "REVOKE ALL ON TABLE opendb_catalog.{} FROM PUBLIC, {}"
                    ).format(
                        sql.Identifier(table),
                        sql.Identifier(owner_role),
                    )
                )
            for signature in FUNCTION_SIGNATURES:
                # Signatures are static trusted literals, never input identifiers.
                function = sql.SQL("opendb_catalog." + signature)
                cur.execute(
                    sql.SQL("REVOKE ALL ON FUNCTION {} FROM PUBLIC").format(function)
                )
                cur.execute(
                    sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                        function, sql.Identifier(owner_role)
                    )
                )
    except psycopg.Error:
        msg = "catalog_install_failed"
        raise CatalogError(
            msg,
            "Catalog installation failed; check provisioning configuration.",
        ) from None
