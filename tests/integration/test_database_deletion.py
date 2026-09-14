import psycopg
import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied
from psycopg import sql

from opendb.databases.models import PersonalDatabase
from opendb.databases.services import dispatch
from opendb.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_owner_delete_drops_database_and_retains_tombstone(personal_db):
    payload = {"database_id": str(personal_db.pk)}
    dispatch(
        personal_db.owner_id,
        "query",
        {**payload, "sql": "CREATE TABLE data.secret (id int)"},
    )
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        result = dispatch(personal_db.owner_id, "delete_database", payload)
        assert result["status"] == "deleted"
        assert result["is_owner"] is True
        assert result["id"] == str(personal_db.pk)
        assert (
            PersonalDatabase.objects.get(owner_id=personal_db.owner_id).status
            == "deleted"
        )
        assert (
            admin.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s",
                (personal_db.database_name,),
            ).fetchone()
            is None
        )
        assert (
            admin.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (personal_db.role_name,)
            ).fetchone()
            is None
        )
        assert dispatch(personal_db.owner_id, "delete_database", payload) == result


def test_guest_cannot_delete(personal_db):
    guest = UserFactory()
    with pytest.raises(PermissionDenied):
        dispatch(guest.pk, "delete_database", {"database_id": str(personal_db.pk)})
    personal_db.refresh_from_db()
    assert personal_db.status == "ready"


def test_login_preserves_tombstone_and_explicit_create_is_empty():
    from opendb.gateway.identity import resolve_google_user

    claims = {
        "sub": "deleted-owner",
        "email": "deleted@example.com",
        "email_verified": True,
    }
    user = resolve_google_user(claims)
    db = PersonalDatabase.objects.get(owner=user)
    payload = {"database_id": str(db.pk)}
    dispatch(user.pk, "query", {**payload, "sql": "CREATE TABLE data.secret (id int)"})
    dispatch(user.pk, "delete_database", payload)
    resolve_google_user(claims)
    db.refresh_from_db()
    assert db.status == "deleted"
    PersonalDatabase.objects.filter(pk=db.pk).update(onboarding_completed=True)
    recreated = dispatch(user.pk, "create_database", {})
    assert recreated["status"] == "ready"
    assert recreated["onboarding_completed"] is False
    from opendb.databases.connections import privileged_connection

    with privileged_connection(db.pk) as conn:
        assert conn.execute("SELECT to_regclass('data.secret')").fetchone() == (None,)


def test_refuses_database_marker_conflict(personal_db):
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(
            sql.SQL("COMMENT ON DATABASE {} IS 'foreign'").format(
                sql.Identifier(personal_db.database_name)
            )
        )
        with pytest.raises(ValueError, match="ownership"):
            dispatch(
                personal_db.owner_id,
                "delete_database",
                {"database_id": str(personal_db.pk)},
            )
        assert admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (personal_db.database_name,)
        ).fetchone()
        personal_db.refresh_from_db()
        assert personal_db.status == "delete_failed"


def test_sharing_roles_and_metadata_removed_with_active_connections(personal_db):
    from opendb.databases import sharing
    from opendb.databases.connections import connection_parameters
    from opendb.databases.models import AccessRole
    from opendb.databases.models import ObjectGrant
    from opendb.databases.models import RoleAssignment
    from opendb.databases.provisioning import provision_personal_database

    actor = personal_db.owner_id
    payload = {"database_id": str(personal_db.pk)}
    guest = UserFactory()
    other = provision_personal_database(UserFactory().pk)
    dispatch(actor, "query", {**payload, "sql": "CREATE TABLE data.secret (id int)"})
    role = sharing.create_role(actor, personal_db.pk, "readers")
    sharing.grant_object(actor, role.pk, "secret")
    sharing.assign_role(actor, role.pk, guest.email)
    guest_name = f"odb_guest_{personal_db.id.hex}_{guest.pk}"
    with (
        sharing.guest_connection(guest.pk, personal_db) as active_guest,
        psycopg.connect(
            **connection_parameters(personal_db, personal_db.role_name), autocommit=True
        ) as active_owner,
        psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin,
    ):
        with pytest.raises(PermissionDenied):
            dispatch(guest.pk, "delete_database", payload)
        assert dispatch(actor, "delete_database", payload)["status"] == "deleted"
        for conn in [active_guest, active_owner]:
            with pytest.raises(psycopg.OperationalError):
                conn.execute("SELECT 1")
        assert not admin.execute(
            "SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)",
            ([role.pg_name, guest_name, personal_db.role_name],),
        ).fetchall()
        assert admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (other.database_name,)
        ).fetchone()
        assert admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (other.role_name,)
        ).fetchone()
    assert not AccessRole.objects.filter(database=personal_db).exists()
    assert not ObjectGrant.objects.filter(role_id=role.pk).exists()
    assert not RoleAssignment.objects.filter(role_id=role.pk).exists()
    assert all(
        db["id"] != str(personal_db.pk)
        for db in dispatch(guest.pk, "list_databases", {})
    )


@pytest.mark.parametrize("stage", ["before_drop", "after_drop", "before_tombstone"])
def test_partial_deletion_retries_without_login_recreation(
    personal_db, monkeypatch, stage
):
    from opendb.databases.provisioning import provision_personal_database

    execute = psycopg.Connection.execute
    save = PersonalDatabase.save

    def interrupted_execute(conn, query, *args, **kwargs):
        statement = (
            query.as_string(conn) if isinstance(query, sql.Composable) else query
        )
        if (stage == "before_drop" and statement.startswith("DROP DATABASE")) or (
            stage == "after_drop" and statement.startswith("DROP ROLE")
        ):
            msg = "simulated interruption"
            raise RuntimeError(msg)
        return execute(conn, query, *args, **kwargs)

    def interrupted_save(db, *args, **kwargs):
        if stage == "before_tombstone" and db.status == "deleted":
            msg = "simulated interruption"
            raise RuntimeError(msg)
        return save(db, *args, **kwargs)

    payload = {"database_id": str(personal_db.pk)}
    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", interrupted_execute)
        patch.setattr(PersonalDatabase, "save", interrupted_save)
        with pytest.raises(RuntimeError, match="simulated"):
            dispatch(personal_db.owner_id, "delete_database", payload)
    personal_db.refresh_from_db()
    assert personal_db.status == "delete_failed"
    assert provision_personal_database(personal_db.owner_id).status == "delete_failed"
    with pytest.raises(ValueError, match="deletion must finish"):
        dispatch(personal_db.owner_id, "create_database", {})
    assert (
        dispatch(personal_db.owner_id, "delete_database", payload)["status"]
        == "deleted"
    )


@pytest.mark.parametrize("target", ["owner", "group", "guest"])
def test_refuses_conflicting_role_markers_before_dropping_database(personal_db, target):
    from opendb.databases import sharing

    if target == "owner":
        name = personal_db.role_name
    else:
        role = sharing.create_role(personal_db.owner_id, personal_db.pk, "readers")
        name = role.pg_name
        if target == "guest":
            guest = UserFactory()
            sharing.assign_role(personal_db.owner_id, role.pk, guest.email)
            with sharing.guest_connection(guest.pk, personal_db):
                pass
            name = f"odb_guest_{personal_db.id.hex}_{guest.pk}"
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(
            sql.SQL("COMMENT ON ROLE {} IS 'foreign'").format(sql.Identifier(name))
        )
        with pytest.raises(ValueError, match="ownership"):
            dispatch(
                personal_db.owner_id,
                "delete_database",
                {"database_id": str(personal_db.pk)},
            )
        assert admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (personal_db.database_name,)
        ).fetchone()
        assert admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)
        ).fetchone()


@pytest.mark.parametrize("explicit", [False, True])
def test_provision_waits_for_delete_and_rechecks_state(
    personal_db, monkeypatch, explicit
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import connections

    from opendb.databases import deletion
    from opendb.databases.provisioning import provision_personal_database

    deleting = Event()
    release = Event()
    provisioning = Event()
    original = deletion._owned_resources  # noqa: SLF001 -- Pause at lock-held preflight.
    execute = psycopg.Connection.execute

    def paused_validation(admin, db):
        deleting.set()
        assert release.wait(10)
        return original(admin, db)

    def observe_lock(conn, query, *args, **kwargs):
        if query == "SELECT pg_advisory_lock(%s)" and deleting.is_set():
            provisioning.set()
        return execute(conn, query, *args, **kwargs)

    def delete():
        try:
            return dispatch(
                personal_db.owner_id,
                "delete_database",
                {"database_id": str(personal_db.pk)},
            )
        finally:
            connections.close_all()

    def provision():
        try:
            return provision_personal_database(
                personal_db.owner_id, recreate_deleted=explicit
            ).status
        finally:
            connections.close_all()

    monkeypatch.setattr(deletion, "_owned_resources", paused_validation)
    monkeypatch.setattr(psycopg.Connection, "execute", observe_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(delete)
        try:
            assert deleting.wait(10)
            second = pool.submit(provision)
            assert provisioning.wait(10)
            assert not second.done()
        finally:
            release.set()
        assert first.result(timeout=10)["status"] == "deleted"
        expected = "ready" if explicit else "deleted"
        assert second.result(timeout=10) == expected
    personal_db.refresh_from_db()
    assert personal_db.status == expected


def test_web_delete_requires_owner_google_session_and_csrf(personal_db):
    from allauth.socialaccount.models import SocialAccount
    from django.test import Client

    def client_for(user):
        subject = f"delete-{user.pk}"
        SocialAccount.objects.create(user=user, provider="google", uid=subject)
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        session = client.session
        session["account_authentication_methods"] = [
            {"method": "socialaccount", "provider": "google", "uid": subject}
        ]
        session.save()
        return client

    path = "/api/actions/delete_database/"
    payload = {"database_id": str(personal_db.pk)}
    owner = client_for(personal_db.owner)
    guest = client_for(UserFactory())
    response = owner.post(path, payload, content_type="application/json")
    assert response.status_code == 403
    for client, expected in [(guest, 403), (owner, 200)]:
        token = client.get("/api/session/").json()["csrf_token"]
        response = client.post(
            path, payload, content_type="application/json", HTTP_X_CSRFTOKEN=token
        )
        assert response.status_code == expected
    assert response.json()["result"]["status"] == "deleted"
    assert response.json()["result"]["onboarding_completed"] is False


@pytest.mark.parametrize("status", ["deleted", "deleting", "delete_failed"])
def test_identity_does_not_schedule_provision_for_deletion_states(status, monkeypatch):
    from django.db import connection
    from django.db import transaction

    from opendb.gateway.identity import resolve_google_user

    claims = {
        "sub": "delete-state",
        "email": "delete-state@example.com",
        "email_verified": True,
    }
    user = resolve_google_user(claims)
    PersonalDatabase.objects.filter(owner=user).update(status=status)

    def unavailable(*args, **kwargs):
        msg = "Login must not contact the customer cluster"
        raise AssertionError(msg)

    with monkeypatch.context() as patch, transaction.atomic():
        patch.setattr(psycopg, "connect", unavailable)
        assert resolve_google_user(claims).pk == user.pk
        assert connection.run_on_commit == []
    assert PersonalDatabase.objects.get(owner=user).status == status


def test_delete_waits_for_provision_and_rechecks_state(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import connections

    from opendb.databases.provisioning import provision_personal_database

    db = PersonalDatabase.objects.create(owner=UserFactory())
    provisioning = Event()
    deleting = Event()
    release = Event()
    execute = psycopg.Connection.execute

    def paused_execute(conn, query, *args, **kwargs):
        statement = (
            query.as_string(conn) if isinstance(query, sql.Composable) else query
        )
        if statement.startswith("CREATE DATABASE"):
            provisioning.set()
            assert release.wait(10)
        if statement == "SELECT pg_advisory_lock(%s)" and provisioning.is_set():
            deleting.set()
        return execute(conn, query, *args, **kwargs)

    def provision():
        try:
            return provision_personal_database(db.owner_id).status
        finally:
            connections.close_all()

    def delete():
        try:
            return dispatch(db.owner_id, "delete_database", {"database_id": str(db.pk)})
        finally:
            connections.close_all()

    monkeypatch.setattr(psycopg.Connection, "execute", paused_execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(provision)
        try:
            assert provisioning.wait(10)
            second = pool.submit(delete)
            assert deleting.wait(10)
            assert not second.done()
        finally:
            release.set()
        assert first.result(timeout=10) == "ready"
        assert second.result(timeout=10)["status"] == "deleted"
    db.refresh_from_db()
    assert db.status == "deleted"
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        assert not admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (db.database_name,)
        ).fetchone()


@pytest.mark.parametrize("revoked", [False, True])
def test_legacy_sharing_roles_are_reconciled_then_deleted(personal_db, revoked):
    from opendb.databases import sharing

    role = sharing.create_role(personal_db.owner_id, personal_db.pk, "legacy")
    guest = UserFactory()
    sharing.assign_role(personal_db.owner_id, role.pk, guest.email)
    with sharing.guest_connection(guest.pk, personal_db):
        pass
    if revoked:
        sharing.revoke_role(personal_db.owner_id, role.pk, guest.email)
    names = [role.pg_name, f"odb_guest_{personal_db.id.hex}_{guest.pk}"]
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        for name in names:
            admin.execute(
                sql.SQL("COMMENT ON ROLE {} IS NULL").format(sql.Identifier(name))
            )
        result = dispatch(
            personal_db.owner_id,
            "delete_database",
            {"database_id": str(personal_db.pk)},
        )
        assert result["status"] == "deleted"
        assert not admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (names,)
        ).fetchall()


def test_legacy_role_with_foreign_dependency_is_not_adopted(personal_db):
    from opendb.databases import sharing
    from opendb.databases.provisioning import provision_personal_database

    role = sharing.create_role(personal_db.owner_id, personal_db.pk, "unsafe")
    other = provision_personal_database(UserFactory().pk)
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(
            sql.SQL("COMMENT ON ROLE {} IS NULL").format(sql.Identifier(role.pg_name))
        )
        admin.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(other.database_name), sql.Identifier(role.pg_name)
            )
        )
        try:
            with pytest.raises(ValueError, match="ownership"):
                dispatch(
                    personal_db.owner_id,
                    "delete_database",
                    {"database_id": str(personal_db.pk)},
                )
            assert admin.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s",
                (personal_db.database_name,),
            ).fetchone()
            assert admin.execute(
                "SELECT shobj_description(oid, 'pg_authid') "
                "FROM pg_roles WHERE rolname=%s",
                (role.pg_name,),
            ).fetchone() == (None,)
        finally:
            admin.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(
                    sql.Identifier(other.database_name), sql.Identifier(role.pg_name)
                )
            )


def test_legacy_marker_survives_interruption_after_database_drop(
    personal_db, monkeypatch
):
    from opendb.databases import sharing

    role = sharing.create_role(personal_db.owner_id, personal_db.pk, "legacy")
    execute = psycopg.Connection.execute

    def interrupted(conn, query, *args, **kwargs):
        if isinstance(query, sql.Composable) and query.as_string(conn).startswith(
            "DROP ROLE"
        ):
            msg = "simulated interruption"
            raise RuntimeError(msg)
        return execute(conn, query, *args, **kwargs)

    payload = {"database_id": str(personal_db.pk)}
    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(
            sql.SQL("COMMENT ON ROLE {} IS NULL").format(sql.Identifier(role.pg_name))
        )
        with monkeypatch.context() as patch:
            patch.setattr(psycopg.Connection, "execute", interrupted)
            with pytest.raises(RuntimeError, match="simulated"):
                dispatch(personal_db.owner_id, "delete_database", payload)
        assert not admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (personal_db.database_name,)
        ).fetchone()
        assert admin.execute(
            "SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname=%s",
            (role.pg_name,),
        ).fetchone() == (f"opendb:{personal_db.id}",)
        assert (
            dispatch(personal_db.owner_id, "delete_database", payload)["status"]
            == "deleted"
        )


@pytest.mark.parametrize("kind", ["owner", "guest_lookalike", "foreign_membership"])
def test_unmarked_roles_require_scope_evidence(personal_db, kind):
    from opendb.databases import sharing

    with psycopg.connect(settings.OPENDB_ADMIN_DSN, autocommit=True) as admin:
        if kind == "owner":
            name = personal_db.role_name
        elif kind == "guest_lookalike":
            name = f"odb_guest_{personal_db.id.hex}_{UserFactory().pk}"
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(name)))
        else:
            role = sharing.create_role(personal_db.owner_id, personal_db.pk, "legacy")
            name = role.pg_name
            admin.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(name), sql.Identifier(personal_db.role_name)
                )
            )
        admin.execute(
            sql.SQL("COMMENT ON ROLE {} IS NULL").format(sql.Identifier(name))
        )
        with pytest.raises(ValueError, match="ownership"):
            dispatch(
                personal_db.owner_id,
                "delete_database",
                {"database_id": str(personal_db.pk)},
            )
        assert admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (personal_db.database_name,)
        ).fetchone()
