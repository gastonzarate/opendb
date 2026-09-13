import pytest
from django.templatetags.static import static
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_home_redirects_to_public_app(client):
    assert reverse("home") == "/"
    response = client.get(reverse("home"))
    assert response.status_code == 302
    assert response["Location"] == "/app/"


def test_public_app_loads_fixed_static_assets(client, settings):
    settings.OPENDB_GOOGLE_CLIENT_SECRET = "private-google-secret"  # noqa: S105
    response = client.get("/app/")
    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="root"' in body
    assert f'src="{static("app/main.js")}"' in body
    assert f'href="{static("app/app.css")}"' in body
    assert "private-google-secret" not in body
    assert "csrf_token" not in body


@pytest.mark.parametrize(
    ("client_id", "secret", "configured"),
    [
        ("", "", False),
        ("public-id", "", False),
        ("", "secret", False),
        ("public-id", "secret", True),
    ],
)
def test_bootstrap_is_public_and_contains_only_public_fields(
    client, settings, client_id, secret, configured
):
    settings.OPENDB_GOOGLE_CLIENT_ID = client_id
    settings.OPENDB_GOOGLE_CLIENT_SECRET = secret
    settings.OPENDB_MCP_BASE_URL = "https://mcp.example.com"
    response = client.get("/api/bootstrap/")
    assert response.status_code == 200
    data = response.json()
    assert data == {
        "csrf_token": data["csrf_token"],
        "google_configured": configured,
        "mcp_url": "https://mcp.example.com/mcp",
    }
    assert data["csrf_token"]
    assert response.cookies[settings.CSRF_COOKIE_NAME].value
    assert response["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response


@pytest.mark.parametrize(
    "method", ["post", "put", "patch", "delete", "head", "options"]
)
def test_bootstrap_accepts_only_get(client, method):
    response = getattr(client, method)("/api/bootstrap/")
    assert response.status_code == 405
    assert response["Allow"] == "GET"


def test_bootstrap_token_allows_same_origin_logout_form(settings):
    client = Client(enforce_csrf_checks=True)
    response = client.get("/api/bootstrap/")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    assert client.post("/accounts/logout/", {"next": "/app/"}).status_code == 403
    response = client.post(
        "/accounts/logout/", {"csrfmiddlewaretoken": token, "next": "/app/"}
    )
    assert response.status_code == 302
    assert response["Location"] == "/app/"
    assert client.get("/api/session/").status_code == 401


def test_bootstrap_token_allows_google_login_without_bypassing_auth(settings):
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "APP": {"client_id": "test-client", "secret": "test-secret", "key": ""},
            "SCOPE": ["openid", "email", "profile"],
        }
    }
    client = Client(enforce_csrf_checks=True)
    response = client.get("/api/bootstrap/")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    assert client.post("/accounts/google/login/", {"next": "/app/"}).status_code == 403
    response = client.post(
        "/accounts/google/login/", {"csrfmiddlewaretoken": token, "next": "/app/"}
    )
    assert response.status_code == 302
    assert response["Location"].startswith("https://accounts.google.com/")
    assert client.get("/api/session/").status_code == 401


@pytest.mark.parametrize("suffix", ["/", "///"])
def test_bootstrap_normalizes_mcp_base_url(client, settings, suffix):
    settings.OPENDB_MCP_BASE_URL = "https://mcp.example.com" + suffix
    response = client.get("/api/bootstrap/")
    assert response.status_code == 200
    assert response.json()["mcp_url"] == "https://mcp.example.com/mcp"


@pytest.fixture
def local_toolbar_callback(monkeypatch, settings):
    from django.utils.module_loading import import_string

    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "debug_toolbar"]
    monkeypatch.setenv("USE_DOCKER", "no")
    from config.settings import local

    return import_string(
        local.DEBUG_TOOLBAR_CONFIG.get(
            "SHOW_TOOLBAR_CALLBACK", "debug_toolbar.middleware.show_toolbar"
        )
    )


@pytest.mark.parametrize(
    "path", ["/", "/app/", "/app/nested/", "/api/", "/api/bootstrap/"]
)
def test_local_toolbar_excludes_spa_and_api(local_toolbar_callback, rf, settings, path):
    settings.DEBUG = True
    settings.INTERNAL_IPS = ["127.0.0.1"]
    assert not local_toolbar_callback(rf.get(path, REMOTE_ADDR="127.0.0.1"))


@pytest.mark.parametrize(
    "visibility",
    [
        (True, "127.0.0.1", True),
        (False, "127.0.0.1", False),
        (True, "192.0.2.1", False),
    ],
)
@pytest.mark.parametrize("path", ["/about/", "/accounts/login/", "/application/"])
def test_local_toolbar_preserves_default_visibility(
    local_toolbar_callback, rf, settings, visibility, path
):
    debug, address, expected = visibility
    settings.DEBUG = debug
    settings.INTERNAL_IPS = ["127.0.0.1"]
    assert local_toolbar_callback(rf.get(path, REMOTE_ADDR=address)) is expected
