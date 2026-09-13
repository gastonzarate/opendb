"""Two personal databases and one guest exercised through the actual MCP SDK."""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from psycopg import sql

from opendb.databases.connections import privileged_connection
from opendb.gateway.mcp import create_mcp
from opendb.ingestion import example_operation
from opendb.users.tests.factories import UserFactory
from opendb.vectors import process_pending
from tests.integration.test_vectors import FakeEmbedder

pytestmark = pytest.mark.django_db(transaction=True)


def test_meeting_expenses_and_guest_through_mcp(personal_db, monkeypatch):  # noqa: PLR0915 -- One acceptance flow across transports and identities.
    from opendb.vectors.embeddings import LocalEmbedder

    monkeypatch.setattr(LocalEmbedder, "tokenize", FakeEmbedder.tokenize)
    monkeypatch.setattr(LocalEmbedder, "detokenize", FakeEmbedder.detokenize)
    monkeypatch.setattr(LocalEmbedder, "embed", FakeEmbedder.embed)
    second = UserFactory()
    guest = UserFactory()
    owner_id = personal_db.owner_id
    database_id = str(personal_db.id)

    def server(user_id):
        async def actor():
            return user_id

        return create_mcp(
            auth_provider=StaticTokenVerifier(tokens={}), actor_dependency=actor
        )

    def work():
        with privileged_connection(database_id) as conn:
            return process_pending(conn, FakeEmbedder())

    async def call(client, action, **payload):
        result = await client.call_tool(action, {"payload": payload})
        return result.data["result"]

    async def exercise():
        async with (
            Client(server(owner_id)) as owner,
            Client(server(second.pk)) as other,
            Client(server(guest.pk)) as invitee,
        ):
            other_db = await call(other, "create_database")
            catalog = await call(owner, "catalog", database_id=database_id)
            operation = example_operation("meeting", catalog["fingerprint"])
            first = await call(
                owner, "ingest", database_id=database_id, operation=operation
            )
            assert (
                await call(
                    owner, "ingest", database_id=database_id, operation=operation
                )
                == first
            )
            expenses_catalog = await call(other, "catalog", database_id=other_db["id"])
            await call(
                other,
                "ingest",
                database_id=other_db["id"],
                operation=example_operation(
                    "expenses", expenses_catalog["fingerprint"]
                ),
            )
            total = await call(
                other,
                "query",
                database_id=other_db["id"],
                sql="SELECT sum(amount) FROM data.expenses",
            )
            assert total["rows"] == [["12.50"]]
            with pytest.raises(ToolError):
                await call(
                    owner,
                    "query",
                    database_id=other_db["id"],
                    sql="SELECT * FROM data.expenses",
                )
            await call(
                owner,
                "query",
                database_id=database_id,
                sql=(
                    "CREATE VIEW data.shared_turns AS SELECT ordinal, body "
                    "FROM data.turns WHERE ordinal = 1"
                ),
            )
            role = await call(
                owner, "create_role", database_id=database_id, name="reader"
            )
            await call(
                owner, "grant_object", role_id=role["id"], object_name="shared_turns"
            )
            await call(owner, "assign_role", role_id=role["id"], email=guest.email)
            rows = await call(
                invitee,
                "query",
                database_id=database_id,
                sql="SELECT * FROM data.shared_turns",
            )
            assert rows["rows"] == [[1, "Hello."]]
            with pytest.raises(ToolError):
                await call(
                    invitee,
                    "query",
                    database_id=database_id,
                    sql="SELECT * FROM data.turns",
                )
            with pytest.raises(ToolError):
                await call(
                    invitee,
                    "query",
                    database_id=database_id,
                    sql="INSERT INTO data.turns (body) VALUES ('forbidden')",
                )
            index = await call(
                owner,
                "register_vector",
                database_id=database_id,
                table="turns",
                key_column="id",
                text_column="body",
            )
            assert (await sync_to_async(work)())["processed"] == 2
            assert (
                len(
                    await call(
                        owner,
                        "search_vectors",
                        database_id=database_id,
                        index_id=index["index_id"],
                        query="hello",
                    )
                )
                == 2
            )
            with pytest.raises(ToolError):
                await call(
                    invitee,
                    "search_vectors",
                    database_id=database_id,
                    index_id=index["index_id"],
                    query="hello",
                )
            await call(
                owner,
                "query",
                database_id=database_id,
                sql=sql.SQL(
                    "CREATE VIEW data.vector_excerpt AS SELECT * FROM {} "
                    "WHERE source_key = '1'::jsonb"
                )
                .format(sql.Identifier("data", index["view_name"]))
                .as_string(),
            )
            await call(
                owner, "grant_object", role_id=role["id"], object_name="vector_excerpt"
            )
            excerpt = await call(
                invitee,
                "search_vectors",
                database_id=database_id,
                index_id=index["index_id"],
                query="hello",
                target_view="vector_excerpt",
                filters={"chunk_order": 0},
            )
            assert [hit["text"] for hit in excerpt] == ["Hello."]
            await call(
                owner,
                "query",
                database_id=database_id,
                sql="UPDATE data.turns SET body = 'Changed' WHERE ordinal = 1",
            )
            hits = await call(
                owner,
                "search_vectors",
                database_id=database_id,
                index_id=index["index_id"],
                query="hello",
            )
            assert all(hit["text"] != "Hello." for hit in hits)
            await call(owner, "revoke_role", role_id=role["id"], email=guest.email)
            with pytest.raises(ToolError):
                await call(
                    invitee,
                    "query",
                    database_id=database_id,
                    sql="SELECT * FROM data.shared_turns",
                )

    asyncio.run(exercise())
