"""FastMCP tools over the same synchronous application services as the web API."""

from functools import partial
from typing import Any

from anyio import CapacityLimiter
from anyio import to_thread
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.db import connections
from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token

from .contract import ACTIONS
from .contract import INSTRUCTIONS
from .contract import dispatch
from .contract import validate_payload
from .identity import resolve_google_user
from .ingestion_guide import ingestion_guide
from .oauth import build_google_provider
from .oauth import verified_token_claims


def _django_call(function, *args):
    # MCP is outside Django's request/response lifecycle. Check connection age
    # in the same thread that uses the ORM, including when a service raises.
    close_old_connections()
    try:
        return function(*args)
    finally:
        # Pool threads outlive requests. Do not retain a connection or session
        # state for the next actor, including after cancellation or failure.
        connections.close_all()


def _resolve_actor(claims):
    return resolve_google_user(claims).pk


async def google_actor(*, limiter=None):
    """Get the actor from FastMCP's verified request token, never tool arguments."""
    claims = verified_token_claims(get_access_token())
    return await to_thread.run_sync(
        _django_call,
        _resolve_actor,
        claims,
        limiter=limiter,
    )


def _tool(action, actor_dependency, dispatcher, limiter):
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            actor_id = await actor_dependency()
            validate_payload(payload)
            result = await to_thread.run_sync(
                _django_call,
                dispatcher,
                actor_id,
                action,
                payload,
                limiter=limiter,
            )
        except PermissionDenied as exc:
            # Authentication and permission failures must not leak object names.
            message = "Google authentication required or insufficient permission."
            raise ToolError(message) from exc
        except ObjectDoesNotExist as exc:
            msg = "Requested resource was not found."
            raise ToolError(msg) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except (KeyError, TypeError, ValidationError) as exc:
            message = (
                "Missing or invalid operation field; check the tool payload contract."
            )
            raise ToolError(message) from exc
        else:
            return {"result": result}

    operation.__name__ = action
    operation.__doc__ = ACTIONS[action]
    return operation


def create_mcp(*, auth_provider=None, actor_dependency=None, dispatcher=None):
    """Build an authenticated server. Test overrides must be explicit Python calls.

    In-process SDK clients bypass HTTP middleware, but every tool still requires
    google_actor unless a test explicitly supplies actor_dependency. There is no
    environment setting or CLI option that disables authentication.
    """
    limit = getattr(settings, "OPENDB_MCP_MAX_CONCURRENT_OPERATIONS", 4)
    if type(limit) is not int or limit < 1:
        message = "OPENDB_MCP_MAX_CONCURRENT_OPERATIONS must be a positive integer."
        raise ImproperlyConfigured(message)
    limiter = CapacityLimiter(limit)
    server = FastMCP(
        "OpenDB",
        instructions=INSTRUCTIONS,
        auth=auth_provider if auth_provider is not None else build_google_provider(),
        mask_error_details=True,
    )
    actor_dependency = actor_dependency or partial(google_actor, limiter=limiter)
    dispatcher = dispatcher or dispatch

    @server.resource(
        "opendb://guides/ingestion",
        mime_type="application/json",
        description=(
            "Ingestion JSON Schema, semantic rules and a complete "
            "linked-record example. Read before ingest."
        ),
    )
    async def read_ingestion_guide() -> dict[str, Any]:
        try:
            await actor_dependency()
        except PermissionDenied as exc:
            message = "Google authentication required."
            raise ResourceError(message) from exc
        return await to_thread.run_sync(ingestion_guide, limiter=limiter)

    readonly = {
        "list_databases",
        "catalog",
        "list_access",
        "saving_instructions",
        "search_vectors",
        "vector_status",
        "ingestion_history",
    }
    for action in ACTIONS:
        server.tool(
            name=action,
            annotations={
                "readOnlyHint": action in readonly,
                "destructiveHint": action not in readonly,
                "openWorldHint": False,
            },
        )(_tool(action, actor_dependency, dispatcher, limiter))
    return server
