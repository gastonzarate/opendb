"""Deterministic validation; no database or extraction model required."""

import copy
from decimal import Decimal

import pytest

from opendb.ingestion import IngestionError
from opendb.ingestion.validation import decode_value
from opendb.ingestion.validation import payload_hash
from opendb.ingestion.validation import validate_operation


def operation():
    return {
        "version": 1,
        "idempotency_key": "receipt-1",
        "expected_schema_fingerprint": "0" * 64,
        "source": {"content": "Coffee: 12.50", "media_type": "text/plain"},
        "statements": [],
        "records": [
            {
                "ref": "expense",
                "table": "expenses",
                "values": {"amount": {"type": "numeric", "value": "12.50"}},
                "returning": ["id"],
            },
        ],
        "annotations": [],
    }


def test_operation_validation_does_not_mutate_payload():
    value = operation()
    original = copy.deepcopy(value)
    validate_operation(value)
    assert value == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True),
        ("version", 2),
        ("idempotency_key", ""),
        ("expected_schema_fingerprint", "stale"),
        ("source", {"content": ""}),
        ("source", {"content": "x", "url": "https://example.test"}),
        ("records", "not a list"),
        ("owner_id", 17),
    ],
)
def test_invalid_top_level_contract(field, value):
    data = operation()
    data[field] = value
    with pytest.raises(IngestionError) as exc:
        validate_operation(data)
    assert exc.value.code == "invalid_operation"


@pytest.mark.parametrize(
    "name", ["data.expenses", "x; DROP TABLE y", "a" * 64, "id\x00"]
)
def test_unsafe_identifier_rejected(name):
    data = operation()
    data["records"][0]["table"] = name
    with pytest.raises(IngestionError):
        validate_operation(data)


def test_only_prior_explicitly_returned_columns_can_be_referenced():
    data = operation()
    data["records"][0]["values"]["person_id"] = {"$ref": "person.id"}
    with pytest.raises(IngestionError):
        validate_operation(data)
    person = {"ref": "person", "table": "people", "values": {}, "returning": ["id"]}
    data["records"].insert(0, person)
    validate_operation(data)
    data["records"][1]["values"]["person_id"] = {"$ref": "person.secret"}
    with pytest.raises(IngestionError):
        validate_operation(data)


def test_upsert_requires_explicit_conflict_and_update_columns():
    data = operation()
    data["records"][0]["on_conflict"] = {"columns": ["amount"], "update": ["missing"]}
    with pytest.raises(IngestionError):
        validate_operation(data)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"type": "numeric", "value": "12.50"}, Decimal("12.50")),
        ({"type": "integer", "value": 3}, 3),
        ({"type": "text", "value": None}, None),
        ({"type": "boolean", "value": False}, False),
    ],
)
def test_typed_values_preserve_unknowns_and_exact_numbers(value, expected):
    assert decode_value(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        {"type": "integer", "value": True},
        {"type": "integer", "value": "3"},
        {"type": "numeric", "value": 0.1},
        {"type": "numeric", "value": "NaN"},
        {"type": "date", "value": "2026-02-30"},
        {"type": "timestamptz", "value": "2026-09-13T10:00:00"},
        {"type": "boolean", "value": "false"},
        {"type": "text", "value": "secret\x00"},
        {"type": "text); DROP TABLE data.expenses; --", "value": "x"},
        {"type": "jsonb", "value": {"number": float("inf")}},
    ],
)
def test_invalid_typed_values_rejected_without_echoing_values(value):
    with pytest.raises(IngestionError) as exc:
        decode_value(value)
    assert "secret" not in str(exc.value)


def test_payload_hash_is_order_independent_but_binds_every_input():
    data = operation()
    assert payload_hash(data) == payload_hash(dict(reversed(list(data.items()))))
    changed = copy.deepcopy(data)
    changed["source"]["content"] += "!"
    assert payload_hash(data) != payload_hash(changed)


@pytest.mark.parametrize("kind", ["meeting", "expenses"])
def test_exported_examples_are_valid_independent_operations(kind):
    from opendb.ingestion import example_operation

    example = example_operation(kind, "a" * 64)
    validate_operation(example)
    assert example["expected_schema_fingerprint"] == "a" * 64
    example["records"].clear()
    assert example_operation(kind, "a" * 64)["records"]


def test_literal_json_escape_in_original_source_is_preserved():
    data = operation()
    data["source"]["content"] = r"The literal sequence \u0000 is text."
    validate_operation(data)
    assert decode_value({"type": "jsonb", "value": {"text": r"\u0000"}}) == {
        "text": r"\u0000"
    }


def test_exported_json_schema_accepts_examples_and_rejects_untyped_records():
    import jsonschema

    from opendb.ingestion import example_operation
    from opendb.ingestion import operation_schema

    schema = operation_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    for kind in ("meeting", "expenses"):
        validator.validate(example_operation(kind, "a" * 64))
    bad = example_operation("expenses", "a" * 64)
    bad["records"][0]["values"]["amount"] = "12.50"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(bad)
