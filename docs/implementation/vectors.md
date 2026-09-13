# Local embeddings and pgvector

This module implements the vector contracts in [contracts.md](contracts.md).
Registration and workers use the trusted administrative connection. Search and
status use the caller's restricted PostgreSQL connection.

## Components and implementation plan

Implemented in these independently checked steps:

1. `embeddings.py`: HTTP transport, finite 384-dimensional vectors, exact source
   slices constrained by the model tokenizer, tested with a local HTTP server.
2. `schema.sql`, `schema.py`, `registry.py`: protected catalog, HNSW index,
   validated registration, transactional change capture and shareable SQL views.
3. `indexing.py`, `retrieval.py`, `view_search.py`: leased versioned work, retries, atomic publication,
   restricted search and status, tested against PostgreSQL 17 with pgvector.
4. `worker.py`: CLI over the parent's `PersonalDatabase` and
   `privileged_connection`; optional model checksum verification; live-model smoke.

No Django model/migration, HTTP client dependency, or Python pgvector adapter is
needed. The implementation uses the existing psycopg dependency and the Python
standard library. Vector literals are bound parameters and cast inside PostgreSQL.
Package `opendb/vectors/schema.sql` with the application, as with the peer catalog.

## Model artifact and reproducible deployment

The exact artifact was verified read-only on the Pi on 2026-09-13. The parent copied
it to `.models/granite-97m-r2-q8_0.gguf` and independently confirmed the checksum.

| Property | Pin |
| --- | --- |
| Authoritative artifact source | `gaston@raspberrypi:/home/gaston/granite-97m-r2-q8_0.gguf` (read-only transfer) |
| Filename | `granite-97m-r2-q8_0.gguf` |
| Size | 115061088 bytes |
| SHA-256 | `d0aefc589e25df26b75d45eaa3b7205cad850ceeaaef82c6b98b04b166b992d4` |
| Quantization / dimensions | Q8_0 / 384 |
| Runtime pooling / context | mean / 512 tokens including inserted special tokens |
| License | Apache-2.0, verified in GGUF metadata and the upstream model card |
| llama.cpp CPU container | `ghcr.io/ggml-org/llama.cpp@sha256:cbcdcb52d484e08e23bfc0135afa5beadd2d540513bbb7c65b233231fa033ff4` |

The GGUF's `general.name` and `general.finetune` metadata both contain
`835ad14087e140460703cf0fae09f97d469d65c2`. This is an available revision of the
[IBM Granite upstream model](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/tree/835ad14087e140460703cf0fae09f97d469d65c2).
The [pinned upstream weights download](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/resolve/835ad14087e140460703cf0fae09f97d469d65c2/model.safetensors)
has SHA-256 `f3ea88b230492811046145513710e76b4cc8c2ad49e8708da0e7247e548903be`.
The [upstream model card](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/blob/835ad14087e140460703cf0fae09f97d469d65c2/README.md)
identifies Apache-2.0. Preserve the license and notices when distributing the model.

**No byte-identical public GGUF download was found.** Public Q8_0 conversions have
other checksums, even when the filename/size looks similar. A fresh conversion of
the upstream weights is not claimed to reproduce the Pi artifact. Deployment must
use the verified copy above or an artifact store containing those exact bytes;
the Pi is not a running service dependency. Do not substitute a public quantization
while retaining the old model ID. This provenance limitation is explicit.

Verify the artifact before starting the model or worker:

```sh
printf '%s  %s\n' \
  d0aefc589e25df26b75d45eaa3b7205cad850ceeaaef82c6b98b04b166b992d4 \
  .models/granite-97m-r2-q8_0.gguf | sha256sum --check
```

Run `llama-server` with the verified file and these options:

```sh
llama-server -m /models/granite-97m-r2-q8_0.gguf \
  --embeddings --pooling mean -c 512 -b 512 -ub 512 \
  --parallel 1 --host 0.0.0.0 --port 8080
```

Expose it only to the application/worker. Set `OPENDB_EMBEDDING_URL` in both
processes (default `http://127.0.0.1:8080`; development smoke used
`http://127.0.0.1:18080`). The deployment must pin the running model; HTTP embedding
responses cannot attest a file checksum. `--model-file` checks a mounted artifact,
not the remote process. The stored model identity includes SHA-256, pooling,
context, dimension, and chunking configuration version. Workers and search reject
mismatched identities. Never hot-swap a different model behind the same endpoint.

The [llama-server API documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
describes `/tokenize`, `/detokenize`, and `/v1/embeddings`, all used here.
No approximate character-to-token ratio or silent embedding truncation is used.

## Usage

The parent provisioner runs `catalog.install` followed by
`vectors.install(admin_conn, owner_role)`. Installation is idempotent, checks schema
ownership, and takes the catalog's shared transaction advisory lock. It creates
only `vector_*` objects inside `opendb_catalog`. It requires pgvector in `public`
(the parent already creates that extension).

```python
from opendb import vectors
from opendb.databases.connections import owner_connection, privileged_connection

# Existing personal database owned by owner_id.
with owner_connection(owner_id, database_id) as conn:
    conn.execute("CREATE TABLE data.notes (id bigint PRIMARY KEY, body text)")
    conn.execute(
        "INSERT INTO data.notes VALUES (%s, %s)",
        (1, "A sleepy kitten rests on the sofa."),
    )

# Call only after authenticating/authorizing the owner.
with privileged_connection(database_id) as conn:
    index = vectors.register(conn, "notes", "id", "body")
    # {'index_id': '<uuid>', 'view_name': 'vector_<uuidhex>'}
    result = vectors.process_pending(conn)
    # {'processed': 1, 'failed': 0, 'superseded': 0}

with owner_connection(owner_id, database_id) as conn:
    hits = vectors.search(conn, index["index_id"], "Where is the cat sleeping?")
    state = vectors.status(conn)
```

Registration also accepts `data.notes`. A source must be an ordinary `data` table
with a single-column immediate unique key (including a primary key), NOT NULL,
and a `text`, `varchar`, or `char` text column. Supported keys are PostgreSQL
smallint/integer/bigint, UUID, text/varchar/char. Composite, nullable, conditional,
expression, deferred, custom-type and nonunique keys are rejected. Views,
materialized views, inheritance, partitions, and row-level-security tables are
explicitly deferred. Enabling RLS after registration hides that index and its SQL
view until RLS is disabled. Column/table renaming preserves OID/attribute identity. Search/status and worker
claim/publication revalidate the registered attribute numbers, their supported
types, NOT NULL, and a valid immediate unique index on the key. Dropped attributes
or invalid keys hide the registration. Readding the same column name cannot
resurrect a dropped attribute's index. Source search also compares live source
key/text to the indexed snapshot, including after table rewrites without triggers.

Optional retrieval parameters preserve the original call signature:

```python
vectors.search(conn, index_id, query, limit=10,
               target_view=None, filters={"label": "public"})
```

`filters` is an object of at most 16 scalar equality conditions, combined with
AND **before ranking and LIMIT**. Without `target_view`, keys name real source
columns; with `target_view`, they name columns exposed by that view. Values may
be strings, finite numbers, booleans, or null. Equality uses the column's JSON
scalar representation; null also matches SQL NULL. Unknown columns, arrays,
objects, nonfinite numbers, and free SQL expressions are rejected. Range, OR,
and expression filters are deferred. Identifier quoting and bound values prevent
SQL injection. Metadata-only source edits affect filters immediately without
requiring new embeddings.

Search returns chunk hits with `index_id`, the JSON scalar `source_key`, source
`version`, `chunk_order`, `char_start`, `char_end`, `token_start`, `token_end`, `text`,
and cosine similarity `score` (higher is closer). It returns at most `limit`
chunks, not deduplicated records. Limits are 1–1000, default 10. Empty queries and
queries exceeding 512 tokens including special tokens are rejected. Empty/NULL
source texts become ready with zero chunks.

Chunks do not overlap. Character offsets are Python Unicode code-point positions
into the exact source text, zero-based and end-exclusive; order starts at zero.
Token offsets address the concatenation of independently tokenized chunks,
excluding inserted special tokens. Tokenization may differ at a chunk boundary
from tokenizing the entire source at once. The text and character offsets remain
lossless even when detokenization normalizes text or splits Unicode bytes.

## Worker and failure behavior

```sh
export DJANGO_SETTINGS_MODULE=config.settings.production
export OPENDB_EMBEDDING_URL=http://embeddings:8080
python -m opendb.vectors.worker \
  --model-file /models/granite-97m-r2-q8_0.gguf \
  --limit 10 --poll-interval 5

# One pass over a specific ready personal database:
python -m opendb.vectors.worker --once --database-id '<database UUID>'
```

The CLI loops through `PersonalDatabase.objects.filter(status='ready')`, opens one
privileged connection per database, and writes JSON count summaries. Database
connection failures are isolated and reported without connection strings or source
text. `process_pending` requires an idle connection; it refuses to commit an
existing caller transaction. It also supports idle connections with autocommit off.

INSERT/UPDATE/DELETE triggers, plus a TRUNCATE trigger, update the protected queue
in the source transaction. UPDATE invalidates only when the registered key or source text changes,
using null-safe comparison. No-op and metadata-only UPDATEs preserve ready chunks,
source versions, active leases, and failed retry schedules. Key changes delete the old key's queue/chunks and enqueue the new key.
Rollback restores source and index together. Registration takes a table lock while
installing capture and backfilling, so concurrent inserts cannot fall between them.

A worker claims a row using `FOR UPDATE SKIP LOCKED`, commits a five-minute lease,
then tokenizes/embeds with no open database transaction. Publication locks only the
queue row and compares the source schema validity, globally increasing source
version, and lease UUID. It atomically publishes all chunks and marks the row ready. A concurrent
source edit, delete/reinsert, or replacement worker makes an old success or failure
obsolete. No old worker can overwrite newer chunks. A process crash leaves a lease
that another worker can reclaim after expiry. Very long jobs can duplicate work
if they outlast the lease; publication still requires the current lease identity.

Failures publish no partial chunks and never roll back relational ingestion. They
store only `embedding_failed` and retry after 30, 60, 120… seconds, capped at one
hour, without a terminal retry limit. New source edits reset attempts immediately.
Status exposes per-index counts for pending/processing/ready/failed only for visible
sources. The worker summary counts outcomes of that pass, not the entire queue.
Normal PostgreSQL snapshot semantics apply: a query whose snapshot predates a
concurrent commit can see the previous committed source and its matching index.

## Authorization and SQL access

The catalog tables, version sequence, and trigger function are inaccessible to the
personal role/guests. Search/status use SECURITY DEFINER functions with a fixed trusted search path.
Source search takes an ACCESS SHARE table lock to prevent name replacement between
validation and its read; it is usable in a read-only PostgreSQL transaction. They derive caller identity from PostgreSQL's session or
active role and require schema USAGE and table-wide SELECT on the actual source.
The Python search entrypoint checks permission before inference, and SQL checks
again afterward, including revocations during inference. Direct invocation of the
SQL search function cannot bypass that check. Column-only grants are insufficient.

Schema USAGE on `opendb_catalog` is public after vector installation; it exposes
names, not table contents or mutation privileges. The peer catalog grants EXECUTE
on its sensitive functions only to the owner. Run catalog bootstrap before vector
bootstrap; a later catalog reinstall revokes schema USAGE from PUBLIC, so rerun
vector installation afterward or explicitly grant guests catalog USAGE.

The generated `data.vector_<uuidhex>` security-barrier view contains current
chunks, their 384-dimensional `embedding`, provenance, and `model_id`. Only the
source owner initially receives SELECT WITH GRANT OPTION. It joins live source
content and checks queue versions; it is not writable. The owner can share a
filtered PostgreSQL view without granting access to the source table:

```sql
-- Replace the generated name with register(...)["view_name"].
CREATE VIEW data.approved_vectors WITH (security_barrier=true) AS
SELECT *
FROM data.vector_0123456789abcdef0123456789abcdef
WHERE source_key = '1'::jsonb;
```

Grant SELECT on `data.approved_vectors` through the parent's sharing service. The
guest can run SQL or use semantic search on that approved view:

```python
vectors.search(guest_conn, index_id, "Where is the cat sleeping?",
               target_view="data.approved_vectors", filters={"source_key": 1})
```

`target_view` accepts an unqualified view name or a `data.`-qualified name. It
requires caller SELECT on an actual `data` view and a direct or transitive
PostgreSQL dependency on this index's admin-owned generated vector view. Unrelated
views, source tables, other indexes, and ungranted views cannot authorize access.
The target must expose the canonical columns: `index_id` (uuid), `source_key`
(jsonb), `version` (bigint), `chunk_order`, `char_start`, `char_end`, `token_start`,
`token_end` (integer), `text` (text), `embedding` (vector), and `model_id` (text).
Additional scalar metadata columns can be exposed and filtered. A projection may
redact `text`; retrieval returns that projected text, never the hidden catalog
copy. PostgreSQL grants and view predicates apply before ranking/limiting.

The dependency/authorization predicate is the only privileged operation for view
search. The actual target view query executes as the restricted caller. No
SECURITY DEFINER function executes a caller-supplied view. Grants and source
validity are checked before and after inference, and the SQL SELECT enforces the
view's grants again. The caller needs USAGE on `data`, `public` (pgvector), and
`opendb_catalog`; the parent's sharing/bootstrap already supplies these grants.
A view-only guest is still refused when `target_view` is omitted, and
`vector_status` continues to expose only registrations with source-table SELECT.
Arbitrary source-view registration remains deferred.

**Parent dispatch contract:** forward `target_view=payload.get("target_view")`
and `filters=payload.get("filters")` to `vectors.search` along with the existing
`index_id`, `query`, and `limit`. Both are optional and keyword-only. No registration
payload, connection privilege, dependency, or settings change is required.

The parent SQL policy must continue rejecting trigger disabling/dropping,
SECURITY DEFINER/function creation, role switching, internal-schema writes, and
session replication-role changes. Direct database administrators can bypass these
invariants and are trusted. Dropped source tables disappear from search/status;
their orphaned internal bookkeeping can be removed by an administrator. Changing
registered key constraints or replacing source schemas requires administrative
re-registration; it is not an automatic schema migration mechanism.

## Validation

Lifecycle/filter verification on 2026-09-13: **52 passed** with the real-model test
enabled (12.76 seconds). Ruff check and formatting check cover all ten Python files;
the CLI help command and the deployed model's SHA-256 verification also passed.

Use only the parent's disposable cluster and runner:

```sh
/tmp/opendb-mvp-venv/bin/pytest --ds=config.settings.integration \
  -p no:cacheprovider tests/test_embeddings.py tests/integration/test_vectors.py

OPENDB_REAL_EMBEDDINGS_URL=http://127.0.0.1:18080 \
  /tmp/opendb-mvp-venv/bin/pytest --ds=config.settings.integration \
  -p no:cacheprovider tests/test_embeddings.py tests/integration/test_vectors.py

/tmp/opendb-mvp-venv/bin/ruff check --no-cache \
  opendb/vectors tests/test_embeddings.py tests/integration/test_vectors.py
```

The real smoke indexes three unrelated source rows and verifies that the query
“Where is the cat sleeping?” ranks the kitten sentence above the dog sentence and
the database sentence. Observed cosine scores with the exact artifact were
0.8009, 0.7081, and 0.6125. An additional direct live tokenizer check preserved all
4,100 characters of Unicode text in three chunks with maximum context usage 512.
Deterministic tests cover backfill, SQL vectors, source/key changes, transactional
rollback, leases, obsolete failures, retries, empty content, lossless chunk offsets,
partial failures, grants, view-only access, revocation during inference, RLS,
bootstrap ownership, CLI integration, dropped attributes/constraints, no-op UPDATEs,
source filters, read-only guest search, view dependencies/projections, and bound
filter values. Set `OPENDB_REAL_EMBEDDINGS_URL` explicitly
to run the model test; otherwise that one test is skipped.

Shared configuration changes needed: none beyond the parent's existing psycopg
binary dependency and pgvector PostgreSQL 17 service; set `OPENDB_EMBEDDING_URL`,
deploy the pinned llama-server separately, and run the worker above. No shared
Docker, settings, dependency, or fixture files were edited by this module's worker.
