import pytest
from django.db import IntegrityError
from django.db import transaction

from opendb.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_one_database_per_owner():
    from opendb.databases.models import PersonalDatabase

    owner = UserFactory()
    PersonalDatabase.objects.create(owner=owner)
    with pytest.raises(IntegrityError), transaction.atomic():
        PersonalDatabase.objects.create(owner=owner)


def test_owner_can_crud_but_not_access_catalog(owner_conn):
    import psycopg

    with owner_conn.transaction():
        owner_conn.execute(
            "CREATE TABLE data.costs (id bigint PRIMARY KEY, amount numeric(10,2))"
        )
        owner_conn.execute("INSERT INTO data.costs VALUES (1, 12.50)")
        assert (
            str(owner_conn.execute("SELECT amount FROM data.costs").fetchone()[0])
            == "12.50"
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege), owner_conn.transaction():
        owner_conn.execute("CREATE TABLE opendb_catalog.evil (id int)")


def test_repeat_provisioning(personal_db):
    from opendb.databases.provisioning import provision_personal_database

    assert provision_personal_database(personal_db.owner_id).pk == personal_db.pk


def test_cross_database_credentials(personal_db):
    import psycopg
    from django.conf import settings
    from psycopg.conninfo import conninfo_to_dict

    from opendb.databases.credentials import derive_database_password
    from opendb.databases.provisioning import provision_personal_database

    other = provision_personal_database(UserFactory().pk)
    config = conninfo_to_dict(settings.OPENDB_ADMIN_DSN)
    config.update(
        dbname=other.database_name,
        user=personal_db.role_name,
        password=derive_database_password(personal_db.id),
        connect_timeout=3,
    )
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(**config)
    config["dbname"] = "postgres"
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(**config)


def test_no_admin_capabilities(owner_conn):
    import psycopg

    for statement in [
        "CREATE ROLE intruder",
        "CREATE DATABASE intruder",
        "SELECT pg_read_file('/etc/passwd')",
        "SET ROLE opendb_test",
    ]:
        with pytest.raises(psycopg.Error), owner_conn.transaction():
            owner_conn.execute(statement)


def test_failed_provision_is_retryable(monkeypatch):
    import opendb.vectors
    from opendb.databases.models import PersonalDatabase
    from opendb.databases.provisioning import provision_personal_database

    owner = UserFactory()
    original = opendb.vectors.install

    def unavailable(*args):
        msg = "forced initialization failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(opendb.vectors, "install", unavailable)
    with pytest.raises(RuntimeError):
        provision_personal_database(owner.pk)
    assert PersonalDatabase.objects.get(owner=owner).status == "failed"
    monkeypatch.setattr(opendb.vectors, "install", original)
    assert provision_personal_database(owner.pk).status == "ready"


def test_concurrent_provision_single_database():
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    from opendb.databases.models import PersonalDatabase
    from opendb.databases.provisioning import provision_personal_database

    owner = UserFactory()

    def provision():
        try:
            return provision_personal_database(owner.pk).pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(lambda _: provision(), range(2)))
    assert values[0] == values[1]
    assert PersonalDatabase.objects.filter(owner=owner).count() == 1


def test_sql_errors_are_safe_and_owner_writes_take_catalog_lock(
    personal_db, admin_conn
):
    from opendb.catalog.install import SCHEMA_LOCK_KEY
    from opendb.databases.services import dispatch

    with admin_conn.transaction():
        admin_conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        with pytest.raises(ValueError, match="database_"):
            dispatch(
                personal_db.owner_id,
                "query",
                {
                    "database_id": str(personal_db.id),
                    "sql": "CREATE TABLE data.locked (id int)",
                },
            )
    with pytest.raises(ValueError, match="database_"):
        dispatch(
            personal_db.owner_id,
            "query",
            {
                "database_id": str(personal_db.id),
                "sql": "SELECT * FROM data.does_not_exist",
            },
        )


def test_ingestion_source_visible_only_to_owner(personal_db, owner_conn):
    from django.core.exceptions import PermissionDenied

    from opendb.catalog import describe
    from opendb.databases.services import dispatch
    from opendb.ingestion import apply
    from opendb.ingestion import example_operation

    operation = example_operation("expenses", describe(owner_conn)["fingerprint"])
    apply(owner_conn, operation)
    rows = dispatch(
        personal_db.owner_id, "ingestion_history", {"database_id": str(personal_db.id)}
    )
    assert len(rows) == 1
    record = dispatch(
        personal_db.owner_id,
        "ingestion_history",
        {"database_id": str(personal_db.id), "operation_id": rows[0]["id"]},
    )
    assert record["source"] == operation["source"]
    stranger = UserFactory()
    with pytest.raises(PermissionDenied):
        dispatch(stranger.pk, "ingestion_history", {"database_id": str(personal_db.id)})


def test_sql_binary_values_and_streamed_row_limit(personal_db, monkeypatch):
    import psycopg

    from opendb.databases.services import dispatch

    payload = {"database_id": str(personal_db.id)}
    binary = dispatch(
        personal_db.owner_id, "query", {**payload, "sql": "SELECT 'abc'::bytea"}
    )
    assert binary["rows"] == [[{"type": "bytea", "base64": "YWJj"}]]
    original = psycopg.RawCursor.execute

    def no_buffered_select(self, query, *args, **kwargs):
        assert not (isinstance(query, str) and "generate_series" in query)
        return original(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.RawCursor, "execute", no_buffered_select)
    result = dispatch(
        personal_db.owner_id,
        "query",
        {**payload, "sql": "SELECT generate_series(1,10000)"},
    )
    assert len(result["rows"]) == 500
    assert result["truncated"] is True


def test_oversized_returning_rolls_back_write(personal_db, monkeypatch):
    from opendb.databases import services

    payload = {"database_id": str(personal_db.id)}
    services.dispatch(
        personal_db.owner_id,
        "query",
        {**payload, "sql": "CREATE TABLE data.large (id int, body text)"},
    )
    monkeypatch.setattr(services, "MAX_RESULT_BYTES", 1024)
    with pytest.raises(ValueError, match=r"result.*limit"):
        services.dispatch(
            personal_db.owner_id,
            "query",
            {
                **payload,
                "sql": "INSERT INTO data.large VALUES (1,$1) RETURNING *",
                "parameters": ["x" * 2048],
            },
        )
    result = services.dispatch(
        personal_db.owner_id,
        "query",
        {**payload, "sql": "SELECT count(*) FROM data.large"},
    )
    assert result["rows"] == [[0]]


@pytest.mark.parametrize("stage", ["after_role", "before_ready"])
def test_provisioning_recovers_interrupted_stages(monkeypatch, stage):
    import psycopg
    from psycopg import sql

    from opendb.databases.models import PersonalDatabase
    from opendb.databases.provisioning import provision_personal_database

    owner = UserFactory()
    execute = psycopg.Connection.execute
    save = PersonalDatabase.save

    def interrupted_execute(self, query, *args, **kwargs):
        if (
            stage == "after_role"
            and isinstance(query, sql.Composable)
            and query.as_string(self).startswith("CREATE DATABASE")
        ):
            msg = "simulated interruption after role creation"
            raise RuntimeError(msg)
        return execute(self, query, *args, **kwargs)

    def interrupted_save(self, *args, **kwargs):
        if stage == "before_ready" and self.status == "ready":
            msg = "simulated interruption before ready"
            raise RuntimeError(msg)
        return save(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", interrupted_execute)
        patch.setattr(PersonalDatabase, "save", interrupted_save)
        with pytest.raises(RuntimeError):
            provision_personal_database(owner.pk)
    assert PersonalDatabase.objects.get(owner=owner).status == "failed"
    assert provision_personal_database(owner.pk).status == "ready"


def test_existing_unmarked_database_is_not_adopted():
    import psycopg
    from django.conf import settings
    from psycopg import sql

    from opendb.databases.models import PersonalDatabase
    from opendb.databases.provisioning import provision_personal_database

    db = PersonalDatabase.objects.create(owner=UserFactory())
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(db.database_name)
            )
        )
    with pytest.raises(ValueError, match="ownership conflict"):
        provision_personal_database(db.owner_id)


def test_zero_row_returning_is_a_success(personal_db):
    from opendb.databases.services import dispatch

    payload = {"database_id": str(personal_db.id)}
    dispatch(
        personal_db.owner_id,
        "query",
        {**payload, "sql": "CREATE TABLE data.empty (id int)"},
    )
    for statement in (
        "UPDATE data.empty SET id=1 RETURNING *",
        "DELETE FROM data.empty RETURNING id",
    ):
        result = dispatch(personal_db.owner_id, "query", {**payload, "sql": statement})
        assert result["rows"] == []
        assert result["truncated"] is False


def test_response_limit_includes_column_names(personal_db, monkeypatch):
    from opendb.databases import services

    monkeypatch.setattr(services, "MAX_RESULT_BYTES", 80)
    with pytest.raises(ValueError, match="response size limit"):
        services.dispatch(
            personal_db.owner_id,
            "query",
            {
                "database_id": str(personal_db.id),
                "sql": "SELECT 1 AS column_name_larger_than_the_row_data",
            },
        )
