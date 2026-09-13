"""MCP guidance backed by the ingestion module's authoritative public contract."""


def ingestion_guide():
    """Load the canonical schema/example at request time, alongside DBA guidance."""
    # Lazy import keeps gateway startup independent of peer initialization.
    from opendb.ingestion import example_operation  # noqa: PLC0415
    from opendb.ingestion import operation_schema  # noqa: PLC0415

    return {
        "schema": operation_schema(),
        "workflow": [
            (
                "Call catalog with database_id; use result.fingerprint "
                "verbatim as expected_schema_fingerprint."
            ),
            (
                "Inspect existing entities and adapt this example. It is "
                "intended for a fresh database."
            ),
            (
                "Replace the example's all-zero fingerprint with the live "
                "catalog value. Choose a new idempotency_key for new "
                "content."
            ),
            (
                "Call ingest with {database_id: YOUR_DATABASE_UUID, "
                "operation: THE_OPERATION}. Every transport returns "
                "{result: ...}."
            ),
            (
                "Retry the same operation unchanged on transient failure; "
                "changed content needs a new key. On schema conflict "
                "reread catalog."
            ),
        ],
        "semantic_rules": [
            (
                "opendb.ingestion.validation.validate_operation is "
                "authoritative; this schema describes structure, with "
                "additional semantic checks below."
            ),
            (
                "All strings/JSON exclude NUL; JSON numbers must be "
                "finite. Total canonical UTF-8 operation size is at most "
                "4,000,000 bytes."
            ),
            (
                "Identifiers are unqualified: table meetings means "
                "data.meetings. SQL statements must pass the permitted "
                "transactional DDL policy."
            ),
            (
                "Record refs must be unique. A $ref may only reference a "
                "column explicitly returned by an earlier record."
            ),
            (
                "on_conflict.columns and on_conflict.update must be "
                "nonempty distinct identifier lists, and subsets of that "
                "record's values keys."
            ),
            (
                "Dates, times, timestamps and UUIDs are parsed, not "
                "merely matched by regex. numeric must be a finite "
                "Decimal string. null preserves unknown values for every "
                "type."
            ),
            (
                "All seven operation keys are required; statements, "
                "records and annotations may be empty arrays. Unknown "
                "keys are rejected."
            ),
        ],
        "example_operation": example_operation("meeting", "0" * 64),
    }
