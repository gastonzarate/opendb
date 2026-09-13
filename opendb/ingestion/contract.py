"""Machine-readable resources for MCP clients and acceptance examples."""

import json
from pathlib import Path

from .errors import invalid


def operation_schema():
    """Return a fresh JSON Schema document for the public operation contract."""
    return json.loads(Path(__file__).with_name("operation.schema.json").read_text())


def example_operation(kind, fingerprint):
    """Build a complete meeting/expenses operation for an empty data schema."""
    if kind not in ("meeting", "expenses"):
        raise invalid()
    path = Path(__file__).parent / "examples" / (kind + ".json")
    operation = json.loads(path.read_text())
    operation["expected_schema_fingerprint"] = fingerprint
    return operation
