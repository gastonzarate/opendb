# Catalog and structured ingestion

Gateway/parent integration handoff: the exported contract is ready.

```python
from opendb.ingestion import operation_schema, example_operation
from opendb.catalog import describe

# Publish the returned JSON as an MCP resource / operation input documentation.
schema = operation_schema()
# Complete operations for fresh databases; use the owner's live catalog fingerprint.
meeting = example_operation("meeting", describe(owner_conn)["fingerprint"])
expenses = example_operation("expenses", describe(owner_conn)["fingerprint"])
# End-to-end: dispatch(actor_id, "ingest", {"database_id": database_id,
#                                        "operation": meeting})
```

Files: `opendb/ingestion/operation.schema.json`,
`opendb/ingestion/examples/meeting.json`, `opendb/ingestion/examples/expenses.json`.
The JSON files use 64 zeros as the fingerprint placeholder; the helper replaces it.
Examples are validated and exercised against the real restricted owner connection in
`tests/integration/test_ingestion.py::test_exported_examples_execute_and_replay`.

MCP instructions: inspect the owner's catalog and existing records first. Reuse tables,
units and verified identities. A speaker label alone does not identify a person; the
meeting example keeps both participants' person_id NULL. Missing dates, timezones,
times and identities stay NULL; never invent them. Preserve the original source.
Use an explicit UNIQUE identity for upsert, never fuzzy-name merging. Retry an exact
operation with its original key and fingerprint; on schema conflict, rediscover and
rebuild. The server performs no extraction or semantic identity matching.

Completed implementation plan (shared workdir; no commits):

- [x] Define and test strict operation validation and typed values.
- [x] Install protected metadata functions and inspect real, authorized relations.
- [x] Test and implement transactional DDL, records, provenance and idempotency.
- [x] Test privacy, rollback, concurrent drift and deduplication on PostgreSQL.
- [x] Document the final JSON contract and meeting/expenses examples.

Interfaces: `opendb.catalog.install(conn, owner_role)`,
`opendb.catalog.describe(conn)` and `opendb.ingestion.apply(conn, operation)`.
The administrator installs the catalog after provisioning `data`. Ingestion uses
the restricted owner connection and the parent's `validate_sql` policy.

## Public contract

`install` runs in an administrative transaction. It verifies that `data` belongs to
the supplied restricted owner role and that an existing `opendb_catalog` belongs to
the connected provisioner. It can be repeated without deleting metadata. It preserves
schema USAGE grants installed by peer read helpers, but revokes PUBLIC CREATE and
direct owner access to catalog tables. It never changes vector-owned objects.

`describe` returns `{schema: "data", fingerprint: "<64 hex>", objects: [...]}`. Each
object has `schema`, `name`, `kind`, `row_security`, `columns`, `primary_key`,
`unique_constraints`, `foreign_keys`, `description`, and `metadata`. Columns contain
`name`, `type`, `nullable`, `identity`, `generated`, `has_default`, `description`, and
`metadata`; identity/generated flags use PostgreSQL's catalog values. FK references
contain `schema`, `table` and ordered `columns`; update/delete actions use PostgreSQL's
`a/r/c/n/d` codes (no action/restrict/cascade/set null/set default).

Discovery queries PostgreSQL, filtering objects by both schema USAGE and
`has_table_privilege(..., 'SELECT')`. A FK is omitted entirely if its target is not
visible; definitions and default/check expressions are never returned. Only the
actual data owner gets private schema revisions incorporated into its fingerprint.
Guest fingerprints ignore private objects and private FK targets. Changes to rows,
statistics and annotations do not change the fingerprint. Discovery normalizes its
search path and restores the caller's transaction settings.

Annotations are **owner-only in v1**, including annotations attached to a shared view.
Guests receive empty descriptions/metadata and do not need permission on any catalog
function. This prevents an owner-written description from accidentally disclosing a
view's private sources. Do not grant guests `read_annotations()`; a future shared
annotation feature requires a separate sanitized read interface and opt-in content.

`apply` requires the actual restricted data-schema owner, an idle psycopg3 connection,
and the complete operation below. It rejects an existing transaction rather than
returning a successful result that an outer transaction could still roll back. It
works with autocommit on or off as long as the connection is idle. The dispatch layer
must authenticate and authorize the owner before opening this connection; there is
no owner/actor field in the operation.

| Field | Contract |
| --- | --- |
| `version` | Integer `1`; booleans are rejected. |
| `idempotency_key` | Nonblank string, maximum 200 characters, scoped to the personal database. |
| `expected_schema_fingerprint` | Exact fingerprint from the owner's current catalog. |
| `source` | Required `content` and `media_type`; optional `name`, `uri`. URI is metadata, never fetched. Original text is stored unchanged. |
| `statements` | Array of single transactional DDL statements, executed in order. Relations must be explicitly `data`-qualified. |
| `records` | Ordered array of `{ref, table, values, returning, on_conflict?}`. Each ref is unique. |
| `annotations` | Array of `{table, column?, description, metadata}`; metadata permits only string `purpose`, `units`, `conventions`. |

Every top-level field is required; unknown fields are rejected throughout the
contract. Each array permits at most 1,000 entries. Source text permits at most
2,000,000 characters; each statement at most 100,000; the canonical JSON payload
at most 4,000,000 bytes. NUL characters and non-finite JSON numbers are rejected.
Literal source text such as `\u0000` is preserved when it represents ordinary
characters rather than an actual NUL.

Schema statements are restricted to CREATE TABLE/VIEW/INDEX, ALTER TABLE, permitted
renames, and DROP TABLE/VIEW/INDEX, further limited by the parent's `validate_sql`.
Transactions, SELECT, arbitrary DML, functions, concurrent index operations and other
nontransactional commands cannot be smuggled into `statements`.

`table`, column names and `ref` use `[A-Za-z_][A-Za-z0-9_]{0,62}`. Table names here are
bare names, always resolved in `data`; DDL uses explicitly qualified names. Runtime
identifiers are composed with `psycopg.sql.Identifier`, and values use parameters.
Only real tables/partitioned tables accept record writes; writing through a view is
outside this contract. Omit a column to use its database default. Empty `values` means
DEFAULT VALUES. Generated columns and ALWAYS identity columns cannot be supplied.

Each value is either `{type, value}` or `{"$ref": "earlier_ref.returned_column"}`.
References must point to an earlier record's explicit `returning` list. Results are
resolved with native PostgreSQL values before they are converted for the JSON response.
At least one returned column is required so provenance can retain record identifiers;
clients should always return the primary key.

| Type tag | JSON representation and PostgreSQL target |
| --- | --- |
| `text` | String; text/varchar/char. |
| `integer` | Integer, excluding booleans; smallint/integer/bigint. |
| `numeric` | Finite decimal **string**, e.g. `"12.50"`; numeric. Avoid float rounding. |
| `float` | Finite JSON number; real/double precision. |
| `boolean` | JSON true/false; boolean. |
| `date` | Valid `YYYY-MM-DD`; date. |
| `time` | ISO local time without timezone; time. |
| `timestamp` | ISO local timestamp with `T`, without timezone. |
| `timestamptz` | ISO timestamp with `T` and explicit offset or `Z`. |
| `uuid` | UUID string. |
| `jsonb` | JSON value; converted through psycopg Jsonb. |

Every type accepts `value: null` as SQL NULL, subject to the real column's NOT NULL
constraint. JSONB `value: null` also means SQL NULL; a separate JSON null literal is
not supported in v1. PostgreSQL enforces numeric precision, text lengths, foreign
keys, checks and UNIQUE constraints. Supplied type tags must match the actual column
type; custom types/domains/arrays are not supported by structured values.

To explicitly upsert, add e.g.
`"on_conflict": {"columns": ["receipt"], "update": ["amount"]}`. Both lists must
be nonempty and contain supplied value columns. PostgreSQL must have a matching
UNIQUE/primary-key constraint or unique index. Only listed update columns are replaced.
This is a caller-declared identity policy; it never merges similar names automatically.
NULL conflict keys follow ordinary PostgreSQL uniqueness semantics, so use a stable
non-null identity where deduplication is intended.

## Atomicity, concurrency and errors

The transaction writes source/idempotency state, checks the schema, executes DDL,
writes related records, stores annotations, snapshots indexing state, and saves the
result. The catalog functions `begin_ingestion`, `annotate`, `finish_ingestion` and
`read_annotations` are SECURITY DEFINER with fixed
`search_path = pg_catalog, opendb_catalog, pg_temp`; EXECUTE is revoked from PUBLIC and
granted only to the owner. Their SQL never executes caller-provided DDL or arbitrary
internal table names. These protections follow PostgreSQL's
[SECURITY DEFINER guidance](https://www.postgresql.org/docs/17/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY).

A SHA-256 hash binds all operation fields, including the expected fingerprint and
source. Object-key order is canonicalized; record/statement order and string content
are significant. Exact retries return the original result **before** checking its now
historical fingerprint. A changed payload with a used key gives `idempotency_conflict`.
A failed transaction retains neither its source nor its key; a corrected operation
may reuse that failed key. Schema, rows and annotations roll back together. PostgreSQL
sequence allocations can leave gaps after a rollback; no gapless ID promise is made.

All ingestions acquire database-local advisory transaction lock `6847392015641`
(exported as `opendb.catalog.install.SCHEMA_LOCK_KEY` in the install module), then lock
existing data tables in ACCESS EXCLUSIVE mode in name order before fingerprinting.
The SQL service should take this same advisory lock before schema changes. Concurrent
ingestions serialize: equal operations replay, while competing schema changes with
an old fingerprint receive an explicit conflict. The five-second lock timeout avoids
waiting indefinitely for another writer. Locking all existing tables is deliberately
conservative and blocks concurrent reads/writes while ingestion runs.

**Concurrency limit:** an administrative or direct SQL writer that ignores the shared
advisory lock can create a new, unrelated relation or change an unlocked view during
ingestion. Existing table locks cannot lock a PostgreSQL namespace against CREATE.
Such writers must participate in the advisory-lock convention for the full guarantee.
Physical relation recreation and grant/DDL changes may invalidate an owner's fingerprint
even if its visible column shape is unchanged; rediscover instead of guessing a hash.

`IngestionError` and `CatalogError` are ValueError subclasses with a stable `.code` and
a safe message. Codes include `invalid_operation`, `invalid_statement`,
`permission_denied`, `transaction_active`, `schema_conflict`, `idempotency_conflict`,
`constraint_violation`, `database_error`, `catalog_install_failed`, `catalog_unavailable`.
Messages exclude SQL, credentials, private identifiers and record/source values.
If the connection fails around COMMIT, the outcome may be unknown: retry the exact
operation/key to recover its stored result; do not assume a transport error means
that PostgreSQL did not commit.

The result contains `operation_id`, `records` (ref to returned-column mappings),
`record_tables`, `schema_fingerprint`, `changes` (counts), and `indexing`. Decimals and
UUIDs become strings and temporal values become ISO strings. `record_tables` and
returned IDs link the protected original source to derived records. These mappings
are historical; direct later DDL or deletion does not rewrite ingestion history.
The parent dispatcher can provide owner-only `ingestion_history` through a trusted
connection to `opendb_catalog.ingestion_operations`; restricted generic SQL cannot read
that table. Annotations use relation OID/attribute number, so renaming an object keeps
its annotation and replacing an object does not inherit an old object's description.

`indexing` is a **commit-time snapshot**, with status `not_requested`, `pending`,
`ready` or `failed` and relevant registered index summaries. Existing vector change
capture runs in the same transaction as records. No embedding/model call runs in
ingestion; unregistered tables remain `not_requested`. A retry returns the historical
snapshot; use the separate vector status action for current worker progress. Mixed
indexes report pending before failed before ready; individual counts remain available.

## Working examples

The complete [meeting operation](../../opendb/ingestion/examples/meeting.json) preserves:

```text
Unknown speaker: Hello.
[00:03] Alice: Welcome.
```

It creates meetings, people, participants and turns only as an example schema for a
fresh database. The date, timezone, first start time and both end times are NULL. Both
person references remain NULL because neither label proves a verified identity. Alice
is retained as the second speaker's label. The second start time is exactly 3.000
seconds. Turn order is unique within each meeting, and a composite FK ensures each
participant belongs to the same meeting as the turn. Existing verified people should
be queried and reused when adapting the example to a real database.

The [expenses operation](../../opendb/ingestion/examples/expenses.json) preserves
`Receipt r1: Coffee USD 12.50. Date not supplied.` It stores the exact numeric amount,
explicit currency and NULL date; receipt r1 is a unique source identity. It does not
infer exchange rates, payment method or a date. To correct this expense, discover
the current fingerprint, use a new operation key/source, omit CREATE TABLE, and add
the explicit upsert clause described above.

```python
from opendb.catalog import describe
from opendb.ingestion import apply, example_operation

operation = example_operation("expenses", describe(owner_conn)["fingerprint"])
result = apply(owner_conn, operation)
assert result["records"]["expense"]["amount"] == "12.50"
assert apply(owner_conn, operation) == result  # exact replay, no duplicate record
```

These examples perform no extraction inside OpenDB and should not be blindly applied
to populated databases. An external assistant must inspect existing entities, tables,
constraints and units, then prepare the minimal operation consistent with that data.

## Verification and dependencies

```bash
/tmp/opendb-mvp-venv/bin/pytest --ds=config.settings.integration -p no:cacheprovider tests/test_ingestion_validation.py tests/integration/test_ingestion.py -q
/tmp/opendb-mvp-venv/bin/ruff check --no-cache opendb/catalog opendb/ingestion tests/test_ingestion_validation.py tests/integration/test_ingestion.py
```

Production uses the standard library, psycopg3 and the parent's existing pglast SQL
parser/policy. JSON Schema verification in tests uses jsonschema, already present in
the shared MCP environment. No dependencies or shared files are changed by this module.
