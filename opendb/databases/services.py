"""Single authorization boundary shared by MCP and the web API."""

import base64
import datetime
import json
from contextlib import closing
from uuid import UUID

import psycopg
from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from pglast.parser import parse_sql_json
from psycopg.rows import dict_row

from . import sharing
from .connections import data_connection
from .connections import owner_connection
from .connections import privileged_connection
from .connections import require_owner
from .models import PersonalDatabase
from .provisioning import provision_personal_database
from .sql_policy import validate_sql

MAX_ROWS = 500
MAX_RESULT_BYTES = 4 * 1024 * 1024


class SQLJSONEncoder(DjangoJSONEncoder):
    def default(self, value):
        if isinstance(value, (bytes, memoryview)):
            return {"type": "bytea", "base64": base64.b64encode(value).decode("ascii")}
        if isinstance(value, datetime.time):
            return value.isoformat()
        return super().default(value)


def _json_safe(value):
    return json.loads(json.dumps(value, cls=SQLJSONEncoder, allow_nan=False))


def dispatch(actor_id: int, action: str, payload: dict):
    user = get_user_model().objects.get(pk=actor_id, is_active=True)
    try:
        result = _dispatch(user, action, payload)
    except psycopg.errors.InsufficientPrivilege as exc:
        from .exceptions import DatabaseAccessDenied

        msg = "Operation not permitted"
        raise DatabaseAccessDenied(msg) from exc
    except PermissionError as exc:
        from .exceptions import DatabaseAccessDenied

        msg = "Operation not permitted"
        raise DatabaseAccessDenied(msg) from exc
    except psycopg.Error as exc:
        # Do not echo DETAIL: constraint errors can contain complete private rows.
        msg = f"database_{exc.sqlstate or 'unavailable'}: operation failed"
        raise ValueError(msg) from None
    except KeyError as exc:
        msg = "Required operation field is missing"
        raise ValueError(msg) from exc
    return _json_safe(result)


def _dispatch(user, action, p):
    if action == "list_databases":
        dbs = PersonalDatabase.objects.filter(
            Q(owner=user) | Q(accessrole__roleassignment__email__iexact=user.email)
        ).distinct()
        return [
            {"id": str(db.id), "status": db.status, "is_owner": db.owner_id == user.pk}
            for db in dbs
        ]
    if action == "create_database":
        db = provision_personal_database(user.pk)
        return {"id": str(db.id), "status": db.status}
    if action in {
        "create_role",
        "grant_object",
        "assign_role",
        "revoke_role",
        "revoke_object",
        "list_access",
    }:
        if action == "create_role":
            role = sharing.create_role(user.pk, p["database_id"], p["name"])
            return {"id": str(role.pk), "name": role.name}
        if action == "list_access":
            return sharing.list_access(user.pk, p["database_id"])
        if action in {"grant_object", "revoke_object"}:
            return getattr(sharing, action)(user.pk, p["role_id"], p["object_name"])
        return getattr(sharing, action)(user.pk, p["role_id"], p["email"])
    database_id = p["database_id"]
    if action == "ingestion_history":
        return _ingestion_history(user.pk, database_id, p.get("operation_id"))
    if action == "ingest":
        from opendb.ingestion import apply

        with owner_connection(user.pk, database_id) as conn:
            return apply(conn, p["operation"])
    if action == "register_vector":
        require_owner(user.pk, database_id)
        from opendb.vectors import register

        with privileged_connection(database_id) as conn:
            return register(conn, p["table"], p["key_column"], p["text_column"])
    with data_connection(user.pk, database_id) as conn:
        if action == "catalog":
            from opendb.catalog import describe

            return describe(conn)
        if action == "vector_status":
            from opendb.vectors import status

            return status(conn)
        if action == "search_vectors":
            from opendb.vectors import search

            return search(
                conn,
                p["index_id"],
                p["query"],
                limit=p.get("limit", 10),
                target_view=p.get("target_view"),
                filters=p.get("filters"),
            )
        if action == "query":
            db = PersonalDatabase.objects.get(pk=database_id)
            return _query(conn, p, readonly=db.owner_id != user.pk)
        msg = "Unknown action"
        raise ValueError(msg)


def _ingestion_history(actor_id, database_id, operation_id):
    require_owner(actor_id, database_id)
    with (
        privileged_connection(database_id) as conn,
        conn.cursor(row_factory=dict_row) as cursor,
    ):
        if operation_id:
            operation_id = UUID(str(operation_id))
            cursor.execute(
                "SELECT id, idempotency_key, created_at, source, result "
                "FROM opendb_catalog.ingestion_operations WHERE id = %s",
                (operation_id,),
            )
            record = cursor.fetchone()
            if record is None:
                msg = "Ingestion operation not found"
                raise ValueError(msg)
            return record
        cursor.execute(
            "SELECT id, idempotency_key, created_at, "
            "source->>'name' AS source_name, result "
            "FROM opendb_catalog.ingestion_operations "
            "ORDER BY created_at DESC, id DESC LIMIT 100"
        )
        return cursor.fetchall()


def _query(conn, payload, *, readonly):
    from opendb.catalog.install import SCHEMA_LOCK_KEY

    statement = payload["sql"]
    validate_sql(statement, readonly=readonly)
    parsed = json.loads(parse_sql_json(statement))["stmts"][0]["stmt"]
    kind, node = next(iter(parsed.items()))
    params = payload.get("parameters")
    if params is not None and not isinstance(params, list):
        msg = "parameters must be a list using $1 placeholders"
        raise ValueError(msg)
    with conn.transaction():
        if readonly:
            conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '5s'")
        conn.execute("SET LOCAL lock_timeout = '2s'")
        conn.execute("SET LOCAL work_mem = '4MB'")
        if not readonly:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        if kind == "SelectStmt":
            with psycopg.RawServerCursor(conn, "opendb_result") as cursor:
                cursor.execute(statement, params)
                return _bounded_result(cursor, cursor.fetchmany(MAX_ROWS + 1))
        with psycopg.RawCursor(conn) as cursor:
            if node.get("returningClause") or node.get("returningList"):
                # Consume all RETURNING rows to finish the mutation, buffering only
                # the bounded response. Exceptions roll back the source mutation.
                with closing(cursor.stream(statement, params)) as rows:
                    return _bounded_result(cursor, rows)
            cursor.execute(statement, params)
            return {"affected_rows": cursor.rowcount}


def _bounded_result(cursor, source_rows):
    result_rows = []
    size = 0
    truncated = False
    for row in source_rows:
        if len(result_rows) >= MAX_ROWS:
            truncated = True
            continue
        encoded = json.dumps(row, cls=SQLJSONEncoder, allow_nan=False)
        size += len(encoded.encode("utf-8"))
        if size > MAX_RESULT_BYTES:
            msg = "Query result exceeds the response size limit"
            raise ValueError(msg)
        result_rows.append(json.loads(encoded))
    result = {
        "columns": [column.name for column in cursor.description or ()],
        "rows": result_rows,
        "truncated": truncated,
    }
    if len(json.dumps({"result": result}).encode("utf-8")) > MAX_RESULT_BYTES:
        msg = "Query result exceeds the response size limit"
        raise ValueError(msg)
    return result
