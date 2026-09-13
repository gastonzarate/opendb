import threading
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import PermissionDenied
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from mcp.shared.auth import OAuthClientInformationFull


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def oauth_settings(settings, tmp_path):
    settings.OPENDB_GOOGLE_CLIENT_ID = "test-client.apps.googleusercontent.com"
    settings.OPENDB_GOOGLE_CLIENT_SECRET = "mock-google-secret"  # noqa: S105
    settings.OPENDB_MCP_BASE_URL = "https://mcp.example.com"
    settings.OPENDB_MCP_JWT_SIGNING_KEY = "test-signing-key-" * 3
    settings.OPENDB_MCP_STORAGE_ENCRYPTION_KEY = Fernet.generate_key().decode()
    settings.OPENDB_MCP_STATE_DIRECTORY = str(tmp_path / "oauth")
    settings.OPENDB_MCP_ALLOWED_CLIENT_REDIRECT_URIS = ["http://127.0.0.1:*/*"]
    return settings


def local_auth():
    return StaticTokenVerifier(
        tokens={"test-only": {"client_id": "test", "scopes": []}}
    )


def test_default_server_fails_closed_without_google_config(settings):
    from opendb.gateway.mcp import create_mcp

    settings.OPENDB_GOOGLE_CLIENT_ID = ""
    with pytest.raises(ImproperlyConfigured):
        create_mcp()


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("OPENDB_MCP_JWT_SIGNING_KEY", "short"),
        ("OPENDB_MCP_STORAGE_ENCRYPTION_KEY", "invalid-fernet"),
        ("OPENDB_MCP_BASE_URL", "http://public.example.com"),
        ("OPENDB_MCP_BASE_URL", "https://user:password@mcp.example.com"),
        ("OPENDB_MCP_STATE_DIRECTORY", "relative/oauth"),
    ],
)
def test_invalid_oauth_configuration_is_rejected(oauth_settings, setting, value):
    from opendb.gateway.oauth import build_google_provider

    setattr(oauth_settings, setting, value)
    with pytest.raises(ImproperlyConfigured):
        build_google_provider()


def test_oauth_storage_rejects_public_directory(oauth_settings):
    from pathlib import Path

    from opendb.gateway.oauth import build_google_provider

    directory = Path(oauth_settings.OPENDB_MCP_STATE_DIRECTORY)
    directory.mkdir(mode=0o755)
    with pytest.raises(ImproperlyConfigured):
        build_google_provider()


@pytest.mark.anyio
async def test_real_provider_client_state_survives_restart_encrypted(oauth_settings):
    from pathlib import Path

    from opendb.gateway.oauth import build_google_provider

    first = build_google_provider()
    assert isinstance(first, GoogleProvider)
    await first.register_client(
        OAuthClientInformationFull(
            client_id="test-mcp-client",
            client_name="sensitive-client-name",
            redirect_uris=["http://127.0.0.1:40000/callback"],
        )
    )
    second = build_google_provider()
    client = await second.get_client("test-mcp-client")
    assert client.client_name == "sensitive-client-name"
    directory = Path(oauth_settings.OPENDB_MCP_STATE_DIRECTORY)

    def check_disk():
        assert directory.stat().st_mode & 0o077 == 0
        assert all(
            b"sensitive-client-name" not in file.read_bytes()
            for file in directory.rglob("*")
            if file.is_file()
        )

    import anyio

    await anyio.to_thread.run_sync(check_disk)


@pytest.mark.anyio
async def test_http_rejects_anonymous_and_invalid_bearer_and_exposes_discovery(
    oauth_settings,
):
    from opendb.gateway.mcp import create_mcp

    app = create_mcp().http_app(path="/mcp")
    async with (
        app.lifespan(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.example.com",
        ) as client,
    ):
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            response = await client.post("/mcp", headers=headers, json={})
            assert response.status_code == 401
        response = await client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        assert response.json()["authorization_endpoint"].startswith(
            "https://mcp.example.com/"
        )


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_sdk_tools_share_dispatch_with_safe_django_thread_boundary():
    from opendb.gateway.contract import ACTIONS
    from opendb.gateway.mcp import create_mcp

    loop_thread = threading.get_ident()
    calls = []

    async def actor():
        return 123

    def dispatch(actor_id, action, payload):
        # This real ORM query raises SynchronousOnlyOperation on the event loop.
        get_user_model().objects.count()
        assert threading.get_ident() != loop_thread
        calls.append((actor_id, action, payload))
        return {"action": action, "actor": actor_id}

    server = create_mcp(
        auth_provider=local_auth(), actor_dependency=actor, dispatcher=dispatch
    )
    async with Client(server) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == set(ACTIONS)
        for tool in tools:
            assert tool.description
            assert "payload" in tool.inputSchema["properties"]
            assert "actor_id" not in tool.inputSchema["properties"]
            result = await client.call_tool(tool.name, {"payload": {}})
            assert result.data == {"result": {"action": tool.name, "actor": 123}}
    assert len(calls) == len(ACTIONS)
    assert "ingestion_history" in ACTIONS


@pytest.mark.anyio
async def test_slow_mcp_operation_does_not_block_revocation():
    import anyio

    from opendb.gateway.mcp import create_mcp

    started = threading.Event()
    release = threading.Event()
    results = []
    actor_ids = iter((123, 456))

    async def actor():
        return next(actor_ids)

    def dispatch(actor_id, action, payload):
        if action == "search_vectors":
            started.set()
            assert release.wait(timeout=5)
        return {"action": action, "actor_id": actor_id}

    server = create_mcp(
        auth_provider=local_auth(),
        actor_dependency=actor,
        dispatcher=dispatch,
    )
    async with Client(server) as client, anyio.create_task_group() as group:

        async def slow_operation():
            results.append(await client.call_tool("search_vectors", {"payload": {}}))

        group.start_soon(slow_operation)
        try:
            assert await anyio.to_thread.run_sync(started.wait, 2)
            with anyio.fail_after(1):
                revoked = await client.call_tool("revoke_role", {"payload": {}})
            assert revoked.data == {
                "result": {"action": "revoke_role", "actor_id": 456},
            }
        finally:
            release.set()
    assert results[0].data == {
        "result": {"action": "search_vectors", "actor_id": 123},
    }


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_mcp_concurrency_is_bounded_and_worker_connections_are_closed(settings):
    import anyio
    from django.db import connection
    from django.db import connections

    from opendb.gateway.mcp import create_mcp

    settings.OPENDB_MCP_MAX_CONCURRENT_OPERATIONS = 2
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    worker_connections = []
    active = 0
    peak = 0

    async def actor():
        return 123

    def dispatch(actor_id, action, payload):
        nonlocal active, peak
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        with lock:
            active += 1
            peak = max(peak, active)
            worker_connections.append(connections["default"])
            if active == 2:
                started.set()
        try:
            assert release.wait(timeout=5)
            if payload.get("fail"):
                message = "Invalid operation for cleanup regression."
                raise ValueError(message)
            return action
        finally:
            with lock:
                active -= 1

    async def call(client, payload):
        if payload.get("fail"):
            with pytest.raises(ToolError, match="cleanup regression"):
                await client.call_tool("query", {"payload": payload})
        else:
            await client.call_tool("query", {"payload": payload})

    server = create_mcp(
        auth_provider=local_auth(),
        actor_dependency=actor,
        dispatcher=dispatch,
    )
    async with Client(server) as client, anyio.create_task_group() as group:
        for payload in ({}, {}, {"fail": True}):
            group.start_soon(call, client, payload)
        try:
            assert await anyio.to_thread.run_sync(started.wait, 2)
            await anyio.sleep(0.05)
            assert peak == 2
        finally:
            release.set()
    assert peak == 2
    assert len(worker_connections) == 3
    assert all(worker.connection is None for worker in worker_connections)


@pytest.mark.parametrize("limit", [0, -1, True, "4"])
def test_mcp_rejects_invalid_concurrency_limits(settings, limit):
    from opendb.gateway.mcp import create_mcp

    settings.OPENDB_MCP_MAX_CONCURRENT_OPERATIONS = limit
    with pytest.raises(ImproperlyConfigured):
        create_mcp(auth_provider=local_auth())


@pytest.mark.anyio
async def test_inprocess_sdk_cannot_implicitly_bypass_identity():
    from opendb.gateway.mcp import create_mcp

    server = create_mcp(auth_provider=local_auth())
    async with Client(server) as client:
        with pytest.raises(ToolError, match="Google authentication required"):
            await client.call_tool("list_databases", {"payload": {}})


@pytest.mark.anyio
async def test_mcp_rejects_actor_override_and_masks_permission_details():
    from opendb.gateway.mcp import create_mcp

    async def actor():
        return 123

    def denied(*args):
        msg = "private customer_secret_table"
        raise PermissionDenied(msg)

    server = create_mcp(
        auth_provider=local_auth(), actor_dependency=actor, dispatcher=denied
    )
    async with Client(server) as client:
        with pytest.raises(ToolError, match="identity"):
            await client.call_tool("query", {"payload": {"actor_id": 999}})
        with pytest.raises(ToolError, match="permission") as error:
            await client.call_tool("catalog", {"payload": {}})
        assert "customer_secret" not in str(error.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "other-app"},
        {"email_verified": False},
        {"sub": ""},
    ],
)
async def test_provider_enforces_google_audience_and_identity(
    oauth_settings, monkeypatch, changes
):
    from opendb.gateway.oauth import build_google_provider

    async def upstream(self, token):
        return AccessToken(
            token=token,
            client_id="subject",
            scopes=["openid"],
            expires_at=int(time.time()) + 60,
            claims={
                "sub": "subject",
                "aud": oauth_settings.OPENDB_GOOGLE_CLIENT_ID,
                "email": "owner@example.com",
                "email_verified": True,
                **changes,
            },
        )

    monkeypatch.setattr(GoogleProvider, "load_access_token", upstream)
    assert await build_google_provider().load_access_token("mock-token") is None


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_validated_sdk_google_token_resolves_django_actor(
    oauth_settings, monkeypatch
):
    from opendb.gateway import mcp

    token = AccessToken(
        token="mock-token",  # noqa: S106 -- Fabricated SDK token for offline tests.
        client_id="subject",
        scopes=["openid"],
        expires_at=int(time.time()) + 60,
        claims={
            "sub": "subject",
            "aud": oauth_settings.OPENDB_GOOGLE_CLIENT_ID,
            "email": "sdk@example.com",
            "email_verified": "true",
        },
    )
    monkeypatch.setattr(mcp, "get_access_token", lambda: token)
    actor_id = await mcp.google_actor()
    assert actor_id > 0
    assert await mcp.google_actor() == actor_id


def test_standalone_asgi_factory(oauth_settings):
    from opendb.gateway.asgi import create_app

    assert callable(create_app())


@pytest.mark.anyio
async def test_sdk_ingestion_resource_example_passes_peer_validator(monkeypatch):
    import json

    from opendb import ingestion
    from opendb.gateway.mcp import create_mcp
    from opendb.ingestion.validation import validate_operation

    schema = ingestion.operation_schema()
    example = ingestion.example_operation("meeting", "0" * 64)
    loop_thread = threading.get_ident()
    calls = []

    def authoritative_schema():
        assert threading.get_ident() != loop_thread
        calls.append("schema")
        return schema

    def authoritative_example(kind, fingerprint):
        assert threading.get_ident() != loop_thread
        assert kind == "meeting"
        assert fingerprint == "0" * 64
        calls.append(kind)
        return example

    monkeypatch.setattr(ingestion, "operation_schema", authoritative_schema)
    monkeypatch.setattr(ingestion, "example_operation", authoritative_example)

    async def actor():
        return 123

    server = create_mcp(auth_provider=local_auth(), actor_dependency=actor)
    async with Client(server) as client:
        resources = await client.list_resources()
        assert "opendb://guides/ingestion" in {
            str(resource.uri) for resource in resources
        }
        contents = await client.read_resource("opendb://guides/ingestion")
        guide = json.loads(contents[0].text)
        assert calls == ["schema", "meeting"]
        assert guide["schema"] == schema
        assert guide["example_operation"] == example
        example = guide["example_operation"]
        validate_operation(example)
        assert set(example) == {
            "version",
            "idempotency_key",
            "expected_schema_fingerprint",
            "source",
            "statements",
            "records",
            "annotations",
        }
        assert guide["schema"]["additionalProperties"] is False
        from jsonschema import Draft202012Validator

        Draft202012Validator(guide["schema"]).validate(example)


@pytest.mark.anyio
async def test_ingestion_resource_does_not_bypass_inprocess_auth():
    from opendb.gateway.mcp import create_mcp

    async with Client(create_mcp(auth_provider=local_auth())) as client:
        from mcp.shared.exceptions import McpError

        with pytest.raises(McpError, match="Google authentication required"):
            await client.read_resource("opendb://guides/ingestion")
