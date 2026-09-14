"""Automatic indexing against the disposable PostgreSQL cluster."""

import json

import pytest
from psycopg import sql

from opendb import vectors
from opendb.vectors import indexing
from opendb.vectors.worker import main
from tests.integration.test_vectors import FakeEmbedder

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def deterministic_embeddings(monkeypatch):
    monkeypatch.setattr(indexing, "LocalEmbedder", FakeEmbedder)


def run_worker(personal_db, capsys, *args):
    code = main(["--once", "--database-id", str(personal_db.id), *args])
    return code, json.loads(capsys.readouterr().out)


def test_discovers_existing_long_text_and_preserves_updates(
    owner_conn, admin_conn, personal_db, capsys
):
    owner_conn.execute("CREATE TABLE data.notes (id int PRIMARY KEY, detail varchar)")
    original = "ñ" * 500
    owner_conn.execute("INSERT INTO data.notes VALUES (1,%s), (2,'short')", (original,))
    code, result = run_worker(personal_db, capsys)
    assert code == 0
    assert result["processed"] == 2
    state = vectors.status(owner_conn)
    assert len(state) == 1
    assert state[0]["ready"] == 2
    assert owner_conn.execute(
        "SELECT detail FROM data.notes WHERE id=1"
    ).fetchone() == (original,)
    assert admin_conn.execute(
        "SELECT string_agg(text,'' ORDER BY chunk_order) "
        "FROM opendb_catalog.vector_chunks WHERE source_key='1'"
    ).fetchone() == (original,)
    before = admin_conn.execute(
        "SELECT source_key,version FROM opendb_catalog.vector_rows ORDER BY source_key"
    ).fetchall()
    assert run_worker(personal_db, capsys)[1]["processed"] == 0
    assert (
        admin_conn.execute(
            "SELECT source_key,version FROM opendb_catalog.vector_rows "
            "ORDER BY source_key"
        ).fetchall()
        == before
    )
    owner_conn.execute("UPDATE data.notes SET detail='updated' WHERE id=1")
    owner_conn.execute("INSERT INTO data.notes VALUES (3,'new')")
    assert run_worker(personal_db, capsys)[1]["processed"] == 2
    assert vectors.status(owner_conn)[0]["ready"] == 3


def test_discovers_53_short_turns_without_merging_structure(
    owner_conn, personal_db, capsys
):
    owner_conn.execute(
        "CREATE TABLE data.turns (id int PRIMARY KEY, speaker text, body text)"
    )
    with owner_conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO data.turns VALUES (%s,'speaker',%s)",
            [(n, f"Turn {n}: cat naps.") for n in range(53)],
        )
    code, result = run_worker(personal_db, capsys, "--limit", "100")
    assert code == 0
    assert result["processed"] == 53
    state = vectors.status(owner_conn)
    assert [(s["text_column"], s["ready"]) for s in state] == [("body", 53)]
    assert owner_conn.execute(
        "SELECT count(*),count(DISTINCT id) FROM data.turns"
    ).fetchone() == (53, 53)


def test_threshold_configuration_and_later_growth(
    owner_conn, personal_db, capsys, monkeypatch
):
    monkeypatch.setenv("OPENDB_VECTOR_MIN_TEXT_CHARS", "600")
    monkeypatch.setenv("OPENDB_VECTOR_NARRATIVE_COLUMNS", "")
    owner_conn.execute(
        "CREATE TABLE data.notes (id int PRIMARY KEY, body text, detail varchar)"
    )
    owner_conn.execute("INSERT INTO data.notes VALUES (1,'short',%s)", ("x" * 599,))
    assert run_worker(personal_db, capsys)[1]["processed"] == 0
    assert vectors.status(owner_conn) == []
    owner_conn.execute("UPDATE data.notes SET detail=detail || 'x'")
    assert run_worker(personal_db, capsys)[1]["processed"] == 1
    assert vectors.status(owner_conn)[0]["text_column"] == "detail"


@pytest.mark.parametrize(
    "key",
    [
        "",
        "id int",
        "id int UNIQUE",
        "id numeric PRIMARY KEY",
        "id int, other int, PRIMARY KEY(id,other)",
    ],
)
def test_surrogate_handles_missing_nullable_nonunique_and_unsupported_keys(
    owner_conn, personal_db, capsys, key
):
    owner_conn.execute(
        sql.SQL("CREATE TABLE data.notes ({})").format(
            sql.SQL(key + ", body text" if key else "body text")
        )
    )
    if "other" in key:
        owner_conn.execute("INSERT INTO data.notes VALUES (1,1,'one'),(1,2,'two')")
    elif key:
        owner_conn.execute("INSERT INTO data.notes VALUES (1,'one'),(2,'two')")
    else:
        owner_conn.execute("INSERT INTO data.notes VALUES ('one'),('two')")
    code, result = run_worker(personal_db, capsys)
    assert code == 0
    assert result["processed"] == 2
    state = vectors.status(owner_conn)[0]
    assert state["key_column"] != "id"
    if "other" in key:
        owner_conn.execute(
            "INSERT INTO data.notes (body,id,other) VALUES ('three',3,3)"
        )
    elif key:
        owner_conn.execute("INSERT INTO data.notes (body,id) VALUES ('three',3)")
    else:
        owner_conn.execute("INSERT INTO data.notes (body) VALUES ('three')")
    assert run_worker(personal_db, capsys)[1]["processed"] == 1
    assert vectors.status(owner_conn)[0]["ready"] == 3
    assert len(vectors.status(owner_conn)) == 1


def test_column_identity_and_surrogate_name_collisions(owner_conn, personal_db, capsys):
    owner_conn.execute(
        'CREATE TABLE data."odd.table" '
        "(_opendb_vector_id text, body text, content varchar)"
    )
    owner_conn.execute(
        "INSERT INTO data.\"odd.table\" VALUES ('keep','body','content')"
    )
    assert run_worker(personal_db, capsys)[1]["processed"] == 2
    state = vectors.status(owner_conn)
    assert {s["text_column"] for s in state} == {"body", "content"}
    assert len({s["key_column"] for s in state}) == 1
    assert len({s["index_id"] for s in state}) == 2
    assert owner_conn.execute(
        'SELECT _opendb_vector_id FROM data."odd.table"'
    ).fetchone() == ("keep",)
    assert run_worker(personal_db, capsys)[1]["processed"] == 0


def test_unsupported_sources_report_failure_without_bypassing_rls(
    owner_conn, personal_db, capsys
):
    owner_conn.execute("CREATE TABLE data.secret (id int PRIMARY KEY, body text)")
    owner_conn.execute("INSERT INTO data.secret VALUES (1,'private')")
    owner_conn.execute("ALTER TABLE data.secret ENABLE ROW LEVEL SECURITY")
    owner_conn.execute("CREATE VIEW data.secret_view AS SELECT * FROM data.secret")
    owner_conn.execute("CREATE TABLE data.safe (id int PRIMARY KEY, body text)")
    owner_conn.execute("INSERT INTO data.safe VALUES (1,'public')")
    code, result = run_worker(personal_db, capsys)
    assert code == 1
    assert result["discovery_failed"] == 1
    assert result["processed"] == 1
    assert {s["table"] for s in vectors.status(owner_conn)} == {"data.safe"}
    assert "private" not in json.dumps(result)
    owner_conn.execute("DROP VIEW data.secret_view")
    owner_conn.execute("ALTER TABLE data.secret DISABLE ROW LEVEL SECURITY")
    code, result = run_worker(personal_db, capsys)
    assert code == 0
    assert result["processed"] == 1


def test_blank_narrative_waits_for_nonblank(owner_conn, personal_db, capsys):
    owner_conn.execute("CREATE TABLE data.notes (id int PRIMARY KEY, body text)")
    owner_conn.execute("INSERT INTO data.notes VALUES (1,E' \\n\\t'),(2,NULL)")
    assert run_worker(personal_db, capsys)[1]["processed"] == 0
    assert vectors.status(owner_conn) == []
    owner_conn.execute("UPDATE data.notes SET body='one turn' WHERE id=1")
    assert run_worker(personal_db, capsys)[1]["processed"] == 2


def test_discovery_respects_schema_lock_and_retries(owner_conn, admin_conn):
    from opendb.vectors.discovery import discover
    from opendb.vectors.schema import SCHEMA_LOCK_KEY

    owner_conn.execute("CREATE TABLE data.notes (body text)")
    owner_conn.execute("INSERT INTO data.notes VALUES ('one turn')")
    with owner_conn.transaction():
        owner_conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        result = discover(admin_conn)
        assert result["registered"] == 0
        assert result["discovery_failed"] == 1
        assert vectors.status(owner_conn) == []
    assert discover(admin_conn)["registered"] == 1
    assert discover(admin_conn)["registered"] == 0


def test_table_replacement_during_discovery_retries_new_identity(
    owner_conn, admin_conn, monkeypatch
):
    from opendb.vectors import discovery

    owner_conn.execute("CREATE TABLE data.notes (body text)")
    owner_conn.execute("INSERT INTO data.notes VALUES ('old')")
    candidates = discovery._candidates  # noqa: SLF001 - insert real DDL at race boundary.

    def replace_after_snapshot(conn):
        result = candidates(conn)
        owner_conn.execute("DROP TABLE data.notes")
        owner_conn.execute("CREATE TABLE data.notes (body text)")
        owner_conn.execute("INSERT INTO data.notes VALUES ('new')")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(discovery, "_candidates", replace_after_snapshot)
        assert discovery.discover(admin_conn)["discovery_failed"] == 1
        assert vectors.status(owner_conn) == []
    assert discovery.discover(admin_conn)["registered"] == 1
    assert vectors.process_pending(admin_conn, FakeEmbedder())["processed"] == 1
    assert admin_conn.execute(
        "SELECT source_text FROM opendb_catalog.vector_rows"
    ).fetchall() == [("new",)]


def test_concurrent_discovery_creates_one_surrogate_and_registration(
    owner_conn, admin_conn
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import psycopg
    from django.conf import settings

    from opendb.vectors.discovery import discover

    owner_conn.execute("CREATE TABLE data.notes (body text)")
    owner_conn.execute("INSERT INTO data.notes VALUES ('one turn')")
    barrier = Barrier(2)

    def scan():
        with psycopg.connect(
            settings.OPENDB_ADMIN_DSN, dbname=admin_conn.info.dbname, autocommit=True
        ) as conn:
            barrier.wait(timeout=5)
            return discover(conn)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: scan(), range(2)))
    assert sum(r["registered"] for r in results) == 1
    assert sum(r["discovery_failed"] for r in results) == 0
    assert owner_conn.execute(
        "SELECT count(*) FROM pg_attribute WHERE attrelid='data.notes'::regclass "
        "AND attnum>0 AND NOT attisdropped"
    ).fetchone() == (2,)


def test_failed_surrogate_registration_rolls_back_then_retries(
    owner_conn, admin_conn, monkeypatch
):
    from opendb.vectors import discovery

    owner_conn.execute("CREATE TABLE data.notes (body text)")
    owner_conn.execute("INSERT INTO data.notes VALUES ('one turn')")
    register = discovery.register

    def fail_after_registration(*args):
        register(*args)
        msg = "synthetic failure containing source text must not be logged"
        raise RuntimeError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(discovery, "register", fail_after_registration)
        result = discovery.discover(admin_conn)
        assert result["discovery_failed"] == 1
        assert "synthetic" not in json.dumps(result)
    assert vectors.status(owner_conn) == []
    assert owner_conn.execute(
        "SELECT count(*) FROM pg_attribute WHERE attrelid='data.notes'::regclass "
        "AND attnum>0 AND NOT attisdropped"
    ).fetchone() == (1,)
    assert discovery.discover(admin_conn)["registered"] == 1
    assert vectors.process_pending(admin_conn, FakeEmbedder())["processed"] == 1


def test_configuration_custom_narrative_and_invalid_threshold(
    owner_conn, personal_db, capsys, monkeypatch
):
    monkeypatch.setenv("OPENDB_VECTOR_NARRATIVE_COLUMNS", " summary ")
    owner_conn.execute(
        "CREATE TABLE data.notes (id int PRIMARY KEY, summary text, body text)"
    )
    owner_conn.execute("INSERT INTO data.notes VALUES (1,'short','also short')")
    assert run_worker(personal_db, capsys)[1]["processed"] == 1
    assert vectors.status(owner_conn)[0]["text_column"] == "summary"
    monkeypatch.setenv("OPENDB_VECTOR_MIN_TEXT_CHARS", "0")
    with pytest.raises(SystemExit) as error:
        main(["--once", "--database-id", str(personal_db.id)])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "index_definition",
    [
        "CREATE INDEX custom_expression ON data.notes ((data.owner_only(id)))",
        "CREATE INDEX custom_partial ON data.notes(id) WHERE data.owner_only(1)=1",
    ],
)
def test_custom_index_expressions_rejected_before_privileged_evaluation(
    owner_conn, admin_conn, index_definition
):
    from opendb.vectors.discovery import discover

    owner_conn.execute("CREATE TABLE data.notes (id int PRIMARY KEY, body text)")
    owner_conn.execute("INSERT INTO data.notes VALUES (1,'a turn')")
    owner_conn.execute("""
        CREATE FUNCTION data.owner_only(value int) RETURNS int
        LANGUAGE plpgsql IMMUTABLE AS $$ BEGIN
            IF current_user NOT LIKE 'odb_owner_%' THEN
                RAISE EXCEPTION 'evaluated as privileged user';
            END IF;
            RETURN value;
        END $$
    """)
    owner_conn.execute(index_definition)
    result = discover(admin_conn)
    assert result["registered"] == 0
    assert result["discovery_errors"][0]["error_code"] == "unsupported_text_source"
    with pytest.raises(ValueError, match=r"expression|partial"):
        vectors.register(admin_conn, "notes", "id", "body")
    assert vectors.status(owner_conn) == []


def test_fixed_char_long_text_is_discovered(owner_conn, personal_db, capsys):
    owner_conn.execute("CREATE TABLE data.notes (id int PRIMARY KEY, detail char(500))")
    owner_conn.execute("INSERT INTO data.notes VALUES (1,%s)", ("x" * 500,))
    assert run_worker(personal_db, capsys)[1]["processed"] == 1
    assert vectors.status(owner_conn)[0]["text_column"] == "detail"
