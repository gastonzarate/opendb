import pytest
from django.core.exceptions import PermissionDenied
from django.db import transaction

from opendb.databases.models import PersonalDatabase
from opendb.databases.services import dispatch
from opendb.gateway.identity import resolve_google_user
from opendb.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)

CLAIMS = {
    "sub": "automatic-owner",
    "email": "automatic@example.com",
    "email_verified": True,
}


def test_login_provisions_after_commit_and_reuses_database():
    with transaction.atomic():
        user = resolve_google_user(CLAIMS)
        assert not PersonalDatabase.objects.filter(owner=user).exists()
    db = PersonalDatabase.objects.get(owner=user)
    assert db.status == "ready"
    assert resolve_google_user(CLAIMS).pk == user.pk
    assert PersonalDatabase.objects.get(owner=user).pk == db.pk


def test_rolled_back_login_does_not_provision():
    with transaction.atomic():
        resolve_google_user(CLAIMS)
        transaction.set_rollback(True)
    assert not PersonalDatabase.objects.exists()


def test_login_failure_is_persistent_and_retryable(monkeypatch):
    import psycopg

    connect = psycopg.connect
    with monkeypatch.context() as patch:

        def unavailable(*args, **kwargs):
            if args:
                msg = "unavailable"
                raise psycopg.OperationalError(msg)
            return connect(*args, **kwargs)

        patch.setattr(psycopg, "connect", unavailable)
        user = resolve_google_user(CLAIMS)
    db = PersonalDatabase.objects.get(owner=user)
    assert db.status == "failed"
    assert db.error_code == "provision_failed"
    assert resolve_google_user(CLAIMS).pk == user.pk
    db.refresh_from_db()
    assert db.status == "ready"
    assert PersonalDatabase.objects.filter(owner=user).count() == 1


def test_ready_login_does_not_require_admin_connection(monkeypatch):
    import psycopg

    user = resolve_google_user(CLAIMS)
    db = PersonalDatabase.objects.get(owner=user)

    def unavailable(*args, **kwargs):
        msg = "ready login must not connect to customer cluster"
        raise AssertionError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg, "connect", unavailable)
        assert resolve_google_user(CLAIMS).pk == user.pk
        assert PersonalDatabase.objects.get(owner=user).pk == db.pk


def test_onboarding_is_persistent_idempotent_and_owner_only(personal_db):
    actor = personal_db.owner_id
    payload = {"database_id": str(personal_db.pk)}
    assert dispatch(actor, "list_databases", {})[0]["onboarding_completed"] is False
    stranger = UserFactory()
    with pytest.raises(PermissionDenied):
        dispatch(stranger.pk, "complete_onboarding", payload)
    for _ in range(2):
        result = dispatch(actor, "complete_onboarding", payload)
        assert result["onboarding_completed"] is True
        assert result["id"] == str(personal_db.pk)
    assert dispatch(actor, "list_databases", {})[0]["onboarding_completed"] is True
    assert dispatch(actor, "create_database", {})["onboarding_completed"] is True


def test_saving_instructions_are_owner_only_persistent_and_bounded(personal_db):
    actor = personal_db.owner_id
    payload = {"database_id": str(personal_db.pk)}
    assert dispatch(actor, "saving_instructions", payload)["instructions"] == ""
    assert dispatch(actor, "list_databases", {})[0]["saving_instructions"] == ""
    stranger = UserFactory()
    for action, body in [
        ("saving_instructions", payload),
        ("update_saving_instructions", {**payload, "instructions": "leaked"}),
    ]:
        with pytest.raises(PermissionDenied):
            dispatch(stranger.pk, action, body)
    stored = dispatch(
        actor,
        "update_saving_instructions",
        {**payload, "instructions": "  Guardá cada reunión.\r\nNo guardes tarjetas. "},
    )
    assert stored["instructions"] == "Guardá cada reunión.\nNo guardes tarjetas."
    assert stored["updated_at"] is not None
    assert stored["max_length"] == 4000
    reread = dispatch(actor, "saving_instructions", payload)
    assert reread["instructions"] == stored["instructions"]
    assert (
        dispatch(actor, "list_databases", {})[0]["saving_instructions"]
        == (stored["instructions"])
    )
    with pytest.raises(ValueError, match="at most 4000 characters"):
        dispatch(
            actor,
            "update_saving_instructions",
            {**payload, "instructions": "x" * 4001},
        )
    with pytest.raises(ValueError, match="must be text"):
        dispatch(
            actor, "update_saving_instructions", {**payload, "instructions": {"a": 1}}
        )
    assert (
        dispatch(actor, "saving_instructions", payload)["instructions"]
        == (stored["instructions"])
    )
    cleared = dispatch(
        actor, "update_saving_instructions", {**payload, "instructions": ""}
    )
    assert cleared["instructions"] == ""


def test_role_description_create_update_and_authorization(personal_db):
    actor = personal_db.owner_id
    payload = {"database_id": str(personal_db.pk)}
    role = dispatch(
        actor,
        "create_role",
        {**payload, "name": "reader", "description": "Read financial summaries"},
    )
    assert role["description"] == "Read financial summaries"
    update = {**payload, "role_id": role["id"], "description": "Read public summaries"}
    stranger = UserFactory()
    for action, body in [
        ("create_role", {**payload, "name": "bad", "description": "bad"}),
        ("update_role", update),
        ("list_access", payload),
    ]:
        with pytest.raises(PermissionDenied):
            dispatch(stranger.pk, action, body)
    updated = dispatch(actor, "update_role", update)
    assert updated["name"] == "reader"
    assert updated["description"] == "Read public summaries"
    updated = dispatch(
        actor, "update_role", {**payload, "role_id": role["id"], "name": "summaries"}
    )
    assert updated["description"] == "Read public summaries"
    assert (
        dispatch(actor, "list_access", payload)[0]["description"]
        == updated["description"]
    )
    other = PersonalDatabase.objects.create(owner=stranger)
    with pytest.raises(PermissionDenied):
        dispatch(actor, "update_role", {**update, "database_id": str(other.pk)})
    assert (
        dispatch(actor, "update_role", {**update, "description": ""})["description"]
        == ""
    )


@pytest.mark.parametrize("description", [None, 42, [], "x" * 2001])
def test_invalid_descriptions_rejected_without_mutation(personal_db, description):
    actor = personal_db.owner_id
    payload = {"database_id": str(personal_db.pk), "name": "reader"}
    with pytest.raises(ValueError, match="description"):
        dispatch(actor, "create_role", {**payload, "description": description})
    assert not personal_db.accessrole_set.exists()
    role = dispatch(actor, "create_role", {**payload, "description": "x" * 2000})
    with pytest.raises(ValueError, match="description"):
        dispatch(
            actor,
            "update_role",
            {**payload, "role_id": role["id"], "description": description},
        )
    assert (
        dispatch(actor, "list_access", {"database_id": str(personal_db.pk)})[0][
            "description"
        ]
        == "x" * 2000
    )


def test_ready_identity_does_not_schedule_provisioning():
    from django.db import connection

    user = resolve_google_user(CLAIMS)
    with transaction.atomic():
        assert resolve_google_user(CLAIMS).pk == user.pk
        assert connection.run_on_commit == []


def test_explicit_provision_rejects_uncommitted_transaction():
    from opendb.databases.provisioning import provision_personal_database

    owner = UserFactory()
    with transaction.atomic(), pytest.raises(RuntimeError, match="autocommit"):
        provision_personal_database(owner.pk)
    assert not PersonalDatabase.objects.filter(owner=owner).exists()


def test_real_web_login_provisions_without_listing(settings, monkeypatch):
    from tests.test_gateway_api import (
        test_real_allauth_google_callback_creates_authenticated_api_session,
    )

    test_real_allauth_google_callback_creates_authenticated_api_session(
        settings, monkeypatch
    )
    db = PersonalDatabase.objects.get(owner__email="callback@example.com")
    assert db.status == "ready"
    assert db.onboarding_completed is False


def test_mcp_identity_provisions_without_listing():
    from opendb.gateway.mcp import _django_call
    from opendb.gateway.mcp import _resolve_actor

    actor = _django_call(_resolve_actor, CLAIMS)
    db = PersonalDatabase.objects.get(owner_id=actor)
    assert db.status == "ready"
    assert _django_call(_resolve_actor, CLAIMS) == actor
    assert PersonalDatabase.objects.get(owner_id=actor).pk == db.pk
