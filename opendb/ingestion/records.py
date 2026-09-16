"""Parameterized row writes with strict returned-value and source references."""

import datetime as dt
from decimal import Decimal
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from .errors import invalid
from .validation import decode_value

POSTGRES_TYPES = {
    "text": {"text", "varchar", "bpchar"},
    "integer": {"int2", "int4", "int8"},
    "numeric": {"numeric"},
    "float": {"float4", "float8"},
    "boolean": {"bool"},
    "date": {"date"},
    "time": {"time"},
    "timestamp": {"timestamp"},
    "timestamptz": {"timestamptz"},
    "uuid": {"uuid"},
    "jsonb": {"jsonb"},
}


def json_safe(value):
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise invalid()


def _record_value(envelope, actual_type, returned, source):
    if "$ref" in envelope:
        ref, column = envelope["$ref"].split(".")
        value = returned[ref][column]
    elif "$source" in envelope:
        if (
            envelope["$source"] != "content"
            or actual_type not in POSTGRES_TYPES["text"]
        ):
            raise invalid()
        value = source["content"]
    else:
        if actual_type not in POSTGRES_TYPES[envelope["type"]]:
            raise invalid()
        value = decode_value(envelope)
    if actual_type == "jsonb" and value is not None:
        value = Jsonb(value)
    return value


def write_record(cur, record, returned, source):
    cur.execute(
        "SELECT a.attname, t.typname, a.attgenerated, a.attidentity "
        "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_type t ON t.oid=a.atttypid "
        "WHERE n.nspname='data' AND c.relname=%s AND c.relkind IN ('r','p') "
        "AND a.attnum>0 AND NOT a.attisdropped",
        (record["table"],),
    )
    columns = {row[0]: row[1:] for row in cur.fetchall()}
    if (
        not columns
        or not set(record["values"]) | set(record["returning"]) <= columns.keys()
    ):
        raise invalid()
    values = []
    for name, envelope in record["values"].items():
        actual_type, generated, identity = columns[name]
        if generated or identity == "a":
            raise invalid()
        values.append(_record_value(envelope, actual_type, returned, source))
    names = list(record["values"])
    query = sql.SQL("INSERT INTO {} ").format(sql.Identifier("data", record["table"]))
    if names:
        query += sql.SQL("({}) VALUES ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, names)),
            sql.SQL(", ").join(sql.Placeholder() for _ in names),
        )
    else:
        query += sql.SQL("DEFAULT VALUES")
    if "on_conflict" in record:
        conflict = record["on_conflict"]
        query += sql.SQL(" ON CONFLICT ({}) DO UPDATE SET {}").format(
            sql.SQL(", ").join(map(sql.Identifier, conflict["columns"])),
            sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(
                    sql.Identifier(name),
                    sql.Identifier(name),
                )
                for name in conflict["update"]
            ),
        )
    query += sql.SQL(" RETURNING {}").format(
        sql.SQL(", ").join(map(sql.Identifier, record["returning"]))
    )
    cur.execute(query, values)
    row = cur.fetchone()
    if row is None:
        raise invalid()
    return dict(zip(record["returning"], row, strict=True))
