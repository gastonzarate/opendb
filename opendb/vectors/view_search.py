"""Search only rows/columns exposed by a granted view, as the restricted caller."""

from psycopg import sql
from psycopg.types.json import Jsonb

REQUIRED_COLUMNS = {
    "index_id": "uuid",
    "source_key": "jsonb",
    "version": "int8",
    "chunk_order": "int4",
    "char_start": "int4",
    "char_end": "int4",
    "token_start": "int4",
    "token_end": "int4",
    "text": "text",
    "embedding": "vector",
    "model_id": "text",
}
FILTER_TYPES = {
    16,
    17,
    20,
    21,
    23,
    25,
    114,
    700,
    701,
    1042,
    1043,
    1082,
    1083,
    1114,
    1184,
    1186,
    1266,
    1700,
    2950,
    3802,
}


def resolve_view(conn, index_id, target_view, filters):
    if not isinstance(target_view, str):
        msg = "Target must be a data view"
        raise ValueError(msg)  # noqa: TRY004 - public input validation contract.
    name = target_view.removeprefix("data.")
    if not name or "." in name or "\x00" in name:
        msg = "Target must be a data view"
        raise ValueError(msg)
    found = conn.execute(
        """
        SELECT c.oid FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='data' AND c.relname=%s AND c.relkind='v'
          AND opendb_catalog.vector_view_visible(%s,c.oid)
    """,
        (name, index_id),
    ).fetchone()
    if not found:
        msg = "Vector index or target view unavailable"
        raise PermissionError(msg)
    oid = found[0]
    columns = conn.execute(
        """
        SELECT a.attname,t.typname,a.atttypid,n.nspname FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
        JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
        WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped
    """,
        (oid,),
    ).fetchall()
    types = {col[0]: col[1:] for col in columns}
    for column, expected in REQUIRED_COLUMNS.items():
        actual = types.get(column)
        namespace = "public" if expected == "vector" else "pg_catalog"
        if actual is None or actual[0] != expected or actual[2] != namespace:
            msg = "Target view must expose the canonical vector provenance columns"
            raise ValueError(msg)
    for column in filters:
        if column not in types or types[column][1] not in FILTER_TYPES:
            msg = "Unsupported filter column or value"
            raise ValueError(msg)
    return name, oid


def search_view(  # noqa: PLR0913 - mirrors validated retrieval options.
    conn,
    index_id,
    target,
    vector,
    *,
    model_id,
    limit,
    filters,
    query,
    semantic_weight,
):
    name, oid = target
    # A second check catches source invalidation and revocation during inference.
    if not conn.execute(
        "SELECT opendb_catalog.vector_view_visible(%s,%s)", (index_id, oid)
    ).fetchone()[0]:
        msg = "Vector index or target view unavailable"
        raise PermissionError(msg)
    configured_model = conn.execute(
        "SELECT opendb_catalog.vector_view_model(%s,%s)", (index_id, oid)
    ).fetchone()[0]
    if model_id is not None and model_id != configured_model:
        msg = "Embedding model mismatch"
        raise ValueError(msg)
    model_id = configured_model
    conditions = []
    parameters = [
        vector,
        query,
        semantic_weight,
        index_id,
        model_id,
        model_id,
        index_id,
        oid,
    ]
    for field, value in filters.items():
        conditions.append(
            sql.SQL(
                "coalesce(pg_catalog.to_jsonb(v.{}),'null'::jsonb)=%s::jsonb",
            ).format(sql.Identifier(field))
        )
        parameters.append(Jsonb(value))
    parameters.append(limit)
    predicate = sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("true")
    # Never execute this view using privileged_connection / SECURITY DEFINER:
    # both PostgreSQL grants and any view projection (including redaction) apply.
    statement = sql.SQL("""
        WITH candidates AS MATERIALIZED (
        SELECT q.weight,v.index_id,v.source_key,v.version,v.chunk_order,
               v.char_start,v.char_end,
               v.token_start,v.token_end,v.text,
               CASE WHEN q.weight>0 THEN
                    1-(v.embedding OPERATOR(public.<=>) q.embedding)
               END AS semantic_score,
               CASE WHEN q.weight<100 THEN
                    ts_rank_cd(to_tsvector('pg_catalog.simple',v.text),q.lexical)
               END AS lexical_score,
               CASE WHEN q.weight<100 THEN
                    to_tsvector('pg_catalog.simple',v.text) @@ q.lexical
               ELSE false END AS lexical_match
        FROM data.{} v CROSS JOIN (SELECT %s::public.vector AS embedding,
             websearch_to_tsquery('pg_catalog.simple',%s) AS lexical,
             %s::double precision AS weight) q
        WHERE v.index_id=%s AND (%s::text IS NULL OR v.model_id=%s)
          AND opendb_catalog.vector_view_visible(%s,%s) AND {}
        ), ranked AS (
            SELECT *,
                CASE WHEN weight>0 THEN row_number() OVER (
                    ORDER BY semantic_score DESC NULLS LAST,source_key,chunk_order
                ) END AS semantic_rank,
                CASE WHEN lexical_match THEN row_number() OVER (
                    ORDER BY lexical_match DESC NULLS LAST,
                             lexical_score DESC NULLS LAST,source_key,chunk_order
                ) END AS lexical_rank
            FROM candidates
        ), scored AS (
            SELECT *,61*(coalesce((weight/100.0)/(60+semantic_rank),0)+
                         coalesce((1-weight/100.0)/(60+lexical_rank),0)) AS score
            FROM ranked WHERE weight>0 OR lexical_match
        )
        SELECT index_id,source_key,version,chunk_order,char_start,char_end,
               token_start,token_end,text,score,semantic_score,lexical_score,
               semantic_rank,lexical_rank
        FROM scored ORDER BY score DESC,source_key,chunk_order LIMIT %s
    """).format(sql.Identifier(name), predicate)
    keys = [
        "index_id",
        "source_key",
        "version",
        "chunk_order",
        "char_start",
        "char_end",
        "token_start",
        "token_end",
        "text",
        "score",
        "semantic_score",
        "lexical_score",
        "semantic_rank",
        "lexical_rank",
    ]
    results = [
        dict(zip(keys, row, strict=True)) for row in conn.execute(statement, parameters)
    ]
    for row in results:
        row["index_id"] = str(row["index_id"])
    return results
