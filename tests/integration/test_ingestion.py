"""Catalog and ingestion acceptance tests against restricted PostgreSQL roles."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from django.conf import settings
from psycopg import sql

from opendb import catalog
from opendb import ingestion

pytestmark = pytest.mark.django_db(transaction=True)


def typed(kind, value):
    return {"type": kind, "value": value}


def expense_operation(conn, key="receipt-1"):
    return {
        "version": 1,
        "idempotency_key": key,
        "expected_schema_fingerprint": catalog.describe(conn)["fingerprint"],
        "source": {"media_type": "text/plain", "content": "Coffee 12.50; date unknown"},
        "statements": [
            (
                "CREATE TABLE data.expenses (id bigint GENERATED ALWAYS "
                "AS IDENTITY PRIMARY KEY, receipt text UNIQUE, "
                "amount numeric(12,2) NOT NULL, spent_on date)"
            ),
        ],
        "records": [
            {
                "ref": "expense",
                "table": "expenses",
                "returning": ["id", "amount"],
                "values": {
                    "receipt": typed("text", "r1"),
                    "amount": typed("numeric", "12.50"),
                    "spent_on": typed("date", None),
                },
            }
        ],
        "annotations": [
            {
                "table": "expenses",
                "description": "Household expenses",
                "metadata": {"units": "USD", "conventions": "Unknown dates are NULL"},
            }
        ],
    }


@pytest.fixture
def guest_conn(admin_conn):
    role = "ingestion_guest_" + uuid4().hex
    admin_conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    admin_conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA data TO {}").format(sql.Identifier(role))
    )
    try:
        with psycopg.connect(
            settings.OPENDB_ADMIN_DSN, dbname=admin_conn.info.dbname, autocommit=True
        ) as conn:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            yield conn, role
    finally:
        admin_conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        admin_conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_atomic_expenses_source_metadata_and_replay(
    owner_conn, admin_conn, personal_db
):
    catalog.install(admin_conn, personal_db.role_name)
    operation = expense_operation(owner_conn)
    result = ingestion.apply(owner_conn, operation)
    assert result["records"]["expense"]["amount"] == "12.50"
    assert result["indexing"]["status"] == "queued_for_discovery"
    assert ingestion.apply(owner_conn, copy.deepcopy(operation)) == result
    assert owner_conn.execute(
        "SELECT count(*), min(spent_on) FROM data.expenses"
    ).fetchone() == (1, None)
    stored = admin_conn.execute(
        "SELECT source, result FROM opendb_catalog.ingestion_operations",
    ).fetchone()
    assert stored == (operation["source"], result)
    described = catalog.describe(owner_conn)
    expense = next(item for item in described["objects"] if item["name"] == "expenses")
    assert expense["description"] == "Household expenses"
    assert expense["metadata"]["units"] == "USD"
    assert described["fingerprint"] == result["schema_fingerprint"]


def test_changed_payload_conflicts_without_duplicate_rows(owner_conn):
    operation = expense_operation(owner_conn)
    ingestion.apply(owner_conn, operation)
    operation["source"]["content"] = "private changed source"
    with pytest.raises(ingestion.IngestionError) as exc:
        ingestion.apply(owner_conn, operation)
    assert exc.value.code == "idempotency_conflict"
    assert "private" not in str(exc.value)
    assert owner_conn.execute("SELECT count(*) FROM data.expenses").fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["constraint", "annotation"])
def test_failure_rolls_back_ddl_rows_source_and_annotations(
    owner_conn, admin_conn, failure
):
    operation = expense_operation(owner_conn)
    before = catalog.describe(owner_conn)["fingerprint"]
    if failure == "constraint":
        duplicate = copy.deepcopy(operation["records"][0])
        duplicate["ref"] = "duplicate"
        operation["records"].append(duplicate)
    else:
        operation["annotations"].append(
            {
                "table": "missing_private_relation",
                "description": "private source",
                "metadata": {},
            }
        )
    with pytest.raises(ingestion.IngestionError) as exc:
        ingestion.apply(owner_conn, operation)
    assert "private" not in str(exc.value)
    assert (
        owner_conn.execute("SELECT to_regclass('data.expenses')").fetchone()[0] is None
    )
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM opendb_catalog.ingestion_operations"
        ).fetchone()[0]
        == 0
    )
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM opendb_catalog.annotations"
        ).fetchone()[0]
        == 0
    )
    assert catalog.describe(owner_conn)["fingerprint"] == before
    ingestion.apply(owner_conn, expense_operation(owner_conn))


def test_stale_fingerprint_conflicts_before_writing(owner_conn, admin_conn):
    operation = expense_operation(owner_conn)
    owner_conn.execute("CREATE TABLE data.concurrent_change (id integer)")
    with pytest.raises(ingestion.IngestionError) as exc:
        ingestion.apply(owner_conn, operation)
    assert exc.value.code == "schema_conflict"
    assert (
        owner_conn.execute("SELECT to_regclass('data.expenses')").fetchone()[0] is None
    )
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM opendb_catalog.ingestion_operations"
        ).fetchone()[0]
        == 0
    )


def test_transcript_preserves_relationships_order_and_unknown_identity(owner_conn):
    operation = expense_operation(owner_conn, "meeting-1")
    operation["source"]["content"] = "Unknown speaker: Hello. [00:03] Alice: Welcome."
    operation["statements"] = [
        (
            "CREATE TABLE data.meetings (id bigint GENERATED ALWAYS AS IDENTITY "
            "PRIMARY KEY, held_on date)"
        ),
        (
            "CREATE TABLE data.people (id bigint GENERATED ALWAYS AS IDENTITY "
            "PRIMARY KEY, external_id text UNIQUE, name text)"
        ),
        (
            "CREATE TABLE data.participants (id bigint GENERATED ALWAYS AS IDENTITY "
            "PRIMARY KEY, meeting_id bigint REFERENCES data.meetings, "
            "person_id bigint REFERENCES data.people, label text)"
        ),
        (
            "CREATE TABLE data.turns (id bigint GENERATED ALWAYS AS IDENTITY "
            "PRIMARY KEY, participant_id bigint REFERENCES data.participants, "
            "ordinal integer, body text, starts_at numeric, "
            "UNIQUE(participant_id, ordinal))"
        ),
    ]
    operation["records"] = [
        {
            "ref": "meeting",
            "table": "meetings",
            "returning": ["id"],
            "values": {"held_on": typed("date", None)},
        },
        {
            "ref": "speaker",
            "table": "participants",
            "returning": ["id"],
            "values": {
                "meeting_id": {"$ref": "meeting.id"},
                "person_id": typed("integer", None),
                "label": typed("text", "Unknown speaker"),
            },
        },
        {
            "ref": "turn",
            "table": "turns",
            "returning": ["id"],
            "values": {
                "participant_id": {"$ref": "speaker.id"},
                "ordinal": typed("integer", 1),
                "body": typed("text", "Hello."),
                "starts_at": typed("numeric", None),
            },
        },
    ]
    operation["annotations"] = []
    result = ingestion.apply(owner_conn, operation)
    assert result["records"]["turn"]["id"] > 0
    assert owner_conn.execute(
        "SELECT p.person_id, t.ordinal, t.body, t.starts_at, m.held_on "
        "FROM data.turns t JOIN data.participants p ON p.id=t.participant_id "
        "JOIN data.meetings m ON m.id=p.meeting_id",
    ).fetchone() == (None, 1, "Hello.", None, None)
    turns = next(
        obj for obj in catalog.describe(owner_conn)["objects"] if obj["name"] == "turns"
    )
    assert turns["foreign_keys"][0]["references"]["table"] == "participants"


def test_explicit_upsert_reuses_identity(owner_conn):
    first = ingestion.apply(owner_conn, expense_operation(owner_conn))
    operation = expense_operation(owner_conn, "receipt-corrected")
    operation["statements"] = []
    operation["records"][0]["values"]["amount"]["value"] = "15.25"
    operation["records"][0]["on_conflict"] = {
        "columns": ["receipt"],
        "update": ["amount"],
    }
    result = ingestion.apply(owner_conn, operation)
    assert result["records"]["expense"]["id"] == first["records"]["expense"]["id"]
    assert result["records"]["expense"]["amount"] == "15.25"
    assert owner_conn.execute("SELECT count(*) FROM data.expenses").fetchone()[0] == 1


def test_describe_filters_views_foreign_keys_and_private_annotations(
    owner_conn, admin_conn, guest_conn
):
    guest, role = guest_conn
    owner_conn.execute(
        "CREATE TABLE data.private_people (id integer PRIMARY KEY, secret text)"
    )
    owner_conn.execute(
        "CREATE TABLE data.public_items (id integer, "
        "person_id integer REFERENCES data.private_people)"
    )
    owner_conn.execute(
        "CREATE VIEW data.shared_names AS SELECT id FROM data.private_people"
    )
    admin_conn.execute(
        sql.SQL("GRANT SELECT ON data.public_items, data.shared_names TO {}").format(
            sql.Identifier(role)
        )
    )
    operation = expense_operation(owner_conn)
    operation["statements"] = []
    operation["records"] = []
    operation["annotations"] = [
        {
            "table": "shared_names",
            "description": "private_people.secret is confidential",
            "metadata": {},
        }
    ]
    ingestion.apply(owner_conn, operation)
    result = catalog.describe(guest)
    assert {item["name"] for item in result["objects"]} == {
        "public_items",
        "shared_names",
    }
    serialized = json.dumps(result)
    assert "private_people" not in serialized
    assert "secret" not in serialized
    assert "definition" not in serialized
    assert (
        next(obj for obj in result["objects"] if obj["name"] == "public_items")[
            "foreign_keys"
        ]
        == []
    )
    before = result["fingerprint"]
    owner_conn.execute("ALTER TABLE data.private_people ADD COLUMN more_secret text")
    assert catalog.describe(guest)["fingerprint"] == before


def test_catalog_metadata_functions_are_restricted_and_install_repeatable(
    owner_conn, admin_conn, personal_db, guest_conn
):
    guest, _ = guest_conn
    catalog.install(admin_conn, personal_db.role_name)
    catalog.install(admin_conn, personal_db.role_name)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), owner_conn.transaction():
        owner_conn.execute("DELETE FROM opendb_catalog.annotations")
    functions = admin_conn.execute(
        "SELECT p.oid, p.prosecdef, p.proconfig FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='opendb_catalog' AND p.proname IN "
        "('begin_ingestion','finish_ingestion','annotate','read_annotations')",
    ).fetchall()
    assert functions
    for oid, definer, config in functions:
        assert definer
        assert "search_path=pg_catalog, opendb_catalog, pg_temp" in config
        assert not guest.execute(
            "SELECT has_function_privilege(%s::oid, 'EXECUTE')", (oid,)
        ).fetchone()[0]
    with pytest.raises(ingestion.IngestionError) as exc:
        ingestion.apply(guest, expense_operation(guest))
    assert exc.value.code == "permission_denied"


@pytest.mark.parametrize(
    "statement",
    [
        "COMMIT",
        "SELECT 1",
        "INSERT INTO data.expenses DEFAULT VALUES",
        "CREATE TABLE data.a (id int); COMMIT",
        "CREATE TABLE opendb_catalog.intruder (id int)",
    ],
)
def test_statements_accept_only_policy_approved_transactional_ddl(
    owner_conn, statement
):
    operation = expense_operation(owner_conn)
    operation["statements"] = [statement]
    with pytest.raises(ingestion.IngestionError):
        ingestion.apply(owner_conn, operation)
    assert (
        owner_conn.execute("SELECT to_regclass('data.expenses')").fetchone()[0] is None
    )


def test_statistics_and_rows_do_not_change_schema_fingerprint(owner_conn):
    owner_conn.execute(
        "CREATE TABLE data.stats (id integer DEFAULT 1, "
        "amount numeric CHECK (amount>0))"
    )
    before = catalog.describe(owner_conn)["fingerprint"]
    owner_conn.execute("INSERT INTO data.stats VALUES (1, 12.50)")
    owner_conn.execute("ANALYZE data.stats")
    assert catalog.describe(owner_conn)["fingerprint"] == before
    owner_conn.execute("ALTER TABLE data.stats ALTER COLUMN id SET DEFAULT 2")
    assert catalog.describe(owner_conn)["fingerprint"] != before


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_ingestions_serialize_replay_or_schema_conflict(
    owner_conn, personal_db, same_key
):
    from opendb.databases.connections import connection_parameters

    operation = expense_operation(owner_conn)
    barrier = Barrier(2)

    def ingest(key):
        payload = copy.deepcopy(operation)
        payload["idempotency_key"] = key
        with psycopg.connect(
            **connection_parameters(personal_db, personal_db.role_name), autocommit=True
        ) as conn:
            barrier.wait(timeout=5)
            try:
                return ingestion.apply(conn, payload)
            except ingestion.IngestionError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(ingest, ["receipt-1", "receipt-1" if same_key else "receipt-2"])
        )
    if same_key:
        assert results[0] == results[1]
        assert isinstance(results[0], dict)
    else:
        assert sum(isinstance(result, dict) for result in results) == 1
        assert "schema_conflict" in results
    assert owner_conn.execute("SELECT count(*) FROM data.expenses").fetchone()[0] == 1


def test_apply_rejects_outer_transaction_without_committing_it(owner_conn):
    operation = expense_operation(owner_conn)
    with owner_conn.transaction():
        owner_conn.execute("CREATE TABLE data.outer_work (id integer)")
        with pytest.raises(ingestion.IngestionError) as exc:
            ingestion.apply(owner_conn, operation)
        assert exc.value.code == "transaction_active"
        assert owner_conn.execute("SELECT to_regclass('data.outer_work')").fetchone()[0]


def test_typed_values_must_match_real_column_type(owner_conn, admin_conn):
    operation = expense_operation(owner_conn)
    operation["records"][0]["values"]["amount"] = typed("text", "12.50")
    with pytest.raises(ingestion.IngestionError) as exc:
        ingestion.apply(owner_conn, operation)
    assert exc.value.code == "invalid_operation"
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM opendb_catalog.ingestion_operations"
        ).fetchone()[0]
        == 0
    )


def test_install_rejects_owner_and_foreign_schema_ownership_safely(
    owner_conn, admin_conn, personal_db
):
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.install(owner_conn, personal_db.role_name)
    assert exc.value.code == "catalog_install_failed"
    admin_conn.execute(
        sql.SQL("ALTER SCHEMA opendb_catalog OWNER TO {}").format(
            sql.Identifier(personal_db.role_name)
        )
    )
    try:
        with pytest.raises(catalog.CatalogError) as exc:
            catalog.install(admin_conn, personal_db.role_name)
        assert exc.value.code == "catalog_install_failed"
        assert personal_db.role_name not in str(exc.value)
    finally:
        admin_conn.execute(
            sql.SQL("ALTER SCHEMA opendb_catalog OWNER TO {}").format(
                sql.Identifier(admin_conn.info.user)
            )
        )


def test_catalog_reinstall_preserves_peer_read_helpers(
    admin_conn, personal_db, guest_conn
):
    guest, _ = guest_conn
    admin_conn.execute("GRANT USAGE ON SCHEMA opendb_catalog TO PUBLIC")
    catalog.install(admin_conn, personal_db.role_name)
    assert guest.execute(
        "SELECT has_schema_privilege('opendb_catalog', 'USAGE')",
    ).fetchone()[0]
    assert not guest.execute(
        "SELECT has_schema_privilege('opendb_catalog', 'CREATE')",
    ).fetchone()[0]
    assert not guest.execute(
        "SELECT has_function_privilege('opendb_catalog.read_annotations()', 'EXECUTE')",
    ).fetchone()[0]


def test_registered_text_ingestion_reports_pending_indexing(owner_conn, admin_conn):
    from opendb.vectors import register

    owner_conn.execute("CREATE TABLE data.notes (id integer PRIMARY KEY, body text)")
    registered = register(admin_conn, "notes", "id", "body")
    operation = expense_operation(owner_conn, "note-1")
    operation["statements"] = []
    operation["annotations"] = []
    operation["records"] = [
        {
            "table": "notes",
            "ref": "note",
            "returning": ["id"],
            "values": {"id": typed("integer", 1), "body": typed("text", "Hello.")},
        }
    ]
    result = ingestion.apply(owner_conn, operation)
    assert result["indexing"]["status"] == "pending"
    assert result["indexing"]["indexes"][0]["index_id"] == registered["index_id"]
    assert result["indexing"]["indexes"][0]["pending"] == 1


def test_fingerprint_is_search_path_independent_and_preserves_caller_state(owner_conn):
    owner_conn.execute("CREATE TABLE data.base (id integer)")
    owner_conn.execute("CREATE VIEW data.visible_base AS SELECT id FROM data.base")
    original = catalog.describe(owner_conn)
    with owner_conn.transaction():
        owner_conn.execute("SET LOCAL search_path = pg_catalog")
        assert catalog.describe(owner_conn) == original
        assert owner_conn.execute("SHOW search_path").fetchone()[0] == "pg_catalog"


@pytest.mark.parametrize("kind", ["meeting", "expenses"])
def test_exported_examples_execute_and_replay(owner_conn, admin_conn, kind):
    operation = ingestion.example_operation(
        kind, catalog.describe(owner_conn)["fingerprint"]
    )
    result = ingestion.apply(owner_conn, operation)
    assert ingestion.apply(owner_conn, operation) == result
    assert (
        admin_conn.execute(
            "SELECT source FROM opendb_catalog.ingestion_operations",
        ).fetchone()[0]
        == operation["source"]
    )
    if kind == "expenses":
        assert owner_conn.execute(
            "SELECT amount::text, currency, spent_on FROM data.expenses"
        ).fetchone() == ("12.50", "USD", None)
    else:
        assert owner_conn.execute(
            "SELECT t.ordinal, p.label, p.person_id, t.body, t.starts_at::text "
            "FROM data.turns t JOIN data.participants p ON p.id=t.participant_id "
            "ORDER BY t.ordinal",
        ).fetchall() == [
            (1, "Unknown speaker", None, "Hello.", None),
            (2, "Alice", None, "Welcome.", "3.000"),
        ]


def test_ready_existing_index_does_not_claim_new_column_is_indexed(
    owner_conn, admin_conn
):
    from opendb import vectors
    from tests.integration.test_vectors import FakeEmbedder

    owner_conn.execute("CREATE TABLE data.notes (id int PRIMARY KEY, body text)")
    owner_conn.execute("INSERT INTO data.notes VALUES (1,'existing')")
    vectors.register(admin_conn, "notes", "id", "body")
    vectors.process_pending(admin_conn, FakeEmbedder())
    operation = expense_operation(owner_conn, "new-column")
    operation["annotations"] = []
    operation["statements"] = ["ALTER TABLE data.notes ADD COLUMN transcript text"]
    operation["records"] = [
        {
            "table": "notes",
            "ref": "note",
            "returning": ["id"],
            "values": {
                "id": typed("integer", 1),
                "transcript": typed("text", "new turn"),
            },
            "on_conflict": {"columns": ["id"], "update": ["transcript"]},
        }
    ]
    result = ingestion.apply(owner_conn, operation)
    assert result["indexing"]["indexes"][0]["ready"] == 1
    assert result["indexing"]["status"] == "queued_for_discovery"
    assert result["indexing"]["automatic_discovery"] == "pending"
    assert ingestion.apply(owner_conn, copy.deepcopy(operation)) == result
