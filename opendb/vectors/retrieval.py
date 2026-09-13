"""Restricted-role entrypoints; authorization is also enforced inside PostgreSQL."""

import json
import math
from uuid import UUID

from psycopg.types.json import Jsonb

from .embeddings import EmbeddingError
from .embeddings import LocalEmbedder
from .embeddings import validate_vector
from .view_search import resolve_view
from .view_search import search_view

MAX_RESULTS = 1000
MAX_FILTERS = 16


def status(conn):
    return [row[0] for row in conn.execute("SELECT opendb_catalog.vector_status()")]


def _filters(filters):
    if filters is None:
        return {}
    if not isinstance(filters, dict) or len(filters) > MAX_FILTERS:
        msg = "Filters require an object with at most 16 scalar equality conditions"
        raise ValueError(msg)
    for key, value in filters.items():
        if (
            not isinstance(key, str)
            or not key
            or "\x00" in key
            or type(value) not in (str, int, float, bool, type(None))
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            msg = "Unsupported filter column or value"
            raise ValueError(msg)
    return filters


def search(  # noqa: PLR0913 - backward-compatible API plus explicit search options.
    conn, index_id, query, limit=10, embedder=None, *, target_view=None, filters=None
):
    index_id = UUID(str(index_id))
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        msg = "Limit must be between 1 and 1000"
        raise ValueError(msg)
    filters = _filters(filters)
    target = None
    # Check authorization before inference, then recheck when reading results.
    if target_view is not None:
        target = resolve_view(conn, index_id, target_view, filters)
    elif not conn.execute(
        "SELECT opendb_catalog.vector_visible(%s)", (index_id,)
    ).fetchone()[0]:
        msg = "Vector index unavailable"
        raise PermissionError(msg)
    embedder = embedder or LocalEmbedder()
    if not isinstance(query, str) or not query.strip():
        msg = "Query must be nonempty text"
        raise ValueError(msg)
    if len(embedder.tokenize(query, add_special=True)) > embedder.context_size:
        msg = "Query exceeds the embedding token budget"
        raise EmbeddingError(msg)
    vector = validate_vector(embedder.embed(query))
    if target is not None:
        return search_view(
            conn,
            index_id,
            target,
            json.dumps(vector),
            model_id=embedder.model_id,
            limit=limit,
            filters=filters,
        )
    return [
        row[0]
        for row in conn.execute(
            """
        SELECT opendb_catalog.vector_search(%s,%s,%s,%s,%s)
    """,
            (index_id, json.dumps(vector), embedder.model_id, limit, Jsonb(filters)),
        )
    ]
