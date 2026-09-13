"""One owner transaction for schema, records, provenance and replay result."""

import json

import psycopg
from pglast.parser import parse_sql_json
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from opendb.catalog import CatalogError
from opendb.catalog import describe
from opendb.databases.sql_policy import validate_sql

from .errors import IngestionError
from .errors import invalid
from .records import json_safe
from .records import write_record
from .validation import payload_hash
from .validation import validate_operation

DDL = {
    "CreateStmt",
    "AlterTableStmt",
    "ViewStmt",
    "IndexStmt",
    "DropStmt",
    "RenameStmt",
}


def _validate_statements(statements):
    for statement in statements:
        try:
            validate_sql(statement)
            parsed = json.loads(parse_sql_json(statement))["stmts"]
            if len(parsed) != 1 or next(iter(parsed[0]["stmt"])) not in DDL:
                raise invalid()
        except ValueError:
            msg = "invalid_statement"
            raise IngestionError(
                msg,
                "Only policy-approved transactional schema statements are allowed.",
            ) from None


def _require_owner(cur):
    cur.execute(
        "SELECT n.nspowner=r.oid AND NOT r.rolsuper FROM pg_catalog.pg_namespace n "
        "CROSS JOIN pg_catalog.pg_roles r "
        "WHERE n.nspname='data' AND r.rolname=current_user",
    )
    if cur.fetchone() != (True,):
        msg = "permission_denied"
        raise IngestionError(msg, "Ingestion requires the database owner connection.")


def _lock_relations(cur):
    cur.execute(
        "SELECT c.relname FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='data' AND c.relkind IN ('r','p') ORDER BY c.relname",
    )
    for (name,) in cur.fetchall():
        cur.execute(
            sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
                sql.Identifier("data", name)
            )
        )


def _database_error(exc):
    if exc.sqlstate == "POD01":
        return IngestionError(
            "idempotency_conflict",
            "This idempotency key was already used with a different payload.",
        )
    if exc.sqlstate == "42501":
        return IngestionError(
            "permission_denied", "The owner connection cannot perform this operation."
        )
    if exc.sqlstate in {"40001", "40P01", "55P03"}:
        return IngestionError(
            "schema_conflict",
            "Concurrent schema activity prevented ingestion; "
            "refresh the catalog and retry.",
        )
    if exc.sqlstate and exc.sqlstate.startswith("23"):
        return IngestionError(
            "constraint_violation",
            "A record violates a database constraint; no ingestion changes were saved.",
        )
    if exc.sqlstate == "POD02":
        return invalid()
    return IngestionError(
        "database_error",
        "Ingestion failed; check schema and typed records. "
        "If the connection failed, retry the identical operation "
        "to confirm its outcome.",
    )


def _indexing(cur, tables):
    cur.execute("SELECT pg_catalog.to_regprocedure('opendb_catalog.vector_status()')")
    if cur.fetchone()[0] is None:
        return {"status": "not_requested", "indexes": []}
    cur.execute("SELECT opendb_catalog.vector_status()")
    indexes = [row[0] for row in cur.fetchall() if row[0]["table"] in tables]
    state = "not_requested"
    if indexes:
        state = "ready"
        if any(index["failed"] for index in indexes):
            state = "failed"
        if any(index["pending"] or index["processing"] for index in indexes):
            state = "pending"
    return {"status": state, "indexes": indexes}


def apply(conn, operation):
    validate_operation(operation)
    _validate_statements(operation["statements"])
    if conn.info.transaction_status != TransactionStatus.IDLE:
        msg = "transaction_active"
        raise IngestionError(
            msg,
            "Ingestion requires a connection with no active transaction.",
        )
    try:
        with conn.transaction(), conn.cursor(row_factory=tuple_row) as cur:
            _require_owner(cur)
            cur.execute("SET LOCAL search_path = pg_catalog, data")
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute(
                "SELECT opendb_catalog.begin_ingestion(%s, %s, %s)",
                (
                    operation["idempotency_key"],
                    payload_hash(operation),
                    Jsonb(operation["source"]),
                ),
            )
            state = cur.fetchone()[0]
            if state["replay"]:
                return state["result"]
            _lock_relations(cur)
            if (
                describe(conn)["fingerprint"]
                != operation["expected_schema_fingerprint"]
            ):
                msg = "schema_conflict"
                raise IngestionError(
                    msg,
                    "The schema changed; refresh the catalog "
                    "and rebuild the operation.",
                )
            for statement in operation["statements"]:
                cur.execute(statement)
            returned = {}
            for record in operation["records"]:
                returned[record["ref"]] = write_record(cur, record, returned)
            for annotation in operation["annotations"]:
                cur.execute(
                    "SELECT opendb_catalog.annotate(%s, %s, %s, %s, %s)",
                    (
                        state["operation_id"],
                        annotation["table"],
                        annotation.get("column"),
                        annotation["description"],
                        Jsonb(annotation["metadata"]),
                    ),
                )
            result = {
                "operation_id": state["operation_id"],
                "records": json_safe(returned),
                "record_tables": {
                    record["ref"]: record["table"] for record in operation["records"]
                },
                "schema_fingerprint": describe(conn)["fingerprint"],
                "changes": {
                    key: len(operation[key])
                    for key in ("statements", "records", "annotations")
                },
                "indexing": _indexing(
                    cur, {"data." + record["table"] for record in operation["records"]}
                ),
            }
            cur.execute(
                "SELECT opendb_catalog.finish_ingestion(%s, %s)",
                (state["operation_id"], Jsonb(result)),
            )
            return result
    except CatalogError as exc:
        raise IngestionError(exc.code, str(exc)) from None
    except psycopg.Error as exc:
        raise _database_error(exc) from None
