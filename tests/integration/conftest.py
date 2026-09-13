import psycopg
import pytest
from django.conf import settings
from psycopg import sql


@pytest.fixture(autouse=True)
def isolated_resources(transactional_db):
    """Run only on explicitly disposable cluster; cleanup this test process's IDs."""
    assert getattr(settings, "OPENDB_TEST_CLUSTER", False), (
        "Use --ds=config.settings.integration"
    )
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as conn:
        for (name,) in conn.execute("SELECT datname FROM pg_database"):
            conn.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(name)
                )
            )
    yield
    from opendb.databases.models import PersonalDatabase

    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as conn:
        for db in PersonalDatabase.objects.all():
            role_names = [
                row[0]
                for row in conn.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname=%s OR rolname LIKE %s",
                    (db.role_name, f"odb_guest_{db.id.hex}_%"),
                )
            ]
            from opendb.databases.models import AccessRole

            role_names += [r.pg_name for r in AccessRole.objects.filter(database=db)]
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(db.database_name)
                )
            )
            for role in role_names:
                conn.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
                )


@pytest.fixture
def personal_db():
    from opendb.databases.provisioning import provision_personal_database
    from opendb.users.tests.factories import UserFactory

    return provision_personal_database(UserFactory().pk)


@pytest.fixture
def owner_conn(personal_db):
    from opendb.databases.connections import owner_connection

    with owner_connection(personal_db.owner_id, personal_db.id) as conn:
        yield conn


@pytest.fixture
def admin_conn(personal_db):
    from opendb.databases.connections import privileged_connection

    with privileged_connection(personal_db.id) as conn:
        yield conn
