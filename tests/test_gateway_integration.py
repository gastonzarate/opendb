"""Transport-to-service checks against the parent's disposable customer cluster."""

import asyncio
import json

import pytest
from allauth.socialaccount.models import SocialAccount
from django.test import Client as WebClient
from django.urls import include
from django.urls import path
from fastmcp import Client as MCPClient
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from tests.integration.conftest import isolated_resources  # noqa: F401
from tests.integration.conftest import personal_db as personal_database_fixture

personal_db = personal_database_fixture

pytestmark = pytest.mark.django_db(transaction=True)


def test_real_dispatch_is_shared_by_session_api_and_sdk(personal_db, settings):
    from opendb.gateway.mcp import create_mcp
    from opendb.gateway.urls import urlpatterns as gateway_patterns

    settings.ROOT_URLCONF = __name__
    globals()["urlpatterns"] = [path("api/", include(gateway_patterns))]
    owner = personal_db.owner
    SocialAccount.objects.create(user=owner, provider="google", uid="integration-owner")
    web = WebClient(enforce_csrf_checks=True)
    web.force_login(owner)
    session = web.session
    session["account_authentication_methods"] = [
        {"method": "socialaccount", "provider": "google", "uid": "integration-owner"},
    ]
    session.save()
    csrf = web.get("/api/session/").json()["csrf_token"]
    response = web.post(
        "/api/actions/query/",
        {
            "database_id": str(personal_db.id),
            "sql": "SELECT $1::text AS greeting",
            "parameters": ["hello"],
        },
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200
    expected = {"columns": ["greeting"], "rows": [["hello"]], "truncated": False}
    assert response.json()["result"] == expected

    async def actor():
        return owner.pk

    async def exercise():
        server = create_mcp(
            auth_provider=StaticTokenVerifier(tokens={}),
            actor_dependency=actor,
        )
        async with MCPClient(server) as client:
            result = await client.call_tool(
                "query",
                {
                    "payload": {
                        "database_id": str(personal_db.id),
                        "sql": "SELECT $1::text AS greeting",
                        "parameters": ["hello"],
                    }
                },
            )
            assert result.data == {"result": expected}
            databases = await client.call_tool("list_databases", {"payload": {}})
            assert databases.data == {
                "result": [
                    {"id": str(personal_db.id), "status": "ready", "is_owner": True}
                ]
            }
            guide = await client.read_resource("opendb://guides/ingestion")
            operation = json.loads(guide[0].text)["example_operation"]
            catalog = await client.call_tool(
                "catalog",
                {
                    "payload": {
                        "database_id": str(personal_db.id),
                    }
                },
            )
            operation["expected_schema_fingerprint"] = catalog.data["result"][
                "fingerprint"
            ]
            payload = {"database_id": str(personal_db.id), "operation": operation}
            ingested = await client.call_tool("ingest", {"payload": payload})
            assert "meeting" in ingested.data["result"]["records"]
            replay = await client.call_tool("ingest", {"payload": payload})
            assert replay.data == ingested.data
            rows = await client.call_tool(
                "query",
                {
                    "payload": {
                        "database_id": str(personal_db.id),
                        "sql": (
                            "SELECT m.title, m.held_on, m.timezone, p.label, "
                            "p.person_id, t.ordinal, t.body, t.starts_at, t.ends_at "
                            "FROM data.meetings m "
                            "JOIN data.participants p ON p.meeting_id = m.id "
                            "JOIN data.turns t ON t.meeting_id = m.id "
                            "AND t.participant_id = p.id ORDER BY t.ordinal"
                        ),
                    }
                },
            )
            assert rows.data["result"]["rows"] == [
                [None, None, None, "Unknown speaker", None, 1, "Hello.", None, None],
                [None, None, None, "Alice", None, 2, "Welcome.", "3.000", None],
            ]

    asyncio.run(exercise())


def test_sdk_service_rejects_a_different_owner(personal_db):
    from django.contrib.auth import get_user_model

    from opendb.gateway.mcp import create_mcp

    stranger = get_user_model().objects.create_user(email="stranger@example.com")

    async def actor():
        return stranger.pk

    async def exercise():
        server = create_mcp(
            auth_provider=StaticTokenVerifier(tokens={}),
            actor_dependency=actor,
        )
        async with MCPClient(server) as client:
            with pytest.raises(ToolError, match="permission"):
                await client.call_tool(
                    "query",
                    {
                        "payload": {
                            "database_id": str(personal_db.id),
                            "sql": "SELECT 1",
                        }
                    },
                )

    asyncio.run(exercise())
