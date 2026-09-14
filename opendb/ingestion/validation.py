"""Strict JSON contract and deterministic, lossless value conversion."""

import datetime as dt
import hashlib
import json
import math
import re
from decimal import Decimal
from decimal import InvalidOperation
from uuid import UUID

from .errors import invalid

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
TYPES = {
    "text",
    "integer",
    "numeric",
    "float",
    "boolean",
    "date",
    "time",
    "timestamp",
    "timestamptz",
    "uuid",
    "jsonb",
}
MAX_ITEMS = 1_000
MAX_PAYLOAD_BYTES = 4_000_000


def _json_tree(value):
    if isinstance(value, str):
        if "\x00" in value:
            raise invalid()
    elif isinstance(value, list):
        for item in value:
            _json_tree(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise invalid()
            _json_tree(key)
            _json_tree(item)
    elif value is not None and type(value) not in (int, float, bool):
        raise invalid()


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise invalid()
    return value


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys():
        raise invalid()
    if value.keys() - set(required) - set(optional):
        raise invalid()


def _text(value, maximum, *, empty=False):
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum:
        raise invalid()
    if not empty and not value.strip():
        raise invalid()


def _json(value):
    try:
        _json_tree(value)
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except TypeError, ValueError, RecursionError:
        raise invalid() from None
    return encoded


def payload_hash(operation):
    return hashlib.sha256(_json(operation).encode()).hexdigest()


def _decode_number(kind, raw):
    if kind == "numeric" and isinstance(raw, str):
        result = Decimal(raw)
        if result.is_finite():
            return result
    if kind == "float" and type(raw) in (int, float) and math.isfinite(raw):
        return float(raw)
    raise invalid()


def _decode_temporal(kind, raw):
    if not isinstance(raw, str):
        raise invalid()
    if kind == "uuid":
        return UUID(raw)
    if kind == "date" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return dt.date.fromisoformat(raw)
    if kind == "time":
        result = dt.time.fromisoformat(raw)
        if result.tzinfo is None:
            return result
    if kind in {"timestamp", "timestamptz"} and "T" in raw:
        result = dt.datetime.fromisoformat(raw)
        if (result.tzinfo is not None) == (kind == "timestamptz"):
            return result
    raise invalid()


def decode_value(value):
    _keys(value, {"type", "value"})
    kind, raw = value["type"], value["value"]
    if not isinstance(kind, str) or kind not in TYPES:
        raise invalid()
    if raw is None:
        return None
    if kind == "text":
        _text(raw, 2_000_000, empty=True)
        return raw
    if kind == "jsonb":
        _json(raw)
        return raw
    if kind in {"integer", "boolean"}:
        if type(raw) is not {"integer": int, "boolean": bool}[kind]:
            raise invalid()
        return raw
    try:
        if kind in {"numeric", "float"}:
            return _decode_number(kind, raw)
        return _decode_temporal(kind, raw)
    except ValueError, InvalidOperation, OverflowError:
        raise invalid() from None


def _identifiers(value, *, empty=False):
    if not isinstance(value, list) or (not empty and not value):
        raise invalid()
    for name in value:
        identifier(name)
    if len(set(value)) != len(value):
        raise invalid()


def _record(record, previous):
    _keys(record, {"ref", "table", "values", "returning"}, {"on_conflict"})
    identifier(record["ref"])
    identifier(record["table"])
    if record["ref"] in previous or not isinstance(record["values"], dict):
        raise invalid()
    _identifiers(record["returning"])
    for column, value in record["values"].items():
        identifier(column)
        if isinstance(value, dict) and "$ref" in value:
            _keys(value, {"$ref"})
            if not isinstance(value["$ref"], str):
                raise invalid()
            reference, separator, returned_column = value["$ref"].partition(".")
            if not separator or returned_column not in previous.get(reference, []):
                raise invalid()
        else:
            decode_value(value)
    if "on_conflict" in record:
        conflict = record["on_conflict"]
        _keys(conflict, {"columns", "update"})
        for names in conflict.values():
            _identifiers(names)
            if not set(names) <= record["values"].keys():
                raise invalid()
    previous[record["ref"]] = record["returning"]


def _source(source):
    _keys(source, {"content", "media_type"}, {"name", "uri"})
    _text(source["content"], 2_000_000)
    _text(source["media_type"], 200)
    for key in source.keys() - {"content", "media_type"}:
        _text(source[key], 2_000)


def _annotation(annotation):
    _keys(annotation, {"table", "description", "metadata"}, {"column"})
    identifier(annotation["table"])
    if "column" in annotation:
        identifier(annotation["column"])
    _text(annotation["description"], 10_000, empty=True)
    limits = {"purpose": 10_000, "units": 10_000, "conventions": 10_000}
    if "column" not in annotation:
        limits.update(display_name=200, attributes_summary=2000)
    _keys(annotation["metadata"], set(), set(limits))
    for key, value in annotation["metadata"].items():
        _text(value, limits[key], empty=True)


def validate_operation(operation):
    _keys(
        operation,
        {
            "version",
            "idempotency_key",
            "expected_schema_fingerprint",
            "source",
            "statements",
            "records",
            "annotations",
        },
    )
    if type(operation["version"]) is not int or operation["version"] != 1:
        raise invalid()
    _text(operation["idempotency_key"], 200)
    fingerprint = operation["expected_schema_fingerprint"]
    if not isinstance(fingerprint, str) or not re.fullmatch(
        r"[0-9a-f]{64}", fingerprint
    ):
        raise invalid()
    _source(operation["source"])
    for key in ("statements", "records", "annotations"):
        if not isinstance(operation[key], list) or len(operation[key]) > MAX_ITEMS:
            raise invalid()
    for statement in operation["statements"]:
        _text(statement, 100_000)
    previous = {}
    for record in operation["records"]:
        _record(record, previous)
    for annotation in operation["annotations"]:
        _annotation(annotation)
    if len(_json(operation).encode()) > MAX_PAYLOAD_BYTES:
        raise invalid()
