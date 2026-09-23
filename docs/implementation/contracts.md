# Implementation contracts (2026-09-13)

Workdir /home/gastonzarate/repos/opendb/opendb. Branch feat/mvp. No push.
Parent owns opendb/databases/**, shared settings/dependencies/Compose and integration.

## Database API implemented by parent
- PersonalDatabase: id UUID, owner FK, database_name, role_name, status,
 saving_instructions (owner text <=4000), saving_instructions_updated_at.
- provision_personal_database(owner_id:int) -> PersonalDatabase (administrative).
- owner_connection(owner_id:int,database_id) -> context manager psycopg.Connection.
- data_connection(actor_id:int,database_id) -> context manager authenticated as own/guest role.
- privileged_connection(database_id) -> trusted internal connection only, no user SQL.
- require_owner(actor_id,database_id) -> PersonalDatabase or PermissionDenied.
- databases.services.dispatch(actor_id:int, action:str, payload:dict) -> JSON-safe result.
Actions: list_databases, create_database, catalog, query, ingest, create_role,
 grant_object, assign_role, revoke_role, revoke_object, list_access, register_vector,
 search_vectors, vector_status, ingestion_history, saving_instructions, ingestion_guide.
Owner-only saving_instructions {database_id} returns {database_id,instructions,max_length,
 updated_at}; web-only update_saving_instructions {database_id,instructions} normalizes
 line endings, trims and rejects non-text or more than 4000 characters.
list_databases includes saving_instructions for owned databases so the assistant reads
 the owner preferences before saving; they never widen permissions.
Identity is actor_id from validated authentication, NEVER supplied payload.
query payload {database_id,sql,parameters?:list}; ingest {database_id,operation:dict}.
sharing identifiers: role_id UUID, email, object_name (data schema only).
vector register payload: database_id, table, key_column, text_column.
search payload: database_id, index_id, query, limit=10, target_view?:name, filters?:{column:scalar}.
Owner-only ingestion_history: database_id, operation_id?:UUID; source content returned only for a selected operation.
Authenticated ingestion_guide {} returns the canonical schema/example and query function allowlist.
SQL returns at most 500 rows / 4MiB; read-only SELECT uses a server cursor;
DML RETURNING and owner SELECTs containing writable CTEs stream and complete atomically.
bytea serializes as {type:"bytea",base64:"..."}, temporal values as ISO strings, decimals as strings.
Sharing services require control database autocommit and serialize changes with guest membership synchronization.
Object grants retain PostgreSQL OIDs; list_access resolves actual current grants after renames.
- databases.sql_policy.validate_sql(statement:str, *, readonly:bool=False) -> None
raises ValueError for forbidden statements/schema/functions; only schema data accessible.

## Cross-module service contracts
- catalog.install(conn,owner_role:str): trusted bootstrap protected catalog and functions.
- catalog.describe(conn): list authorized data tables/views, columns, annotations.
- ingestion.apply(conn,operation:dict): accepts owner connection, transaction + idempotency.
- vectors.install(conn,owner_role:str): trusted bootstrap vector bookkeeping, SQL functions.
- vectors.register(conn,table:str,key_column:str,text_column:str): trusted ADMIN connection,
 called only after owner auth; validates data table and installs change capture.
- vectors.process_pending(conn, embedder=None, limit=10): trusted ADMIN connection per DB,
 embedding work outside commit critical section, returns count/status summary.
- vectors.search(conn,index_id,query,limit=10,embedder=None): RESTRICTED caller connection,
 never leaks rows beyond SELECT grants (including view-only guests).
- vectors.status(conn): restricted connection, visible registered object state only.

Modules should avoid imports at Django app initialization requiring unavailable peer modules.
Tests in module-owned directories; parent will provide integration fixtures in
 tests/integration/conftest.py: personal_db (PersonalDatabase), owner_conn (context fixture
 psycopg.Connection), admin_conn (admin to same database). pytest.mark.django_db(transaction=True).
Dedicated test cluster pgvector17 from docker-compose.test.yml. Native test runner planned
 .venv/bin/pytest, shared dependencies managed ONLY by parent. Report dependency needs.

An empty streamed DML RETURNING returns rows=[] and columns=[]; SELECT preserves column metadata even without rows.
