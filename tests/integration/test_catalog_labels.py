"""Business metadata remains separate from SQL structure and authorization."""

import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from opendb import catalog
from opendb import ingestion
from tests.integration.test_ingestion import guest_conn as guest_conn_fixture

guest_conn = guest_conn_fixture
pytestmark = pytest.mark.django_db(transaction=True)


def annotate(conn, annotations, key="labels"):
    return ingestion.apply(
        conn,
        {
            "version": 1,
            "idempotency_key": key,
            "expected_schema_fingerprint": catalog.describe(conn)["fingerprint"],
            "source": {
                "content": "User-supplied business metadata",
                "media_type": "text/plain",
            },
            "statements": [],
            "records": [],
            "annotations": annotations,
        },
    )


def test_labels_and_column_descriptions_do_not_change_fingerprint(owner_conn):
    owner_conn.execute(
        "CREATE TABLE data.sales_orders (id int PRIMARY KEY, total numeric)"
    )
    before = catalog.describe(owner_conn)["fingerprint"]
    annotate(
        owner_conn,
        [
            {
                "table": "sales_orders",
                "description": "Orders placed by customers.",
                "metadata": {
                    "display_name": "Customer orders",
                    "attributes_summary": "Order identity and total.",
                },
            },
            {
                "table": "sales_orders",
                "column": "total",
                "description": "Total charged",
                "metadata": {},
            },
        ],
    )
    result = catalog.describe(owner_conn)
    item = result["objects"][0]
    assert result["fingerprint"] == before
    assert item["name"] == "sales_orders"
    assert item["display_name"] == "Customer orders"
    assert item["attributes_summary"] == "Order identity and total."
    assert item["description"] == "Orders placed by customers."
    assert item["columns"][1]["description"] == "Total charged"


def test_legacy_summary_uses_visible_column_descriptions(owner_conn):
    owner_conn.execute("CREATE TABLE data.sales_orders (order_id int, total numeric)")
    annotate(
        owner_conn,
        [
            {
                "table": "sales_orders",
                "column": "total",
                "description": "Total charged",
                "metadata": {},
            }
        ],
    )
    item = catalog.describe(owner_conn)["objects"][0]
    assert item["display_name"] == "Sales orders"
    assert item["attributes_summary"] == "Order id; Total: Total charged"


def test_catalog_reinstall_preserves_annotations_and_accepts_new_metadata(
    owner_conn, admin_conn, personal_db
):
    owner_conn.execute("CREATE TABLE data.sales_orders (total numeric)")
    annotate(
        owner_conn,
        [
            {
                "table": "sales_orders",
                "description": "Orders",
                "metadata": {"conventions": "Unknown totals stay NULL"},
            },
            {
                "table": "sales_orders",
                "column": "total",
                "description": "Total charged",
                "metadata": {},
            },
        ],
    )
    before = catalog.describe(owner_conn)
    # Existing catalogs use CREATE OR REPLACE functions; rows must survive upgrade.
    catalog.install(admin_conn, personal_db.role_name)
    catalog.install(admin_conn, personal_db.role_name)
    assert catalog.describe(owner_conn) == before
    annotate(
        owner_conn,
        [
            {
                "table": "sales_orders",
                "description": "Customer orders",
                "metadata": {
                    "display_name": "Orders",
                    "attributes_summary": "Order totals",
                    "conventions": "Unknown totals stay NULL",
                },
            }
        ],
        key="updated-labels",
    )
    after = catalog.describe(owner_conn)
    assert after["fingerprint"] == before["fingerprint"]
    assert after["objects"][0]["metadata"]["conventions"] == "Unknown totals stay NULL"
    assert after["objects"][0]["display_name"] == "Orders"
    assert after["objects"][0]["columns"][0]["description"] == "Total charged"


def test_upgrade_command_updates_existing_legacy_catalog(
    owner_conn, admin_conn, personal_db
):
    from io import StringIO
    from pathlib import Path

    from django.core.management import call_command

    owner_conn.execute("CREATE TABLE data.orders (total numeric)")
    annotate(
        owner_conn,
        [
            {
                "table": "orders",
                "description": "Orders",
                "metadata": {"conventions": "Preserve totals"},
            }
        ],
    )
    before = catalog.describe(owner_conn)
    admin_conn.execute(
        (Path(__file__).parents[1] / "fixtures/catalog_legacy_annotate.sql").read_text()
    )
    labels = [
        {
            "table": "orders",
            "description": "Orders",
            "metadata": {
                "display_name": "Customer orders",
                "attributes_summary": "Totals",
                "conventions": "Preserve totals",
            },
        }
    ]
    with pytest.raises(ingestion.IngestionError):
        annotate(owner_conn, labels, key="new-labels")
    output = StringIO()
    call_command("upgrade_catalog", database_id=personal_db.id, stdout=output)
    call_command("upgrade_catalog", all_ready=True, stdout=output)
    assert "failed: 0" in output.getvalue()
    assert catalog.describe(owner_conn) == before
    annotate(owner_conn, labels, key="new-labels")
    after = catalog.describe(owner_conn)
    assert after["objects"][0]["display_name"] == "Customer orders"
    assert after["objects"][0]["metadata"]["conventions"] == "Preserve totals"
    assert after["fingerprint"] == before["fingerprint"]


def test_upgrade_command_reports_redacted_failure_and_skips_deleted(
    personal_db, monkeypatch
):
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    from opendb.databases.management.commands import upgrade_catalog

    def unavailable(*args):
        msg = "private DSN must not appear in command output"
        raise RuntimeError(msg)

    monkeypatch.setattr(upgrade_catalog, "privileged_connection", unavailable)
    output, errors = StringIO(), StringIO()
    with pytest.raises(CommandError, match="incomplete"):
        call_command("upgrade_catalog", all_ready=True, stdout=output, stderr=errors)
    assert "failed: 1" in output.getvalue()
    assert "private DSN" not in errors.getvalue()
    personal_db.status = "deleted"
    personal_db.save(update_fields=["status"])
    output = StringIO()
    call_command("upgrade_catalog", all_ready=True, stdout=output)
    assert "upgraded: 0; failed: 0" in output.getvalue()
    with pytest.raises(CommandError, match="not ready"):
        call_command("upgrade_catalog", database_id=personal_db.id)


def test_large_legacy_summary_is_bounded(owner_conn):
    owner_conn.execute(
        "CREATE TABLE data.many_fields ("
        + ",".join(f"field_{i} text" for i in range(20))
        + ")"
    )
    annotate(
        owner_conn,
        [
            {
                "table": "many_fields",
                "column": f"field_{i}",
                "description": "x" * 10000,
                "metadata": {},
            }
            for i in range(20)
        ],
    )
    summary = catalog.describe(owner_conn)["objects"][0]["attributes_summary"]
    assert len(summary) <= 2000
    assert summary.startswith("Field 0: ")
    assert summary.endswith("…")


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        None,
        "invalid",
        {"display_name": 9, "attributes_summary": []},
        {"display_name": " " * 5, "attributes_summary": ""},
        {"display_name": "x" * 201, "attributes_summary": "x" * 2001},
    ],
)
def test_malformed_direct_metadata_falls_back_safely(owner_conn, admin_conn, metadata):
    owner_conn.execute("CREATE TABLE data.sales_orders (total numeric)")
    annotate(
        owner_conn, [{"table": "sales_orders", "description": "Orders", "metadata": {}}]
    )
    # Simulate an old or externally modified catalog without the object check.
    admin_conn.execute(
        "ALTER TABLE opendb_catalog.annotations "
        "DROP CONSTRAINT annotations_metadata_check"
    )
    admin_conn.execute(
        "UPDATE opendb_catalog.annotations SET metadata=%s", (Jsonb(metadata),)
    )
    item = catalog.describe(owner_conn)["objects"][0]
    assert item["display_name"] == "Sales orders"
    assert item["attributes_summary"] == "Total"


def test_guest_fallback_never_uses_hidden_base_columns(
    owner_conn, admin_conn, guest_conn
):
    owner_conn.execute("CREATE TABLE data.private_orders (id int, secret text)")
    owner_conn.execute(
        "CREATE VIEW data.shared_orders AS SELECT id FROM data.private_orders"
    )
    annotate(
        owner_conn,
        [
            {
                "table": "private_orders",
                "description": "Confidential",
                "metadata": {
                    "display_name": "Secret business",
                    "attributes_summary": "Hidden secret",
                },
            }
        ],
    )
    guest, role = guest_conn
    admin_conn.execute(
        sql.SQL("GRANT SELECT ON data.shared_orders TO {}").format(sql.Identifier(role))
    )
    items = catalog.describe(guest)["objects"]
    assert len(items) == 1
    assert items[0]["display_name"] == "Shared orders"
    assert items[0]["attributes_summary"] == "Id"
    assert "secret" not in str(items).lower()


@pytest.mark.parametrize(
    "metadata",
    [
        {"display_name": 1},
        {"attributes_summary": []},
        {"display_name": "x" * 201},
        {"attributes_summary": "x" * 2001},
    ],
)
def test_sql_annotation_entrypoint_validates_business_metadata(owner_conn, metadata):
    import psycopg

    owner_conn.execute("CREATE TABLE data.orders (id int)")
    with owner_conn.transaction():
        operation = owner_conn.execute(
            "SELECT opendb_catalog.begin_ingestion('direct',%s,%s)",
            ("0" * 64, Jsonb({"content": "metadata", "media_type": "text/plain"})),
        ).fetchone()[0]
        with (
            pytest.raises(psycopg.Error, match="Invalid annotation"),
            owner_conn.transaction(),
        ):
            owner_conn.execute(
                "SELECT opendb_catalog.annotate(%s,'orders',NULL,'Orders',%s)",
                (operation["operation_id"], Jsonb(metadata)),
            )
