import pytest
from django.core.exceptions import PermissionDenied

from opendb.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_roles_union_and_revocation(personal_db, owner_conn):
    from opendb.databases import sharing
    from opendb.databases.connections import data_connection

    owner_conn.execute(
        "CREATE TABLE data.expenses (id int, secret text, amount numeric)"
    )
    owner_conn.execute("INSERT INTO data.expenses VALUES (1, 'private', 42)")
    owner_conn.execute("CREATE VIEW data.summary AS SELECT amount FROM data.expenses")
    owner_conn.execute("CREATE TABLE data.categories (name text)")
    owner_conn.execute("INSERT INTO data.categories VALUES ('food')")
    actor = personal_db.owner_id
    r1 = sharing.create_role(actor, personal_db.id, "accountant")
    r2 = sharing.create_role(actor, personal_db.id, "categories")
    sharing.grant_object(actor, r1.id, "summary")
    sharing.grant_object(actor, r2.id, "categories")
    guest = UserFactory()
    sharing.assign_role(actor, r1.id, guest.email)
    sharing.assign_role(actor, r2.id, guest.email)
    with data_connection(guest.pk, personal_db.id) as conn:
        assert conn.execute("SELECT amount FROM data.summary").fetchone()[0] == 42
        assert conn.execute("SELECT name FROM data.categories").fetchone()[0] == "food"
        import psycopg

        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SELECT * FROM data.expenses")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("INSERT INTO data.categories VALUES ('evil')")
    sharing.revoke_role(actor, r1.id, guest.email)
    with data_connection(guest.pk, personal_db.id) as conn:
        assert conn.execute("SELECT count(*) FROM data.categories").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SELECT * FROM data.summary")
    sharing.revoke_role(actor, r2.id, guest.email)
    with pytest.raises(PermissionDenied), data_connection(guest.pk, personal_db.id):
        pass


def test_revocation_before_membership_sync_is_not_regranted(
    personal_db, owner_conn, monkeypatch
):
    from contextlib import contextmanager

    from opendb.databases import sharing
    from opendb.databases.connections import data_connection
    from opendb.databases.models import RoleAssignment

    owner_conn.execute("CREATE TABLE data.shared (id int)")
    role = sharing.create_role(personal_db.owner_id, personal_db.id, "temporary")
    sharing.grant_object(personal_db.owner_id, role.id, "shared")
    guest = UserFactory()
    sharing.assign_role(personal_db.owner_id, role.id, guest.email)
    original = sharing.privileged_connection

    @contextmanager
    def revoked_before_connection(database_id):
        RoleAssignment.objects.filter(role=role, email=guest.email).delete()
        with original(database_id) as connection:
            yield connection

    monkeypatch.setattr(sharing, "privileged_connection", revoked_before_connection)
    with pytest.raises(PermissionDenied), data_connection(guest.pk, personal_db.id):
        pass


@pytest.mark.parametrize("name", ["original", "MixedCase"])
def test_object_revoke_survives_rename(personal_db, owner_conn, name):
    import psycopg
    from psycopg import sql

    from opendb.databases import sharing
    from opendb.databases.connections import data_connection

    actor = personal_db.owner_id
    owner_conn.execute(
        sql.SQL("CREATE TABLE {} (id int)").format(sql.Identifier("data", name))
    )
    role = sharing.create_role(actor, personal_db.id, "reader")
    sharing.grant_object(actor, role.id, name)
    guest = UserFactory()
    sharing.assign_role(actor, role.id, guest.email)
    with data_connection(guest.pk, personal_db.id) as conn:
        owner_conn.execute(
            sql.SQL("ALTER TABLE {} RENAME TO renamed").format(
                sql.Identifier("data", name)
            )
        )
        assert conn.execute("SELECT count(*) FROM data.renamed").fetchone()[0] == 0
        sharing.revoke_object(actor, role.id, name)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM data.renamed")


def test_sharing_rejects_uncommitted_control_transaction(personal_db):
    from django.db import transaction

    from opendb.databases import sharing

    with (
        transaction.atomic(),
        pytest.raises(RuntimeError, match="autocommit"),
        sharing.sharing_lock(personal_db.id),
    ):
        pass


def test_grant_tracking_follows_identity_when_name_is_reused(personal_db, owner_conn):
    from opendb.databases import sharing

    actor = personal_db.owner_id
    owner_conn.execute("CREATE TABLE data.old (id int)")
    role = sharing.create_role(actor, personal_db.id, "reader")
    sharing.grant_object(actor, role.id, "old")
    owner_conn.execute("ALTER TABLE data.old RENAME TO renamed")
    assert sharing.list_access(actor, personal_db.id)[0]["objects"] == ["renamed"]
    owner_conn.execute("CREATE TABLE data.old (id int)")
    sharing.grant_object(actor, role.id, "old")
    assert sharing.list_access(actor, personal_db.id)[0]["objects"] == [
        "old",
        "renamed",
    ]
    sharing.revoke_object(actor, role.id, "renamed")
    assert sharing.list_access(actor, personal_db.id)[0]["objects"] == ["old"]
    sharing.revoke_object(actor, role.id, "old")
    assert sharing.list_access(actor, personal_db.id)[0]["objects"] == []
