"""Run the separate authenticated MCP service with python -m opendb.gateway."""

import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run(
        "opendb.gateway.asgi:create_app", factory=True, host=args.host, port=args.port
    )


if __name__ == "__main__":
    main()
