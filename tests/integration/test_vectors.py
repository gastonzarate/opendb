"""Real PostgreSQL/pgvector behavior with a deterministic local embedder."""

from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from django.conf import settings
from psycopg import sql
from psycopg.pq import TransactionStatus

from opendb.vectors.embeddings import MODEL_ID

pytestmark = pytest.mark.django_db(transaction=True)


class FakeEmbedder:
    model_id = MODEL_ID

    dimension = 384
    context_size = 512

    def tokenize(self, text, *, add_special=False):
        result = [ord(char) + 10 for char in text]
        return [1, *result, 2] if add_special else result

    def detokenize(self, tokens):
        return "".join(chr(token - 10) for token in tokens if token > 2)

    def embed(self, text):
        # Deterministic geometry, not a pretend semantic model.
        values = [float("cat" in text.lower()), float("dog" in text.lower()), 0.1]
        return values + [0.0] * 381


@pytest.fixture
def registered(owner_conn, admin_conn, personal_db):
    from opendb import vectors

    vectors.install(admin_conn, personal_db.role_name)
    owner_conn.execute(
        "CREATE TABLE data.notes (id integer PRIMARY KEY, body text, label text)"
    )
    owner_conn.execute(
        "INSERT INTO data.notes VALUES (1, 'cat naps', 'public'), "
        "(2, 'dog walks', 'private')"
    )
    return vectors.register(admin_conn, "notes", "id", "body")


@contextmanager
def restricted(admin_conn):
    role = "vector_test_" + uuid4().hex
    admin_conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    admin_conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA data, opendb_catalog TO {}").format(
            sql.Identifier(role)
        )
    )
    with psycopg.connect(
        settings.OPENDB_ADMIN_DSN, dbname=admin_conn.info.dbname, autocommit=True
    ) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        try:
            yield conn, role
        finally:
            conn.execute("RESET ROLE")
            admin_conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            admin_conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_register_backfill_search_and_sql_vectors(registered, owner_conn, admin_conn):
    from opendb import vectors

    summary = vectors.process_pending(admin_conn, FakeEmbedder())
    assert summary["processed"] == 2
    hits = vectors.search(
        owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
    )
    assert [hit["source_key"] for hit in hits] == [1, 2]
    assert hits[0]["text"] == "cat naps"
    assert hits[0]["char_start"] == 0
    assert hits[0]["char_end"] == 8
    assert hits[0]["chunk_order"] == 0
    assert hits[0]["score"] > hits[1]["score"]
    assert vectors.status(owner_conn)[0]["ready"] == 2
    view = sql.Identifier("data", registered["view_name"])
    assert (
        owner_conn.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE embedding IS NOT NULL").format(view)
        ).fetchone()[0]
        == 2
    )
    assert (
        vectors.register(admin_conn, "data.notes", "id", "body")["index_id"]
        == registered["index_id"]
    )


def test_update_delete_key_change_and_rollback_never_show_stale(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    with owner_conn.transaction(force_rollback=True):
        owner_conn.execute("UPDATE data.notes SET body='changed' WHERE id=1")
        assert (
            len(
                vectors.search(
                    owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
                )
            )
            == 1
        )
    assert (
        len(
            vectors.search(
                owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
            )
        )
        == 2
    )
    owner_conn.execute("UPDATE data.notes SET id=3, body='cat new' WHERE id=1")
    owner_conn.execute("DELETE FROM data.notes WHERE id=2")
    assert (
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
        == []
    )
    vectors.process_pending(admin_conn, FakeEmbedder())
    assert [
        h["source_key"]
        for h in vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
    ] == [3]
    owner_conn.execute("TRUNCATE data.notes")
    assert (
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
        == []
    )


def test_worker_version_check_and_no_database_transaction_during_embedding(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    class Racing(FakeEmbedder):
        edited = False

        def embed(self, text):
            assert admin_conn.info.transaction_status == TransactionStatus.IDLE
            if not self.edited:
                self.edited = True
                owner_conn.execute("UPDATE data.notes SET body='cat latest' WHERE id=1")
            return super().embed(text)

    result = vectors.process_pending(admin_conn, Racing(), limit=1)
    assert result["superseded"] == 1
    assert (
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
        == []
    )
    vectors.process_pending(admin_conn, FakeEmbedder())
    hits = vectors.search(
        owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
    )
    assert hits[0]["text"] == "cat latest"


def test_failure_retries_without_damaging_source(registered, owner_conn, admin_conn):
    from opendb import vectors

    class Offline(FakeEmbedder):
        def embed(self, text):
            msg = "private service details must never enter status"
            raise RuntimeError(msg)

    assert vectors.process_pending(admin_conn, Offline())["failed"] == 2
    state = vectors.status(owner_conn)[0]
    assert state["failed"] == 2
    assert "private service details" not in str(state)
    assert owner_conn.execute("SELECT count(*) FROM data.notes").fetchone()[0] == 2
    assert vectors.process_pending(admin_conn, FakeEmbedder())["processed"] == 0
    admin_conn.execute(
        "UPDATE opendb_catalog.vector_rows SET retry_at=now() - interval '1 second'"
    )
    assert vectors.process_pending(admin_conn, FakeEmbedder())["processed"] == 2
    assert vectors.status(owner_conn)[0]["ready"] == 2


def test_search_and_status_honor_grants_and_view_only_guests(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    with restricted(admin_conn) as (guest, role):
        assert vectors.status(guest) == []
        with pytest.raises((PermissionError, psycopg.errors.InsufficientPrivilege)):
            vectors.search(
                guest, registered["index_id"], "cat", embedder=FakeEmbedder()
            )
        for table in ("vector_indexes", "vector_rows", "vector_chunks"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                guest.execute(
                    sql.SQL("SELECT * FROM opendb_catalog.{}").format(
                        sql.Identifier(table)
                    )
                )
        admin_conn.execute(
            sql.SQL("GRANT SELECT ON data.notes TO {}").format(sql.Identifier(role))
        )
        assert (
            len(
                vectors.search(
                    guest, registered["index_id"], "cat", embedder=FakeEmbedder()
                )
            )
            == 2
        )
        admin_conn.execute(
            sql.SQL("REVOKE SELECT ON data.notes FROM {}").format(sql.Identifier(role))
        )
        owner_conn.execute(
            sql.SQL(
                "CREATE VIEW data.approved_vectors WITH (security_barrier=true) "
                "AS SELECT * FROM data.{} WHERE source_key='1'::jsonb"
            ).format(sql.Identifier(registered["view_name"]))
        )
        admin_conn.execute(
            sql.SQL("GRANT SELECT ON data.approved_vectors TO {}").format(
                sql.Identifier(role)
            )
        )
        assert guest.execute("SELECT text FROM data.approved_vectors").fetchall() == [
            ("cat naps",)
        ]
        with pytest.raises((PermissionError, psycopg.errors.InsufficientPrivilege)):
            vectors.search(
                guest, registered["index_id"], "cat", embedder=FakeEmbedder()
            )
        assert vectors.status(guest) == []


@pytest.mark.parametrize(
    ("ddl", "key", "text"),
    [
        ("CREATE VIEW data.bad AS SELECT 1 id, 'x' body", "id", "body"),
        ("CREATE TABLE data.bad (id integer, body text)", "id", "body"),
        ("CREATE TABLE data.bad (id integer UNIQUE, body text)", "id", "body"),
        ("CREATE TABLE data.bad (id integer PRIMARY KEY, body integer)", "id", "body"),
        (
            "CREATE TABLE data.bad (id integer PRIMARY KEY, body text)",
            "missing",
            "body",
        ),
    ],
)
def test_register_validates_real_source(ddl, key, text, owner_conn, admin_conn):
    from opendb import vectors

    owner_conn.execute(ddl)
    with pytest.raises(ValueError, match="Source requires"):
        vectors.register(admin_conn, "bad", key, text)
    assert vectors.status(owner_conn) == []


def test_worker_refuses_callers_transaction(registered, admin_conn):
    from opendb import vectors

    with admin_conn.transaction(), pytest.raises(ValueError, match="idle connection"):
        vectors.process_pending(admin_conn, FakeEmbedder())


def test_expired_lease_cannot_overwrite_a_new_workers_publication(
    registered, admin_conn
):
    from opendb import vectors
    from opendb.vectors.indexing import _claim
    from opendb.vectors.indexing import _fail
    from opendb.vectors.indexing import _publish

    abandoned = _claim(admin_conn)
    admin_conn.execute(
        "UPDATE opendb_catalog.vector_rows "
        "SET retry_at=now()-interval '1 second' "
        "WHERE index_id=%s AND source_key='1'::jsonb",
        (registered["index_id"],),
    )
    assert vectors.process_pending(admin_conn, FakeEmbedder())["processed"] == 2
    assert _publish(admin_conn, abandoned, []) == "superseded"
    assert _fail(admin_conn, abandoned) == "superseded"
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM opendb_catalog.vector_chunks"
        ).fetchone()[0]
        == 2
    )


def test_delete_reinsert_same_key_is_not_an_aba_version_race(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    class Replacing(FakeEmbedder):
        replaced = False

        def embed(self, text):
            if not self.replaced:
                self.replaced = True
                owner_conn.execute("DELETE FROM data.notes WHERE id=1")
                owner_conn.execute(
                    "INSERT INTO data.notes VALUES (1,'cat replaced','public')"
                )
            return super().embed(text)

    assert vectors.process_pending(admin_conn, Replacing(), limit=1)["superseded"] == 1
    vectors.process_pending(admin_conn, FakeEmbedder())
    assert (
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )[0]["text"]
        == "cat replaced"
    )


def test_row_security_enabled_after_registration_fails_closed(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    owner_conn.execute("ALTER TABLE data.notes ENABLE ROW LEVEL SECURITY")
    assert vectors.status(owner_conn) == []
    with pytest.raises(PermissionError):
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
    assert (
        owner_conn.execute(
            sql.SQL("SELECT * FROM data.{}").format(
                sql.Identifier(registered["view_name"])
            )
        ).fetchall()
        == []
    )


def test_column_renaming_preserves_trigger_identity_and_provenance(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    owner_conn.execute("ALTER TABLE data.notes RENAME COLUMN body TO content")
    owner_conn.execute("ALTER TABLE data.notes RENAME COLUMN id TO key")
    owner_conn.execute("UPDATE data.notes SET content='cat renamed' WHERE key=1")
    vectors.process_pending(admin_conn, FakeEmbedder())
    assert (
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )[0]["text"]
        == "cat renamed"
    )
    assert vectors.status(owner_conn)[0]["key_column"] == "key"


def test_partial_multichunk_failure_publishes_nothing(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    owner_conn.execute("DELETE FROM data.notes WHERE id=2")
    owner_conn.execute("UPDATE data.notes SET body='cat cat cat cat cat' WHERE id=1")

    class Partial(FakeEmbedder):
        context_size = 8
        calls = 0

        def embed(self, text):
            self.calls += 1
            if self.calls == 2:
                msg = "failed in middle"
                raise RuntimeError(msg)
            return super().embed(text)

    assert vectors.process_pending(admin_conn, Partial(), limit=2)["failed"] == 1
    hits = vectors.search(
        owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
    )
    assert all(h["source_key"] != 1 for h in hits)


def test_model_mismatch_fails_without_publishing(registered, owner_conn, admin_conn):
    from opendb import vectors

    other = FakeEmbedder()
    other.model_id = "different-weights"
    assert vectors.process_pending(admin_conn, other)["failed"] == 2
    assert (
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
        == []
    )


def test_empty_and_null_source_are_ready_without_embeddings(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    owner_conn.execute(
        "UPDATE data.notes SET body=CASE WHEN id=1 THEN '' ELSE NULL END"
    )

    class NoRequests(FakeEmbedder):
        def embed(self, text):
            pytest.fail("Empty content should not make an embedding request")

    assert vectors.process_pending(admin_conn, NoRequests())["processed"] == 2
    assert vectors.status(owner_conn)[0]["ready"] == 2
    assert (
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
        == []
    )


def test_worker_cli_processes_ready_personal_database(
    registered, personal_db, monkeypatch, capsys
):
    from opendb.vectors import indexing
    from opendb.vectors.worker import main

    monkeypatch.setattr(indexing, "LocalEmbedder", FakeEmbedder)
    assert main(["--once", "--database-id", str(personal_db.id)]) == 0
    import json

    summary = json.loads(capsys.readouterr().out)
    assert summary["database_id"] == str(personal_db.id)
    assert summary["processed"] == 2


def test_live_model_semantic_pgvector_search(owner_conn, admin_conn, personal_db):
    import os

    from opendb import vectors
    from opendb.vectors.embeddings import LocalEmbedder

    endpoint = os.environ.get("OPENDB_REAL_EMBEDDINGS_URL")
    if not endpoint:
        pytest.skip("Set OPENDB_REAL_EMBEDDINGS_URL for the pinned model smoke test")
    vectors.install(admin_conn, personal_db.role_name)
    owner_conn.execute("CREATE TABLE data.semantic (id integer PRIMARY KEY, body text)")
    with owner_conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO data.semantic VALUES (%s,%s)",
            [
                (1, "A sleepy kitten rests on the sofa."),
                (2, "The dog chases a ball in the park."),
                (3, "A database uses indexes to speed up queries."),
            ],
        )
    index = vectors.register(admin_conn, "semantic", "id", "body")
    embedder = LocalEmbedder(endpoint)
    assert vectors.process_pending(admin_conn, embedder)["processed"] == 3
    hits = vectors.search(
        owner_conn, index["index_id"], "Where is the cat sleeping?", embedder=embedder
    )
    assert [h["source_key"] for h in hits] == [1, 2, 3]
    assert hits[0]["score"] > hits[2]["score"] + 0.1


def test_grant_revocation_during_query_embedding_is_rechecked(registered, admin_conn):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    with restricted(admin_conn) as (guest, role):
        admin_conn.execute(
            sql.SQL("GRANT SELECT ON data.notes TO {}").format(sql.Identifier(role))
        )

        class Revoking(FakeEmbedder):
            def embed(self, text):
                admin_conn.execute(
                    sql.SQL("REVOKE SELECT ON data.notes FROM {}").format(
                        sql.Identifier(role)
                    )
                )
                return super().embed(text)

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            vectors.search(guest, registered["index_id"], "cat", embedder=Revoking())


def test_multiple_registered_columns_and_insert_capture(owner_conn, admin_conn):
    from opendb import vectors

    owner_conn.execute(
        "CREATE TABLE data.multi (key text NOT NULL UNIQUE, title text, body text)"
    )
    title = vectors.register(admin_conn, "multi", "key", "title")
    body = vectors.register(admin_conn, "multi", "key", "body")
    owner_conn.execute(
        "INSERT INTO data.multi VALUES ('note-1','cat title','dog body')"
    )
    assert vectors.process_pending(admin_conn, FakeEmbedder())["processed"] == 2
    assert (
        vectors.search(owner_conn, title["index_id"], "cat", embedder=FakeEmbedder())[
            0
        ]["text"]
        == "cat title"
    )
    assert (
        vectors.search(owner_conn, body["index_id"], "dog", embedder=FakeEmbedder())[0][
            "source_key"
        ]
        == "note-1"
    )
    owner_conn.execute("UPDATE data.multi SET body='cat body'")
    assert (
        vectors.search(owner_conn, body["index_id"], "dog", embedder=FakeEmbedder())
        == []
    )


def test_direct_sql_cannot_bypass_search_authorization(registered, admin_conn):
    import json

    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    with restricted(admin_conn) as (guest, _role):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            guest.execute(
                "SELECT opendb_catalog.vector_search(%s,%s,%s,10)",
                (
                    registered["index_id"],
                    json.dumps(FakeEmbedder().embed("cat")),
                    MODEL_ID,
                ),
            ).fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            guest.execute("SELECT opendb_catalog.vector_capture()")


def test_install_refuses_schema_owned_by_the_personal_role(
    owner_conn, admin_conn, personal_db
):
    from opendb import vectors

    admin_conn.execute(
        sql.SQL("ALTER SCHEMA opendb_catalog OWNER TO {}").format(
            sql.Identifier(personal_db.role_name)
        )
    )
    with pytest.raises(ValueError, match="ownership"):
        vectors.install(admin_conn, personal_db.role_name)


@pytest.mark.parametrize("column", ["body", "id"])
def test_dropped_registered_column_hides_index_and_cannot_be_resurrected(
    registered,
    owner_conn,
    admin_conn,
    column,
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    owner_conn.execute(
        sql.SQL("ALTER TABLE data.notes DROP COLUMN {} CASCADE").format(
            sql.Identifier(column)
        ),
    )
    assert vectors.status(owner_conn) == []
    with pytest.raises(PermissionError, match="unavailable"):
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
    owner_conn.execute(
        sql.SQL("ALTER TABLE data.notes ADD COLUMN {} text").format(
            sql.Identifier(column)
        ),
    )
    assert vectors.status(owner_conn) == []


def test_key_constraint_removed_hides_api_and_sql_vectors(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    owner_conn.execute("ALTER TABLE data.notes DROP CONSTRAINT notes_pkey")
    assert vectors.status(owner_conn) == []
    with pytest.raises(PermissionError, match="unavailable"):
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
    assert (
        owner_conn.execute(
            sql.SQL("SELECT * FROM data.{}").format(
                sql.Identifier(registered["view_name"])
            ),
        ).fetchall()
        == []
    )


def test_column_dropped_during_embedding_prevents_publication(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    class Dropping(FakeEmbedder):
        def embed(self, text):
            owner_conn.execute("ALTER TABLE data.notes DROP COLUMN body CASCADE")
            return super().embed(text)

    result = vectors.process_pending(admin_conn, Dropping(), limit=1)
    assert result == {"processed": 0, "failed": 0, "superseded": 1}
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM opendb_catalog.vector_chunks"
        ).fetchone()[0]
        == 0
    )
    assert vectors.process_pending(admin_conn, FakeEmbedder())["processed"] == 0


def test_unchanged_key_and_text_preserve_ready_version_and_chunks(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    before = vectors.search(
        owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
    )
    owner_conn.execute("UPDATE data.notes SET body=body, id=id")
    owner_conn.execute("UPDATE data.notes SET label='new label' WHERE id=1")
    assert (
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
        == before
    )
    assert vectors.status(owner_conn)[0]["ready"] == 2
    assert vectors.process_pending(admin_conn, FakeEmbedder())["processed"] == 0


def test_metadata_only_update_does_not_reset_failed_retry(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    class Offline(FakeEmbedder):
        def embed(self, text):
            raise ConnectionError

    vectors.process_pending(admin_conn, Offline())
    before = admin_conn.execute(
        "SELECT source_key,version,attempts,retry_at "
        "FROM opendb_catalog.vector_rows ORDER BY source_key",
    ).fetchall()
    owner_conn.execute("UPDATE data.notes SET label='retry stays scheduled'")
    assert (
        admin_conn.execute(
            "SELECT source_key,version,attempts,retry_at "
            "FROM opendb_catalog.vector_rows ORDER BY source_key",
        ).fetchall()
        == before
    )


def test_semantic_source_filters_apply_before_ranking_limit(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    hits = vectors.search(
        owner_conn,
        registered["index_id"],
        "cat",
        limit=1,
        embedder=FakeEmbedder(),
        filters={"label": "private"},
    )
    assert [h["source_key"] for h in hits] == [2]
    assert (
        vectors.search(
            owner_conn,
            registered["index_id"],
            "cat",
            embedder=FakeEmbedder(),
            filters={"label": "missing"},
        )
        == []
    )
    owner_conn.execute("UPDATE data.notes SET label=NULL WHERE id=2")
    assert (
        vectors.search(
            owner_conn,
            registered["index_id"],
            "cat",
            limit=1,
            embedder=FakeEmbedder(),
            filters={"label": None},
        )[0]["source_key"]
        == 2
    )
    with pytest.raises((ValueError, psycopg.errors.InvalidParameterValue)):
        vectors.search(
            owner_conn,
            registered["index_id"],
            "cat",
            embedder=FakeEmbedder(),
            filters={"missing": "private"},
        )


def test_view_only_search_respects_projection_filter_and_limit(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    with restricted(admin_conn) as (guest, role):
        owner_conn.execute(
            sql.SQL("""
            CREATE VIEW data.approved WITH (security_barrier=true) AS
            SELECT index_id,source_key,version,chunk_order,char_start,char_end,
                   token_start,token_end,'redacted'::text AS text,embedding,model_id,
                   'shared'::text AS audience
            FROM data.{} WHERE source_key='2'::jsonb
        """).format(sql.Identifier(registered["view_name"]))
        )
        admin_conn.execute(
            sql.SQL("GRANT SELECT ON data.approved TO {}").format(sql.Identifier(role))
        )
        admin_conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
        )
        hits = vectors.search(
            guest,
            registered["index_id"],
            "cat",
            limit=1,
            embedder=FakeEmbedder(),
            target_view="approved",
            filters={"audience": "shared"},
        )
        assert [h["source_key"] for h in hits] == [2]
        assert hits[0]["text"] == "redacted"
        assert (
            vectors.search(
                guest,
                registered["index_id"],
                "cat",
                embedder=FakeEmbedder(),
                target_view="data.approved",
                filters={"source_key": 1},
            )
            == []
        )
        with pytest.raises(PermissionError, match="unavailable"):
            vectors.search(
                guest, registered["index_id"], "cat", embedder=FakeEmbedder()
            )
        with pytest.raises((ValueError, psycopg.errors.InvalidParameterValue)):
            vectors.search(
                guest,
                registered["index_id"],
                "cat",
                embedder=FakeEmbedder(),
                target_view="approved",
                filters={"label": "private"},
            )
        owner_conn.execute("UPDATE data.notes SET body='changed' WHERE id=2")
        assert (
            vectors.search(
                guest,
                registered["index_id"],
                "cat",
                embedder=FakeEmbedder(),
                target_view="approved",
            )
            == []
        )


def test_ungranted_or_unrelated_views_cannot_authorize_search(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    with restricted(admin_conn) as (guest, role):
        owner_conn.execute(
            sql.SQL("CREATE VIEW data.private_vectors AS SELECT * FROM data.{}").format(
                sql.Identifier(registered["view_name"])
            )
        )
        owner_conn.execute("CREATE VIEW data.unrelated AS SELECT 1 AS value")
        admin_conn.execute(
            sql.SQL("GRANT SELECT ON data.unrelated TO {}").format(sql.Identifier(role))
        )
        for view in ("private_vectors", "unrelated", "public.unrelated", "notes"):
            with pytest.raises(
                (PermissionError, ValueError), match=r"unavailable|data view"
            ):
                vectors.search(
                    guest,
                    registered["index_id"],
                    "cat",
                    embedder=FakeEmbedder(),
                    target_view=view,
                )


def test_view_search_checks_grant_again_after_inference(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    with restricted(admin_conn) as (guest, role):
        owner_conn.execute(
            sql.SQL("CREATE VIEW data.revocable AS SELECT * FROM data.{}").format(
                sql.Identifier(registered["view_name"])
            )
        )
        admin_conn.execute(
            sql.SQL("GRANT SELECT ON data.revocable TO {}").format(sql.Identifier(role))
        )
        admin_conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
        )

        class Revoking(FakeEmbedder):
            def embed(self, text):
                admin_conn.execute(
                    sql.SQL("REVOKE SELECT ON data.revocable FROM {}").format(
                        sql.Identifier(role)
                    )
                )
                return super().embed(text)

        with pytest.raises((PermissionError, psycopg.errors.InsufficientPrivilege)):
            vectors.search(
                guest,
                registered["index_id"],
                "cat",
                embedder=Revoking(),
                target_view="revocable",
            )


def test_view_search_cannot_cross_index_boundary(registered, owner_conn, admin_conn):
    from opendb import vectors

    owner_conn.execute("CREATE TABLE data.other (id integer PRIMARY KEY, body text)")
    other = vectors.register(admin_conn, "other", "id", "body")
    with restricted(admin_conn) as (guest, role):
        owner_conn.execute(
            sql.SQL("CREATE VIEW data.only_notes AS SELECT * FROM data.{}").format(
                sql.Identifier(registered["view_name"])
            )
        )
        admin_conn.execute(
            sql.SQL("GRANT SELECT ON data.only_notes TO {}").format(
                sql.Identifier(role)
            )
        )
        with pytest.raises(PermissionError, match="unavailable"):
            vectors.search(
                guest,
                other["index_id"],
                "cat",
                embedder=FakeEmbedder(),
                target_view="only_notes",
            )


@pytest.mark.parametrize(
    "filters",
    [
        {"label": ["private"]},
        {"label": {"sql": "true"}},
        {"label": float("nan")},
        {str(i): i for i in range(17)},
    ],
)
def test_filter_contract_rejects_non_scalar_conditions(registered, owner_conn, filters):
    from opendb import vectors

    with pytest.raises(ValueError, match=r"filter|Filter"):
        vectors.search(
            owner_conn,
            registered["index_id"],
            "cat",
            embedder=FakeEmbedder(),
            filters=filters,
        )


def test_filtered_source_search_honors_readonly_guest_and_bound_values(
    registered, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    with restricted(admin_conn) as (guest, role):
        admin_conn.execute(
            sql.SQL("GRANT SELECT ON data.notes TO {}").format(sql.Identifier(role))
        )
        guest.execute("SET default_transaction_read_only=on")
        hits = vectors.search(
            guest,
            registered["index_id"],
            "cat",
            embedder=FakeEmbedder(),
            filters={"label": "private"},
        )
        assert [h["source_key"] for h in hits] == [2]
        assert (
            vectors.search(
                guest,
                registered["index_id"],
                "cat",
                embedder=FakeEmbedder(),
                filters={"label": "private' OR true --"},
            )
            == []
        )


def test_source_rewrite_without_row_trigger_never_returns_stale_text(
    registered, owner_conn, admin_conn
):
    from opendb import vectors

    vectors.process_pending(admin_conn, FakeEmbedder())
    # Drop the generated view to permit PostgreSQL's ALTER TYPE rewrite. This
    # rewrites source data without firing row UPDATE triggers.
    owner_conn.execute(
        sql.SQL("DROP VIEW data.{}").format(sql.Identifier(registered["view_name"]))
    )
    owner_conn.execute(
        "ALTER TABLE data.notes ALTER COLUMN body TYPE text USING upper(body)"
    )
    assert (
        vectors.search(
            owner_conn, registered["index_id"], "cat", embedder=FakeEmbedder()
        )
        == []
    )
