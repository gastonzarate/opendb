import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action",
    ["delete_database", "complete_onboarding", "update_saving_instructions"],
)
async def test_web_only_actions_cannot_be_called_as_mcp_tools(action):
    from opendb.gateway.contract import ACTIONS
    from opendb.gateway.contract import WEB_ONLY_ACTIONS
    from opendb.gateway.mcp import create_mcp
    from tests.test_gateway_mcp import local_auth

    assert action in WEB_ONLY_ACTIONS
    assert action not in ACTIONS

    async def actor():
        return 123

    async with Client(
        create_mcp(auth_provider=local_auth(), actor_dependency=actor)
    ) as client:
        assert action not in {tool.name for tool in await client.list_tools()}
        with pytest.raises(ToolError, match=r"[Uu]nknown tool"):
            await client.call_tool(action, {"payload": {"database_id": "unused"}})
