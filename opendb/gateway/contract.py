"""Transport allowlist and common input boundary; services own authorization."""

ACTIONS = {
    "ingestion_history": (
        "Inspect original sources and ingestion results as database "
        "owner. Payload: {database_id, operation_id?: UUID}. Without an "
        "operation_id, list recent operation IDs, source names and "
        "results. With an ID, retrieve the original source and result to "
        "audit provenance. Guests cannot access history."
    ),
    "list_databases": "List databases you own or can read. Start here; payload: {}.",
    "create_database": (
        "Provision your private database. Payload: {}. Inspect existing "
        "databases first."
    ),
    "catalog": (
        "Inspect authorized tables, views, columns and modeling "
        "annotations. Payload: {database_id}. Read before changing schema "
        "or ingesting data."
    ),
    "query": (
        "Execute authorized SQL in schema data. Payload: {database_id, "
        "sql, parameters?: []}. Use $1, $2, etc. for parameter values. "
        "Owners may run permitted DDL/DML; guests can only read granted "
        "objects. Results are bounded by the service."
    ),
    "ingest": (
        "Apply one atomic structured ingestion. Payload: {database_id, "
        "operation}. First read opendb://guides/ingestion for JSON Schema "
        "and a working example, then catalog for result.fingerprint. "
        "Operation requires version: 1, idempotency_key, "
        "expected_schema_fingerprint, source, statements, records, "
        "annotations. Preserve unknown values instead of inventing them."
    ),
    "create_role": (
        "Create a database-scoped read role as owner. Payload: "
        "{database_id, name}. Group related sharing permissions into "
        "roles."
    ),
    "grant_object": (
        "Grant a role read access to a data table or view as owner. "
        "Payload: {database_id, role_id, object_name}. Share a view when "
        "only selected columns or rows should be visible."
    ),
    "assign_role": (
        "Assign a read role to a verified user's email as owner. Payload: "
        "{database_id, role_id, email}. Email identifies the invitation "
        "recipient, never the current actor."
    ),
    "revoke_role": (
        "Remove a user's role assignment as owner. Payload: {database_id, "
        "role_id, email}. Permissions from other assigned roles remain."
    ),
    "revoke_object": (
        "Remove a role's read grant on a data table or view as owner. "
        "Payload: {database_id, role_id, object_name}."
    ),
    "list_access": (
        "Inspect sharing roles, memberships and object grants as owner. "
        "Payload: {database_id}. Check this before changing access."
    ),
    "register_vector": (
        "Register a data table's text column for asynchronous indexing as "
        "owner. Payload: {database_id, table, key_column, text_column}. "
        "Rows remain stored if embeddings are temporarily unavailable."
    ),
    "search_vectors": (
        "Search current indexed content permitted for your identity. "
        "Payload: {database_id, index_id, query, limit?: 10, "
        "target_view?: name, filters?: {column: scalar}}. Filters use equality. "
        "For view-only sharing, create a filtered view selecting all canonical "
        "columns from register_vector's generated vector view, grant it, and "
        "pass its name as target_view. No base-table grant is required. Check "
        "vector_status for pending or failed indexing; missing results do "
        "not prove source data is absent."
    ),
    "vector_status": (
        "Inspect visible vector indexing state and failures. Payload: "
        "{database_id}. Report pending or failed embeddings honestly; do "
        "not imply relational ingestion failed."
    ),
}

INSTRUCTIONS = """You are the user's personal DBA.
Start with list_databases and catalog;
reuse existing entities and conventions before adding data tables or views. Use real
PostgreSQL constraints and relationships, parameterized SQL, and schema data only.
Model the user's domain, not a fixed template. Before ingest read the resource
opendb://guides/ingestion for JSON Schema and a complete related-record example.
Prefer ingest for transactional schema, records, relationships and provenance;
set expected_schema_fingerprint to catalog's result.fingerprint, version to 1, and
choose idempotency_key. Reuse a key only with exactly the same operation content.
Inspect existing records before merging identities;
similar names alone are insufficient.
Preserve original sources and links to derived records. Keep missing dates, identities
and times unknown. For a meeting, retain participants and each intervention's speaker,
sequence, original text and any source timestamps. Explain schema conflicts and retry
after re-reading the catalog. Owners may change or delete their data without extra
gateway confirmations; guests are read-only. Tool visibility never grants permission.
Share the smallest useful table or view via roles; role grants accumulate. OpenDB
generates embeddings asynchronously. Report pending or failed indexing separately from
successful relational writes; never present stale embeddings as current. Every tool
takes a payload object ({} when empty) and returns {result: ...}. Never supply
actor_id, user_id or owner_id. SQL parameters use $1, $2, etc., never string joining.
"""


def validate_payload(payload):
    if not isinstance(payload, dict):
        msg = "Payload must be a JSON object."
        raise ValueError(msg)  # noqa: TRY004 -- Public JSON validation contract.
    if {"actor_id", "user_id", "owner_id"}.intersection(payload):
        msg = "Actor identity is supplied by authentication, never payload."
        raise ValueError(msg)


def dispatch(actor_id, action, payload):
    """Import peer services only when called, after Django application setup."""
    from opendb.databases.services import dispatch as service_dispatch  # noqa: PLC0415

    return service_dispatch(actor_id, action, payload)
