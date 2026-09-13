"""Leased, retryable embedding work with compare-and-publish version checks."""

import json
from uuid import uuid4

from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .embeddings import EmbeddingError
from .embeddings import LocalEmbedder
from .embeddings import chunk_text
from .embeddings import validate_vector

MAX_ROWS = 1000


def _claim(conn):
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        job = cursor.execute("""
            SELECT r.*,i.model_id FROM opendb_catalog.vector_rows r
            JOIN opendb_catalog.vector_valid_sources i ON i.id=r.index_id
            JOIN pg_catalog.pg_class c ON c.oid=i.source_relid
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE r.state<>'ready' AND r.retry_at<=now() AND n.nspname='data'
              AND c.relkind='r' AND NOT c.relrowsecurity AND NOT c.relhassubclass
            ORDER BY r.retry_at,r.index_id,r.source_key
            FOR UPDATE OF r SKIP LOCKED LIMIT 1
        """).fetchone()
        if job is None:
            return None
        job["lease_token"] = uuid4()
        cursor.execute(
            """
            UPDATE opendb_catalog.vector_rows
            SET state='processing',attempts=attempts+1,lease_token=%s,
                retry_at=now()+interval '5 minutes',error_code=NULL
            WHERE index_id=%s AND source_key=%s
        """,
            (job["lease_token"], job["index_id"], Jsonb(job["source_key"])),
        )
        return job


def _current(conn, job):
    return conn.execute(
        """
        SELECT 1 FROM opendb_catalog.vector_rows r
        JOIN opendb_catalog.vector_valid_sources i ON i.id=r.index_id
        WHERE index_id=%s AND source_key=%s AND version=%s AND lease_token=%s
        FOR UPDATE OF r
    """,
        (job["index_id"], Jsonb(job["source_key"]), job["version"], job["lease_token"]),
    ).fetchone()


def _publish(conn, job, embedded):
    with conn.transaction():
        if not _current(conn, job):
            return "superseded"
        conn.execute(
            "DELETE FROM opendb_catalog.vector_chunks "
            "WHERE index_id=%s AND source_key=%s",
            (job["index_id"], Jsonb(job["source_key"])),
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO opendb_catalog.vector_chunks
                    (index_id,source_key,version,chunk_order,char_start,char_end,
                     token_start,token_end,text,embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::public.vector)
            """,
                [
                    (
                        job["index_id"],
                        Jsonb(job["source_key"]),
                        job["version"],
                        chunk.ordinal,
                        chunk.char_start,
                        chunk.char_end,
                        chunk.token_start,
                        chunk.token_end,
                        chunk.text,
                        json.dumps(vector),
                    )
                    for chunk, vector in embedded
                ],
            )
        conn.execute(
            """
            UPDATE opendb_catalog.vector_rows SET state='ready', retry_at=NULL,
                lease_token=NULL,error_code=NULL WHERE index_id=%s AND source_key=%s
        """,
            (job["index_id"], Jsonb(job["source_key"])),
        )
        return "processed"


def _fail(conn, job):
    with conn.transaction():
        if not _current(conn, job):
            return "superseded"
        conn.execute(
            """
            UPDATE opendb_catalog.vector_rows SET state='failed',lease_token=NULL,
                error_code='embedding_failed',
                retry_at=now()+make_interval(
                    secs => LEAST(3600,30*power(2,LEAST(attempts-1,7)))::integer
                )
            WHERE index_id=%s AND source_key=%s
        """,
            (job["index_id"], Jsonb(job["source_key"])),
        )
        return "failed"


def _embed(job, embedder):
    if job["model_id"] != embedder.model_id:
        msg = "Embedding model mismatch"
        raise EmbeddingError(msg)
    return [
        (chunk, validate_vector(embedder.embed(chunk.text)))
        for chunk in chunk_text(job["source_text"] or "", embedder)
    ]


def process_pending(conn, embedder=None, limit=10):
    """Use a dedicated idle admin connection; never commit a caller's transaction.

    Leases expire after five minutes. Losing a lease or changing source versions
    always discards the old computation, including its failure status.
    """
    if conn.info.transaction_status != TransactionStatus.IDLE:
        msg = "Worker requires an idle connection outside any transaction"
        raise ValueError(msg)
    if type(limit) is not int or not 1 <= limit <= MAX_ROWS:
        msg = "Limit must be between 1 and 1000"
        raise ValueError(msg)
    result = {"processed": 0, "failed": 0, "superseded": 0}
    embedder = embedder or LocalEmbedder()
    for _ in range(limit):
        job = _claim(conn)
        if job is None:
            break
        try:
            embedded = _embed(job, embedder)
            outcome = _publish(conn, job, embedded)
        except Exception:  # noqa: BLE001 - preserve source and queue on provider failure.
            outcome = _fail(conn, job)
        result[outcome] += 1
    return result
