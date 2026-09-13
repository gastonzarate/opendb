"""Standalone factory: uvicorn opendb.gateway.asgi:create_app --factory."""

import django


def create_app():
    django.setup()

    from .mcp import create_mcp  # noqa: PLC0415 -- Models require django.setup().

    return create_mcp().http_app(path="/mcp", stateless_http=True)
