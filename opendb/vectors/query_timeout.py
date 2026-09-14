"""Bound exact retrieval without changing the caller's session timeout."""

from contextlib import contextmanager


@contextmanager
def query_timeout(conn):
    with conn.transaction():
        previous = conn.execute(
            "SELECT setting FROM pg_catalog.pg_settings WHERE name='statement_timeout'"
        ).fetchone()[0]
        timeout_ms = min(int(previous) or 10_000, 10_000)
        conn.execute(
            "SELECT set_config('statement_timeout',%s,true)", (str(timeout_ms),)
        )
        yield
        conn.execute("SELECT set_config('statement_timeout',%s,true)", (previous,))
