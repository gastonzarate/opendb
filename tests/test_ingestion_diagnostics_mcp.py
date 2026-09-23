"""Client-visible recovery without requiring MCP resource support."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_tool_only_client_can_discover_and_validate_ingestion_format():
    from asgiref.sync import sync_to_async

    from opendb.gateway.mcp import create_mcp
    from opendb.ingestion.validation import validate_operation
    from opendb.users.tests.factories import UserFactory

    user = await sync_to_async(UserFactory)()

    async def actor():
        return user.pk

    server = create_mcp(
        auth_provider=StaticTokenVerifier(tokens={}),
        actor_dependency=actor,
    )
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "ingestion_guide" in tools
        assert tools["ingestion_guide"].annotations.readOnlyHint is True
        result = await client.call_tool("ingestion_guide", {"payload": {}})
        guide = result.data["result"]
        validate_operation(guide["example_operation"])
        resource = await client.read_resource("opendb://guides/ingestion")
        assert guide == json.loads(resource[0].text)
        assert "jsonb_to_recordset" in guide["query_policy"]["allowed_functions"]
        assert "ingestion_guide" in tools["ingest"].description


@pytest.mark.anyio
async def test_ingestion_guide_tool_requires_authentication():
    from opendb.gateway.mcp import create_mcp

    server = create_mcp(auth_provider=StaticTokenVerifier(tokens={}))
    async with Client(server) as client:
        with pytest.raises(ToolError, match="Authentication required"):
            await client.call_tool("ingestion_guide", {"payload": {}})
