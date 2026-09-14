"""Validate and register an actual data table using an administrative connection."""

from uuid import uuid4

from psycopg import sql

from .embeddings import MODEL_ID
from .schema import SCHEMA_LOCK_KEY


def _source(conn, table, key_column, text_column):
    name = table.removeprefix("data.")
    if not name:
        msg = "Only tables in the data schema can be registered"
        raise ValueError(msg)
    row = conn.execute(
        """
        SELECT c.oid, c.relowner, k.attnum, t.attnum
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_attribute k ON k.attrelid=c.oid AND k.attname=%s
        JOIN pg_catalog.pg_attribute t ON t.attrelid=c.oid AND t.attname=%s
        WHERE n.nspname='data' AND c.relname=%s AND c.relkind='r'
          AND NOT c.relrowsecurity AND NOT c.relispartition AND NOT c.relhassubclass
          AND NOT EXISTS (SELECT FROM pg_catalog.pg_inherits WHERE inhrelid=c.oid)
          AND NOT EXISTS (
              SELECT FROM pg_catalog.pg_index unsafe WHERE unsafe.indrelid=c.oid
                AND (unsafe.indexprs IS NOT NULL OR unsafe.indpred IS NOT NULL)
          )
          AND NOT k.attisdropped AND NOT t.attisdropped AND k.attnotnull
          AND k.atttypid IN (20,21,23,25,1042,1043,2950)
          AND t.atttypid IN (25,1042,1043)
          AND EXISTS (
              SELECT FROM pg_catalog.pg_index x
              WHERE x.indrelid=c.oid AND x.indisunique AND x.indisvalid
                AND x.indisready AND x.indimmediate AND x.indnkeyatts=1
                AND x.indkey[0]=k.attnum AND x.indpred IS NULL AND x.indexprs IS NULL
          )
    """,
        (key_column, text_column, name),
    ).fetchone()
    if not row:
        msg = (
            "Source requires a plain data table, a unique non-null scalar key, "
            "and a text column; views, inheritance, RLS, expression and partial "
            "indexes are unsupported"
        )
        raise ValueError(msg)
    return name, row


def register(conn, table, key_column, text_column):
    """Install transactional change capture and backfill; never call the model."""
    with conn.transaction():
        conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        name, (relid, owner_oid, key_attnum, text_attnum) = _source(
            conn,
            table,
            key_column,
            text_column,
        )
        source = sql.Identifier("data", name)
        # Serialize registration and close the write/backfill race. Check again
        # after locking in case a concurrent DDL changed the original relation.
        conn.execute(
            sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(source)
        )
        _, checked = _source(conn, table, key_column, text_column)
        if checked != (relid, owner_oid, key_attnum, text_attnum):
            msg = "Source changed during registration; retry"
            raise ValueError(msg)
        existing = conn.execute(
            """
            SELECT id,view_name FROM opendb_catalog.vector_indexes
            WHERE source_relid=%s AND key_attnum=%s AND text_attnum=%s
        """,
            (relid, key_attnum, text_attnum),
        ).fetchone()
        if existing:
            return {"index_id": str(existing[0]), "view_name": existing[1]}
        index_id = uuid4()
        view_name = "vector_" + index_id.hex
        conn.execute(
            """
            INSERT INTO opendb_catalog.vector_indexes
                (id,source_relid,key_attnum,text_attnum,model_id,view_name)
            VALUES (%s,%s,%s,%s,%s,%s)
        """,
            (index_id, relid, key_attnum, text_attnum, MODEL_ID, view_name),
        )
        conn.execute(
            sql.SQL("""
            CREATE OR REPLACE TRIGGER vector_capture_rows
            AFTER INSERT OR UPDATE OR DELETE ON {} FOR EACH ROW
            EXECUTE FUNCTION opendb_catalog.vector_capture()
        """).format(source)
        )
        conn.execute(
            sql.SQL("""
            CREATE OR REPLACE TRIGGER vector_capture_truncate
            AFTER TRUNCATE ON {} FOR EACH STATEMENT
            EXECUTE FUNCTION opendb_catalog.vector_capture()
        """).format(source)
        )
        conn.execute(
            sql.SQL("""
            INSERT INTO opendb_catalog.vector_rows (index_id,source_key,source_text)
            SELECT %s,pg_catalog.to_jsonb({}),{} FROM {}
        """).format(sql.Identifier(key_column), sql.Identifier(text_column), source),
            (index_id,),
        )
        # An explicitly shareable SQL object. The owner can define filtered
        # PostgreSQL views over this object without exposing bookkeeping.
        conn.execute(
            sql.SQL("""
            CREATE VIEW data.{} WITH (security_barrier=true) AS
            SELECT c.*,i.model_id FROM opendb_catalog.vector_chunks c
            JOIN opendb_catalog.vector_rows r USING (index_id,source_key,version)
            JOIN opendb_catalog.vector_valid_sources i ON i.id=c.index_id
            JOIN {} s ON pg_catalog.to_jsonb(s.{})=c.source_key
                       AND s.{} IS NOT DISTINCT FROM r.source_text
            WHERE c.index_id={} AND r.state='ready'
              AND NOT EXISTS (SELECT FROM pg_catalog.pg_class p
                              WHERE p.oid={} AND p.relrowsecurity)
        """).format(
                sql.Identifier(view_name),
                source,
                sql.Identifier(key_column),
                sql.Identifier(text_column),
                sql.Literal(index_id),
                sql.Literal(relid),
            )
        )
        owner = conn.execute(
            "SELECT pg_catalog.pg_get_userbyid(%s)", (owner_oid,)
        ).fetchone()[0]
        conn.execute(
            sql.SQL("REVOKE ALL ON data.{} FROM PUBLIC").format(
                sql.Identifier(view_name)
            )
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON data.{} TO {} WITH GRANT OPTION").format(
                sql.Identifier(view_name),
                sql.Identifier(owner),
            )
        )
    return {"index_id": str(index_id), "view_name": view_name}
