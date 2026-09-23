"""Strict JSON contract and deterministic, lossless value conversion."""

import datetime as dt
import hashlib
import json
import math
import re
from decimal import Decimal
from decimal import InvalidOperation
from uuid import UUID

from .errors import IngestionError
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


def identifier(value, *, path="identifier"):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        msg = "expected an unqualified identifier matching [A-Za-z_][A-Za-z0-9_]{0,62}"
        raise invalid(
            msg,
            path=path,
        )
    return value


def _field(path, key):
    # Paths contain bounded field names, never submitted values or arbitrary text.
    name = (
        key if isinstance(key, str) and IDENTIFIER.fullmatch(key) else "<invalid key>"
    )
    return f"{path}.{name}"


def _keys(value, required, optional=(), *, path="value"):
    expected = ", ".join(sorted(required)) or "no required keys"
    if not isinstance(value, dict):
        msg = f"expected an object with keys: {expected}"
        raise invalid(msg, path=path)
    missing = set(required) - value.keys()
    if missing:
        msg = f"missing required keys: {', '.join(sorted(missing))}"
        raise invalid(msg, path=path)
    unknown = value.keys() - set(required) - set(optional)
    if unknown:
        names = sorted({_field("", key).removeprefix(".") for key in unknown})
        allowed = ", ".join(sorted(set(required) | set(optional)))
        msg = f"unknown keys: {', '.join(names[:10])}; expected keys: {allowed}"
        raise invalid(
            msg,
            path=path,
        )


def _text(value, maximum, *, empty=False, path="value"):
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum:
        msg = f"expected a string of at most {maximum} characters without NUL"
        raise invalid(msg, path=path)
    if not empty and not value.strip():
        msg = "expected a nonblank string"
        raise invalid(msg, path=path)


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


def decode_value(value, *, path="value"):
    _keys(value, {"type", "value"}, path=path)
    kind, raw = value["type"], value["value"]
    if not isinstance(kind, str) or kind not in TYPES:
        msg = f"expected one of: {', '.join(sorted(TYPES))}"
        raise invalid(msg, path=f"{path}.type")
    if raw is None:
        return None
    value_path = f"{path}.value"
    if kind == "text":
        _text(raw, 2_000_000, empty=True, path=value_path)
        return raw
    if kind == "jsonb":
        try:
            _json(raw)
        except IngestionError:
            msg = "expected finite JSON without NUL"
            raise invalid(msg, path=value_path) from None
        return raw
    if kind in {"integer", "boolean"}:
        if type(raw) is not {"integer": int, "boolean": bool}[kind]:
            msg = f"expected a JSON {kind}"
            raise invalid(msg, path=value_path)
        return raw
    try:
        if kind in {"numeric", "float"}:
            return _decode_number(kind, raw)
        return _decode_temporal(kind, raw)
    except ValueError, InvalidOperation, OverflowError:
        expectations = {
            "numeric": "expected numeric as a finite decimal string",
            "float": "expected a finite JSON number",
            "date": "expected a valid ISO date (YYYY-MM-DD)",
            "time": "expected a valid ISO time without timezone",
            "timestamp": "expected an ISO timestamp with T and without timezone",
            "timestamptz": "expected an ISO timestamp with T and timezone",
            "uuid": "expected a UUID string",
        }
        raise invalid(expectations[kind], path=value_path) from None


def _identifiers(value, *, empty=False, path="value"):
    if not isinstance(value, list) or (not empty and not value):
        msg = "expected a nonempty list of identifiers"
        raise invalid(msg, path=path)
    for index, name in enumerate(value):
        identifier(name, path=f"{path}[{index}]")
    if len(set(value)) != len(value):
        msg = "identifiers must be distinct"
        raise invalid(msg, path=path)


def _record_value(value, previous, *, path):
    if isinstance(value, dict) and "$ref" in value:
        _keys(value, {"$ref"}, path=path)
        if not isinstance(value["$ref"], str):
            msg = "expected ref.column naming a column returned by an earlier record"
            raise invalid(
                msg,
                path=path,
            )
        reference, separator, returned_column = value["$ref"].partition(".")
        if not separator or returned_column not in previous.get(reference, []):
            msg = "expected ref.column naming a column returned by an earlier record"
            raise invalid(
                msg,
                path=path,
            )
    elif isinstance(value, dict) and "$source" in value:
        _keys(value, {"$source"}, path=path)
        if value["$source"] != "content":
            msg = 'expected {"$source": "content"}'
            raise invalid(msg, path=path)
    else:
        decode_value(value, path=path)


def _record(record, previous, *, path):
    _keys(record, {"ref", "table", "values", "returning"}, {"on_conflict"}, path=path)
    identifier(record["ref"], path=f"{path}.ref")
    identifier(record["table"], path=f"{path}.table")
    if record["ref"] in previous:
        msg = "record ref must be unique"
        raise invalid(msg, path=f"{path}.ref")
    if not isinstance(record["values"], dict):
        msg = "expected an object mapping columns to typed values or references"
        raise invalid(
            msg,
            path=f"{path}.values",
        )
    _identifiers(record["returning"], path=f"{path}.returning")
    for column, value in record["values"].items():
        value_path = _field(f"{path}.values", column)
        identifier(column, path=value_path)
        _record_value(value, previous, path=value_path)
    if "on_conflict" in record:
        conflict = record["on_conflict"]
        _keys(conflict, {"columns", "update"}, path=f"{path}.on_conflict")
        for key, names in conflict.items():
            conflict_path = f"{path}.on_conflict.{key}"
            _identifiers(names, path=conflict_path)
            if not set(names) <= record["values"].keys():
                msg = "columns must also appear in this record's values"
                raise invalid(
                    msg,
                    path=conflict_path,
                )
    previous[record["ref"]] = record["returning"]


def _source(source):
    _keys(source, {"content", "media_type"}, {"name", "uri"}, path="source")
    _text(source["content"], 2_000_000, path="source.content")
    _text(source["media_type"], 200, path="source.media_type")
    for key in source.keys() - {"content", "media_type"}:
        _text(source[key], 2_000, path=f"source.{key}")


def _annotation(annotation, *, path):
    _keys(annotation, {"table", "description", "metadata"}, {"column"}, path=path)
    identifier(annotation["table"], path=f"{path}.table")
    if "column" in annotation:
        identifier(annotation["column"], path=f"{path}.column")
    _text(annotation["description"], 10_000, empty=True, path=f"{path}.description")
    limits = {"purpose": 10_000, "units": 10_000, "conventions": 10_000}
    if "column" not in annotation:
        limits.update(display_name=200, attributes_summary=2000)
    _keys(annotation["metadata"], set(), set(limits), path=f"{path}.metadata")
    for key, value in annotation["metadata"].items():
        _text(value, limits[key], empty=True, path=f"{path}.metadata.{key}")


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
        path="operation",
    )
    if type(operation["version"]) is not int or operation["version"] != 1:
        msg = "expected integer 1"
        raise invalid(msg, path="version")
    _text(operation["idempotency_key"], 200, path="idempotency_key")
    fingerprint = operation["expected_schema_fingerprint"]
    if not isinstance(fingerprint, str) or not re.fullmatch(
        r"[0-9a-f]{64}", fingerprint
    ):
        msg = "expected 64 lowercase hexadecimal characters from catalog"
        raise invalid(
            msg,
            path="expected_schema_fingerprint",
        )
    _source(operation["source"])
    for key in ("statements", "records", "annotations"):
        if not isinstance(operation[key], list) or len(operation[key]) > MAX_ITEMS:
            msg = f"expected an array with at most {MAX_ITEMS} items"
            raise invalid(msg, path=key)
    for index, statement in enumerate(operation["statements"]):
        _text(statement, 100_000, path=f"statements[{index}]")
    previous = {}
    for index, record in enumerate(operation["records"]):
        _record(record, previous, path=f"records[{index}]")
    for index, annotation in enumerate(operation["annotations"]):
        _annotation(annotation, path=f"annotations[{index}]")
    if len(_json(operation).encode()) > MAX_PAYLOAD_BYTES:
        msg = f"canonical JSON must be at most {MAX_PAYLOAD_BYTES} UTF-8 bytes"
        raise invalid(msg)
