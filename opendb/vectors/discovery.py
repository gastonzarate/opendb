"""Deterministic automatic registration; no source text leaves PostgreSQL."""

import os
from uuid import uuid4

from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from .registry import register
from .schema import SCHEMA_LOCK_KEY

NARRATIVE_COLUMNS = (
    "body",
    "content",
    "text",
    "texto",
    "contenido",
    "transcript",
    "transcription",
    "transcripcion",
    "dialogue",
    "utterance",
)


def configuration():
    threshold = int(os.environ.get("OPENDB_VECTOR_MIN_TEXT_CHARS", "500"))
    if threshold < 1:
        msg = "OPENDB_VECTOR_MIN_TEXT_CHARS must be positive"
        raise ValueError(msg)
    names = os.environ.get(
        "OPENDB_VECTOR_NARRATIVE_COLUMNS", ",".join(NARRATIVE_COLUMNS)
    )
    return threshold, {
        name.strip().lower() for name in names.split(",") if name.strip()
    }


def _candidates(conn):
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute("""
            SELECT c.oid AS relid,c.relname,a.attnum,a.attname,
                c.relkind='r' AND NOT c.relrowsecurity AND NOT c.relispartition
                AND NOT c.relhassubclass AND NOT EXISTS (
                    SELECT FROM pg_catalog.pg_inherits WHERE inhrelid=c.oid
                ) AND NOT EXISTS (
                    SELECT FROM pg_catalog.pg_index unsafe WHERE unsafe.indrelid=c.oid
                      AND (unsafe.indexprs IS NOT NULL OR unsafe.indpred IS NOT NULL)
                ) AS supported
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
            WHERE n.nspname='data' AND c.relkind IN ('r','p','f')
                AND a.attnum>0 AND NOT a.attisdropped AND a.atttypid IN (25,1042,1043)
                AND NOT EXISTS (
                    SELECT FROM opendb_catalog.vector_valid_sources i
                    WHERE i.source_relid=c.oid AND i.text_attnum=a.attnum
                )
            ORDER BY c.oid,a.attnum
        """).fetchall()


def _key(conn, candidate, source):
    key = conn.execute(
        """
        SELECT a.attname FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_index x ON x.indrelid=a.attrelid AND x.indkey[0]=a.attnum
        WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped AND a.attnotnull
          AND a.atttypid IN (20,21,23,25,1042,1043,2950)
          AND x.indisunique AND x.indisvalid AND x.indisready AND x.indimmediate
          AND x.indnkeyatts=1 AND x.indpred IS NULL AND x.indexprs IS NULL
        ORDER BY x.indisprimary DESC,a.attnum LIMIT 1
    """,
        (candidate["relid"],),
    ).fetchone()
    if key:
        return key[0]
    # Random suffix is below PostgreSQL's identifier limit, unlike concatenated
    # table/column names. DDL and registration share a transaction: retries leave
    # neither duplicate columns nor a partly installed registration.
    name = "_opendb_vector_id_" + uuid4().hex
    conn.execute(
        sql.SQL(
            "ALTER TABLE {} ADD COLUMN {} bigint GENERATED ALWAYS AS IDENTITY UNIQUE"
        ).format(source, sql.Identifier(name))
    )
    return name


def _discover(conn, candidate, threshold, narrative_columns):
    with conn.transaction():
        conn.execute("SET LOCAL search_path=pg_catalog")
        conn.execute("SET LOCAL lock_timeout='1s'")
        conn.execute("SET LOCAL statement_timeout='30s'")
        conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        source = sql.Identifier("data", candidate["relname"])
        conn.execute(
            sql.SQL("LOCK TABLE {} IN SHARE UPDATE EXCLUSIVE MODE").format(source)
        )
        # This lock permits DML but excludes index creation, including CONCURRENTLY.
        # Revalidate OID/attnum and eligibility after locking. A replacement with
        # the same name must not inherit the old column's discovery decision.
        current = conn.execute(
            """
            SELECT c.oid,a.attnum,c.relkind='r' AND NOT c.relrowsecurity
                AND NOT c.relispartition AND NOT c.relhassubclass
                AND NOT EXISTS (SELECT FROM pg_catalog.pg_inherits WHERE inhrelid=c.oid)
                AND a.atttypid IN (25,1042,1043)
                AND NOT EXISTS (
                    SELECT FROM pg_catalog.pg_index unsafe WHERE unsafe.indrelid=c.oid
                      AND (unsafe.indexprs IS NOT NULL OR unsafe.indpred IS NOT NULL)
                )
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
            WHERE n.nspname='data' AND c.relname=%s AND a.attname=%s
                AND NOT a.attisdropped
        """,
            (candidate["relname"], candidate["attname"]),
        ).fetchone()
        if current != (candidate["relid"], candidate["attnum"], True):
            msg = "Source changed during discovery"
            raise ValueError(msg)
        if conn.execute(
            """
            SELECT 1 FROM opendb_catalog.vector_valid_sources
            WHERE source_relid=%s AND text_attnum=%s
        """,
            (candidate["relid"], candidate["attnum"]),
        ).fetchone():
            return 0
        column = sql.Identifier(candidate["attname"])
        qualifies = conn.execute(
            sql.SQL("""
            SELECT EXISTS (SELECT FROM {} WHERE char_length({})>=%s
                OR (%s AND {} ~ '[^[:space:]]'))
        """).format(source, column, column),
            (
                threshold,
                candidate["attname"].lower() in narrative_columns,
            ),
        ).fetchone()[0]
        if not qualifies:
            return 0
        conn.execute(
            sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(source)
        )
        key = _key(conn, candidate, source)
        register(conn, "data." + candidate["relname"], key, candidate["attname"])
        return 1


def discover(conn, *, threshold=None, narrative_columns=None):
    """Register eligible columns once; return safe per-column failure metadata.

    Unsupported base tables are reported without evaluating their rows.
    Views and materialized views are not sources and are never evaluated.
    Every pass retries failures and previously short/empty columns.
    """
    if conn.info.transaction_status != TransactionStatus.IDLE:
        msg = "Discovery requires an idle connection outside any transaction"
        raise ValueError(msg)
    configured_threshold, configured_names = configuration()
    threshold = configured_threshold if threshold is None else threshold
    narrative_columns = (
        configured_names if narrative_columns is None else narrative_columns
    )
    if type(threshold) is not int or threshold < 1:
        msg = "Threshold must be a positive integer"
        raise ValueError(msg)
    result = {"registered": 0, "discovery_failed": 0, "discovery_errors": []}
    for candidate in _candidates(conn):
        error = None
        if not candidate["supported"]:
            error = "unsupported_text_source"
        else:
            try:
                result["registered"] += _discover(
                    conn, candidate, threshold, narrative_columns
                )
            except Exception:  # noqa: BLE001 - rollback and retry; never log source/provider errors.
                error = "auto_registration_failed"
        if error:
            result["discovery_failed"] += 1
            result["discovery_errors"].append(
                {
                    "table": "data." + candidate["relname"],
                    "text_column": candidate["attname"],
                    "error_code": error,
                }
            )
    return result
