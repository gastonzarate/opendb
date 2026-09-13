"""Administrative bootstrap. Every owned internal object has a vector_ prefix."""

from pathlib import Path

from psycopg import sql

# Shared catalog/ingestion schema lock, also used by the catalog installer.
SCHEMA_LOCK_KEY = 6847392015641


def install(conn, owner_role):
    """Run only through trusted provisioning; does not own the peer catalog."""
    with conn.transaction():
        conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        existing = conn.execute(
            "SELECT pg_catalog.pg_get_userbyid(nspowner)=current_user "
            "FROM pg_catalog.pg_namespace WHERE nspname='opendb_catalog'",
        ).fetchone()
        if existing is not None and existing != (True,):
            msg = "Catalog schema ownership does not match the provisioner"
            raise ValueError(msg)
        conn.execute("CREATE SCHEMA IF NOT EXISTS opendb_catalog")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        conn.execute(Path(__file__).with_name("schema.sql").read_text())
        conn.execute("GRANT USAGE ON SCHEMA opendb_catalog TO PUBLIC")
        conn.execute(
            sql.SQL("REVOKE ALL ON {}, {}, {}, {}, {} FROM {}").format(
                *(
                    sql.Identifier("opendb_catalog", name)
                    for name in (
                        "vector_indexes",
                        "vector_valid_sources",
                        "vector_rows",
                        "vector_chunks",
                        "vector_version_seq",
                    )
                ),
                sql.Identifier(owner_role),
            )
        )
