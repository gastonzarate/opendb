import json

import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.models import SocialLogin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import include
from django.urls import path

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def gateway_urls(settings):
    from config.urls import urlpatterns as base_patterns
    from opendb.gateway.urls import urlpatterns as gateway_patterns

    settings.ROOT_URLCONF = __name__
    globals()["urlpatterns"] = [path("api/", include(gateway_patterns)), *base_patterns]


@pytest.fixture
def google_client():
    user = get_user_model().objects.create_user(email="web@example.com")
    SocialAccount.objects.create(user=user, provider="google", uid="web-subject")
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    session = client.session
    session["account_authentication_methods"] = [
        {"method": "socialaccount", "provider": "google", "uid": "web-subject"},
    ]
    session.save()
    return client, user


@pytest.fixture
def local_login_client(settings):
    settings.OPENDB_LOCAL_LOGIN_ENABLED = True
    user = get_user_model().objects.create_user(email="local@example.com")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    session = client.session
    session["account_authentication_methods"] = [{"method": "password"}]
    session.save()
    return client, user


def post(client, action, payload):
    csrf = client.get("/api/session/").json()["csrf_token"]
    return client.post(
        f"/api/actions/{action}/",
        json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


def test_anonymous_session_is_json_401():
    response = Client().get("/api/session/")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_password_session_cannot_use_google_api(google_client):
    client, _ = google_client
    session = client.session
    session.pop("account_authentication_methods")
    session.save()
    assert client.get("/api/session/").status_code == 401


def test_session_record_must_match_the_authenticated_users_subject(google_client):
    client, _ = google_client
    session = client.session
    session["account_authentication_methods"][0]["uid"] = "someone-else"
    session.save()
    assert client.get("/api/session/").status_code == 401


def test_session_returns_identity_and_csrf_token(google_client):
    client, user = google_client
    response = client.get("/api/session/")
    assert response.json()["user"] == {"id": user.pk, "email": user.email}
    assert response.json()["csrf_token"]
    assert response["Cache-Control"] == "no-store"


def test_local_password_session_can_use_api_when_local_login_enabled(
    local_login_client,
):
    client, user = local_login_client
    response = client.get("/api/session/")
    assert response.status_code == 200
    assert response.json()["user"] == {"id": user.pk, "email": user.email}


def test_local_password_session_cannot_use_api_when_local_login_disabled(settings):
    settings.OPENDB_LOCAL_LOGIN_ENABLED = True
    user = get_user_model().objects.create_user(email="local@example.com")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    session = client.session
    session["account_authentication_methods"] = [{"method": "password"}]
    session.save()
    settings.OPENDB_LOCAL_LOGIN_ENABLED = False
    assert client.get("/api/session/").status_code == 401


def test_personal_access_tokens_endpoint_hidden_when_local_login_disabled(
    google_client,
):
    client, _ = google_client
    assert client.get("/api/personal-access-tokens/").status_code == 404


def test_create_list_and_revoke_personal_access_token(local_login_client):
    client, user = local_login_client
    csrf = client.get("/api/session/").json()["csrf_token"]

    created = client.post(
        "/api/personal-access-tokens/create/",
        json.dumps({"name": "laptop"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["raw_token"].startswith("odbpat_")
    assert body["token"]["name"] == "laptop"
    token_id = body["token"]["id"]

    listed = client.get("/api/personal-access-tokens/")
    assert listed.status_code == 200
    [row] = listed.json()["tokens"]
    assert row["id"] == token_id
    assert row["revoked_at"] is None
    assert body["raw_token"] not in json.dumps(row)

    from opendb.gateway.tokens import resolve_personal_access_token

    assert resolve_personal_access_token(body["raw_token"]) == user.pk

    revoked = client.post(
        f"/api/personal-access-tokens/{token_id}/revoke/",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert revoked.status_code == 200
    assert (
        client.get("/api/personal-access-tokens/").json()["tokens"][0]["revoked_at"]
        is not None
    )
    assert resolve_personal_access_token(body["raw_token"]) is None

    again = client.post(
        f"/api/personal-access-tokens/{token_id}/revoke/",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert again.status_code == 404


def test_personal_access_tokens_are_scoped_to_their_owner(settings):
    from opendb.gateway.tokens import generate_personal_access_token

    settings.OPENDB_LOCAL_LOGIN_ENABLED = True
    owner = get_user_model().objects.create_user(email="owner2@example.com")
    other = get_user_model().objects.create_user(email="other2@example.com")
    owner_token, _ = generate_personal_access_token(owner)

    client = Client(enforce_csrf_checks=True)
    client.force_login(other)
    session = client.session
    session["account_authentication_methods"] = [{"method": "password"}]
    session.save()

    assert client.get("/api/personal-access-tokens/").json()["tokens"] == []
    csrf = client.get("/api/session/").json()["csrf_token"]
    response = client.post(
        f"/api/personal-access-tokens/{owner_token.pk}/revoke/",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 404
    owner_token.refresh_from_db()
    assert owner_token.revoked_at is None


def test_api_dispatches_with_session_actor(google_client, monkeypatch):
    from opendb.gateway import api

    calls = []
    monkeypatch.setattr(
        api, "dispatch", lambda *args: calls.append(args) or {"ok": True}
    )
    client, user = google_client
    payload = {"database_id": "abc", "sql": "SELECT 1"}
    response = post(client, "query", payload)
    assert response.status_code == 200
    assert response.json() == {"result": {"ok": True}}
    assert calls == [(user.pk, "query", payload)]


@pytest.mark.django_db(transaction=True)
def test_api_dispatch_runs_in_autocommit_despite_atomic_requests(
    google_client, monkeypatch
):
    from django.db import connection

    from opendb.gateway import api

    assert connection.settings_dict["ATOMIC_REQUESTS"] is True
    states = []

    def dispatch(*args):
        states.append((connection.get_autocommit(), connection.in_atomic_block))
        return {"ok": True}

    monkeypatch.setattr(api, "dispatch", dispatch)
    response = post(google_client[0], "revoke_role", {})
    assert response.status_code == 200
    assert states == [(True, False)]


@pytest.mark.parametrize("payload", [{"actor_id": 99}, {"user_id": 99}, [], None])
def test_api_rejects_identity_overrides_and_nonobject_payloads(
    google_client, monkeypatch, payload
):
    from opendb.gateway import api

    def forbidden(*args):
        pytest.fail("Invalid payload reached dispatch")

    monkeypatch.setattr(api, "dispatch", forbidden)
    assert post(google_client[0], "query", payload).status_code == 400


def test_api_unknown_action_is_404_and_never_dispatched(google_client, monkeypatch):
    from opendb.gateway import api

    monkeypatch.setattr(
        api, "dispatch", lambda *args: pytest.fail("Unknown action dispatched")
    )
    assert post(google_client[0], "privileged_connection", {}).status_code == 404


def test_api_csrf_is_mandatory(google_client):
    client, _ = google_client
    response = client.post(
        "/api/actions/create_database/", "{}", content_type="application/json"
    )
    assert response.status_code == 403


def test_api_does_not_expose_permission_error_details(google_client, monkeypatch):
    from opendb.gateway import api

    def denied(*args):
        msg = "private table customer_secrets"
        raise PermissionDenied(msg)

    monkeypatch.setattr(api, "dispatch", denied)
    response = post(google_client[0], "catalog", {})
    assert response.status_code == 403
    assert "customer_secrets" not in response.content.decode()


def test_api_missing_required_operation_field_returns_bad_request(
    google_client, monkeypatch
):
    from opendb.gateway import api

    def missing(*args):
        msg = "database_id"
        raise KeyError(msg)

    monkeypatch.setattr(api, "dispatch", missing)
    response = post(google_client[0], "query", {})
    assert response.status_code == 400


def test_api_requires_post_and_json(google_client):
    client, _ = google_client
    assert client.get("/api/actions/list_databases/").status_code == 405
    csrf = client.get("/api/session/").json()["csrf_token"]
    response = client.post(
        "/api/actions/query/",
        "garbage",
        content_type="text/plain",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 415
    response = client.post(
        "/api/actions/query/",
        "{bad",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 400


def test_allauth_adapter_shares_subject_identity_with_mcp(rf):
    from opendb.gateway.adapters import GoogleSocialAccountAdapter
    from opendb.gateway.identity import resolve_google_user

    request = rf.get("/")
    sociallogin = SocialLogin(
        account=SocialAccount(
            provider="google",
            uid="shared-subject",
            extra_data={
                "sub": "shared-subject",
                "email": "shared@example.com",
                "email_verified": True,
            },
        )
    )
    GoogleSocialAccountAdapter().pre_social_login(request, sociallogin)
    assert sociallogin.is_existing
    assert resolve_google_user(sociallogin.account.extra_data).pk == sociallogin.user.pk


def test_allauth_adapter_refuses_email_authentication_even_if_setting_is_enabled(
    settings,
):
    from opendb.gateway.adapters import GoogleSocialAccountAdapter

    settings.SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
    assert not GoogleSocialAccountAdapter().can_authenticate_by_email(
        None, "owner@example.com"
    )


@pytest.mark.parametrize(
    ("extra", "process"),
    [
        ({"email_verified": False}, "login"),
        ({"sub": "wrong-subject"}, "login"),
        ({}, "connect"),
    ],
)
def test_allauth_adapter_rejects_unverified_or_conflicting_identity(rf, extra, process):
    from opendb.gateway.adapters import GoogleSocialAccountAdapter

    sociallogin = SocialLogin(
        account=SocialAccount(
            provider="google",
            uid="subject",
            extra_data={
                "sub": "subject",
                "email": "new@example.com",
                "email_verified": True,
                **extra,
            },
        )
    )
    sociallogin.state = {"process": process}
    with pytest.raises(PermissionDenied):
        GoogleSocialAccountAdapter().pre_social_login(rf.get("/"), sociallogin)
    assert not get_user_model().objects.exists()


def test_real_allauth_google_callback_creates_authenticated_api_session(
    settings, monkeypatch
):
    from urllib.parse import parse_qs
    from urllib.parse import urlsplit

    import requests

    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "APP": {"client_id": "mock-client", "secret": "mock-secret", "key": ""},
            "SCOPE": ["openid", "email", "profile"],
        }
    }
    settings.SOCIALACCOUNT_ADAPTER = (
        "opendb.gateway.adapters.GoogleSocialAccountAdapter"
    )
    settings.SOCIALACCOUNT_STORE_TOKENS = False
    calls = []

    def google_response(session, method, url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        if url == "https://oauth2.googleapis.com/token":
            body = {
                "access_token": "mock-access",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        elif url == "https://www.googleapis.com/oauth2/v2/userinfo":
            body = {
                "id": "callback-subject",
                "email": "callback@example.com",
                "verified_email": True,
            }
        else:
            pytest.fail(f"Unexpected Google HTTP request: {url}")
        response._content = json.dumps(body).encode()  # noqa: SLF001
        return response

    monkeypatch.setattr(requests.Session, "request", google_response)
    client = Client()
    start = client.post("/accounts/google/login/")
    assert start.status_code == 302
    state = parse_qs(urlsplit(start["Location"]).query)["state"][0]
    callback = client.get(
        "/accounts/google/login/callback/", {"code": "mock-code", "state": state}
    )
    assert callback.status_code == 302
    session = client.get("/api/session/")
    assert session.status_code == 200
    assert session.json()["user"]["email"] == "callback@example.com"
    assert len(calls) == 2
