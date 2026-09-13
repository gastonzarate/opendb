"""Structured ingestion on an authenticated owner connection."""

from .contract import example_operation
from .contract import operation_schema
from .errors import IngestionError

__all__ = ["IngestionError", "apply", "example_operation", "operation_schema"]


def apply(conn, operation):
    # Keep Django app initialization independent of peer module availability.
    from .service import apply as apply_operation  # noqa: PLC0415

    return apply_operation(conn, operation)
