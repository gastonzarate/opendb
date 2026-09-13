CREATE SEQUENCE IF NOT EXISTS opendb_catalog.vector_version_seq;
CREATE TABLE IF NOT EXISTS opendb_catalog.vector_indexes (
    id uuid PRIMARY KEY,
    source_relid oid NOT NULL,
    key_attnum smallint NOT NULL,
    text_attnum smallint NOT NULL,
    model_id text NOT NULL,
    view_name name NOT NULL,
    UNIQUE (source_relid, key_attnum, text_attnum)
);
-- Resolve attribute numbers and the actual unique key on every read/claim.
-- A dropped column followed by a same-name replacement has a different attnum.
CREATE OR REPLACE VIEW opendb_catalog.vector_valid_sources AS
SELECT i.* FROM opendb_catalog.vector_indexes i
JOIN pg_catalog.pg_class c ON c.oid=i.source_relid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
JOIN pg_catalog.pg_attribute k ON k.attrelid=c.oid AND k.attnum=i.key_attnum
JOIN pg_catalog.pg_attribute t ON t.attrelid=c.oid AND t.attnum=i.text_attnum
WHERE n.nspname='data' AND c.relkind='r'
  AND NOT c.relrowsecurity AND NOT c.relispartition AND NOT c.relhassubclass
  AND NOT EXISTS (SELECT FROM pg_catalog.pg_inherits WHERE inhrelid=c.oid)
  AND NOT k.attisdropped AND NOT t.attisdropped AND k.attnotnull
  AND k.atttypid IN (20,21,23,25,1042,1043,2950)
  AND t.atttypid IN (25,1042,1043)
  AND EXISTS (
      SELECT FROM pg_catalog.pg_index x
      WHERE x.indrelid=c.oid AND x.indisunique AND x.indisvalid
        AND x.indisready AND x.indimmediate AND x.indnkeyatts=1
        AND x.indkey[0]=k.attnum AND x.indpred IS NULL AND x.indexprs IS NULL
  );
REVOKE ALL ON opendb_catalog.vector_valid_sources FROM PUBLIC;

CREATE TABLE IF NOT EXISTS opendb_catalog.vector_rows (
    index_id uuid NOT NULL REFERENCES opendb_catalog.vector_indexes ON DELETE CASCADE,
    source_key jsonb NOT NULL,
    version bigint NOT NULL DEFAULT nextval('opendb_catalog.vector_version_seq'),
    source_text text,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','processing','ready','failed')),
    attempts integer NOT NULL DEFAULT 0,
    retry_at timestamptz DEFAULT now(),
    lease_token uuid,
    error_code text,
    PRIMARY KEY (index_id, source_key)
);
CREATE INDEX IF NOT EXISTS vector_rows_pending ON opendb_catalog.vector_rows (retry_at)
    WHERE state <> 'ready';
CREATE TABLE IF NOT EXISTS opendb_catalog.vector_chunks (
    index_id uuid NOT NULL,
    source_key jsonb NOT NULL,
    version bigint NOT NULL,
    chunk_order integer NOT NULL,
    char_start integer NOT NULL,
    char_end integer NOT NULL,
    token_start integer NOT NULL,
    token_end integer NOT NULL,
    text text NOT NULL,
    embedding public.vector(384) NOT NULL,
    PRIMARY KEY (index_id, source_key, chunk_order),
    FOREIGN KEY (index_id, source_key)
        REFERENCES opendb_catalog.vector_rows ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS vector_chunks_hnsw ON opendb_catalog.vector_chunks
    USING hnsw (embedding public.vector_cosine_ops);

CREATE OR REPLACE FUNCTION opendb_catalog.vector_capture() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    cfg record;
    old_key jsonb;
    old_text text;
    new_key jsonb;
    new_text text;
BEGIN
    -- The index and column identities come from admin-owned bookkeeping and
    -- PostgreSQL's catalogs, never from trigger arguments supplied by a caller.
    FOR cfg IN
        SELECT i.id, k.attname AS key_name, t.attname AS text_name,
               k.atttypid AS key_type, t.atttypid AS text_type
        FROM opendb_catalog.vector_indexes i
        JOIN pg_catalog.pg_class c ON c.oid = i.source_relid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute k ON k.attrelid=c.oid AND k.attnum=i.key_attnum
        JOIN pg_catalog.pg_attribute t ON t.attrelid=c.oid AND t.attnum=i.text_attnum
        WHERE i.source_relid=TG_RELID AND n.nspname='data' AND c.relkind='r'
              AND NOT k.attisdropped AND NOT t.attisdropped
    LOOP
        IF cfg.key_type NOT IN (20,21,23,25,1042,1043,2950)
           OR cfg.text_type NOT IN (25,1042,1043) THEN
            RAISE EXCEPTION 'Vector source column type changed' USING ERRCODE='22023';
        END IF;
        IF TG_OP='TRUNCATE' THEN
            DELETE FROM opendb_catalog.vector_rows WHERE index_id=cfg.id;
            CONTINUE;
        END IF;
        IF TG_OP IN ('UPDATE','DELETE') THEN
            EXECUTE format('SELECT pg_catalog.to_jsonb(($1).%I), ($1).%I::pg_catalog.text',
                           cfg.key_name, cfg.text_name)
                INTO old_key, old_text USING OLD;
        END IF;
        IF TG_OP IN ('INSERT','UPDATE') THEN
            EXECUTE format('SELECT pg_catalog.to_jsonb(($1).%I), ($1).%I::pg_catalog.text',
                           cfg.key_name, cfg.text_name)
                INTO new_key, new_text USING NEW;
        END IF;
        IF TG_OP='UPDATE' AND old_key IS NOT DISTINCT FROM new_key
           AND old_text IS NOT DISTINCT FROM new_text THEN
            CONTINUE;
        END IF;
        IF TG_OP='DELETE' OR (TG_OP='UPDATE' AND old_key IS DISTINCT FROM new_key) THEN
            DELETE FROM opendb_catalog.vector_rows WHERE index_id=cfg.id AND source_key=old_key;
        END IF;
        IF TG_OP IN ('INSERT','UPDATE') THEN
            INSERT INTO opendb_catalog.vector_rows (index_id,source_key,source_text)
                VALUES (cfg.id,new_key,new_text)
            ON CONFLICT (index_id,source_key) DO UPDATE
                SET version=nextval('opendb_catalog.vector_version_seq'),
                    source_text=excluded.source_text, state='pending', attempts=0,
                    retry_at=now(), lease_token=NULL, error_code=NULL;
            DELETE FROM opendb_catalog.vector_chunks WHERE index_id=cfg.id AND source_key=new_key;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION opendb_catalog.vector_capture() FROM PUBLIC;

CREATE OR REPLACE FUNCTION opendb_catalog.vector_visible(wanted uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM opendb_catalog.vector_valid_sources i
        JOIN pg_catalog.pg_class c ON c.oid=i.source_relid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE i.id=wanted AND n.nspname='data' AND c.relkind='r'
          AND NOT c.relrowsecurity AND NOT c.relhassubclass
          AND pg_catalog.has_schema_privilege(
              coalesce(nullif(current_setting('role'),'none'),session_user), n.oid, 'USAGE')
          AND pg_catalog.has_table_privilege(
              coalesce(nullif(current_setting('role'),'none'),session_user), c.oid, 'SELECT')
    );
$$;
CREATE OR REPLACE FUNCTION opendb_catalog.vector_status() RETURNS SETOF jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT jsonb_build_object(
        'index_id', i.id, 'table', 'data.' || c.relname,
        'key_column', k.attname, 'text_column', t.attname,
        'model_id', i.model_id, 'view_name', i.view_name,
        'pending', count(r.index_id) FILTER (WHERE r.state='pending'),
        'processing', count(r.index_id) FILTER (WHERE r.state='processing'),
        'ready', count(r.index_id) FILTER (WHERE r.state='ready'),
        'failed', count(r.index_id) FILTER (WHERE r.state='failed')
    )
    FROM opendb_catalog.vector_indexes i
    JOIN pg_catalog.pg_class c ON c.oid=i.source_relid
    JOIN pg_catalog.pg_attribute k ON k.attrelid=c.oid AND k.attnum=i.key_attnum
    JOIN pg_catalog.pg_attribute t ON t.attrelid=c.oid AND t.attnum=i.text_attnum
    LEFT JOIN opendb_catalog.vector_rows r ON r.index_id=i.id
    WHERE opendb_catalog.vector_visible(i.id)
    GROUP BY i.id,c.relname,k.attname,t.attname ORDER BY i.id;
$$;
-- Only this fixed metadata predicate is privileged; target view rows are always
-- selected on the restricted caller connection by the Python retrieval layer.
CREATE OR REPLACE FUNCTION opendb_catalog.vector_view_visible(wanted uuid, target oid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    WITH RECURSIVE dependencies(oid) AS (
        SELECT target
        UNION
        SELECT d.refobjid
        FROM dependencies p
        JOIN pg_catalog.pg_rewrite w ON w.ev_class=p.oid
        JOIN pg_catalog.pg_depend d ON d.classid='pg_catalog.pg_rewrite'::regclass
             AND d.objid=w.oid AND d.refclassid='pg_catalog.pg_class'::regclass
    )
    SELECT EXISTS (
        SELECT FROM opendb_catalog.vector_valid_sources i
        JOIN pg_catalog.pg_class backing ON backing.relname=i.view_name
        JOIN pg_catalog.pg_namespace bn ON bn.oid=backing.relnamespace
        JOIN pg_catalog.pg_class granted ON granted.oid=target
        JOIN pg_catalog.pg_namespace gn ON gn.oid=granted.relnamespace
        WHERE i.id=wanted AND bn.nspname='data' AND backing.relkind='v'
          AND backing.relowner=(SELECT relowner FROM pg_catalog.pg_class
                                WHERE oid='opendb_catalog.vector_indexes'::regclass)
          AND gn.nspname='data' AND granted.relkind='v'
          AND backing.oid IN (SELECT oid FROM dependencies)
          AND pg_catalog.has_schema_privilege(
              coalesce(nullif(current_setting('role'),'none'),session_user),gn.oid,'USAGE')
          AND pg_catalog.has_table_privilege(
              coalesce(nullif(current_setting('role'),'none'),session_user),target,'SELECT')
    );
$$;

-- Upgrade the original four-argument entrypoint; the default preserves callers.
DROP FUNCTION IF EXISTS opendb_catalog.vector_search(uuid,text,text,integer);
CREATE OR REPLACE FUNCTION opendb_catalog.vector_search(
    wanted uuid, query_input text, wanted_model text, result_limit integer,
    filters jsonb DEFAULT '{}'::jsonb
) RETURNS SETOF jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    query_vector public.vector;
    cfg record;
    field text;
    conditions text := '';
    statement text;
BEGIN
    IF NOT opendb_catalog.vector_visible(wanted) THEN
        RAISE EXCEPTION 'Vector index unavailable' USING ERRCODE='42501';
    END IF;
    SELECT i.*,c.relname INTO cfg FROM opendb_catalog.vector_valid_sources i
        JOIN pg_catalog.pg_class c ON c.oid=i.source_relid WHERE i.id=wanted;
    -- Hold the real relation identity through validation/query. This prevents
    -- a concurrent DROP/recreate from replacing the checked table by name.
    EXECUTE format('LOCK TABLE data.%I IN ACCESS SHARE MODE',cfg.relname);
    IF to_regclass(format('data.%I',cfg.relname)) IS DISTINCT FROM cfg.source_relid
       OR NOT opendb_catalog.vector_visible(wanted) THEN
        RAISE EXCEPTION 'Vector index unavailable' USING ERRCODE='42501';
    END IF;
    SELECT i.*,c.relname,k.attname AS key_name,t.attname AS text_name INTO cfg
        FROM opendb_catalog.vector_valid_sources i
        JOIN pg_catalog.pg_class c ON c.oid=i.source_relid
        JOIN pg_catalog.pg_attribute k ON k.attrelid=c.oid AND k.attnum=i.key_attnum
        JOIN pg_catalog.pg_attribute t ON t.attrelid=c.oid AND t.attnum=i.text_attnum
        WHERE i.id=wanted;
    IF result_limit IS NULL OR result_limit < 1 OR result_limit > 1000 THEN
        RAISE EXCEPTION 'Limit must be between 1 and 1000' USING ERRCODE='22023';
    END IF;
    IF cfg.model_id IS DISTINCT FROM wanted_model THEN
        RAISE EXCEPTION 'Embedding model mismatch' USING ERRCODE='22023';
    END IF;
    IF filters IS NULL OR jsonb_typeof(filters)<>'object' THEN
        RAISE EXCEPTION 'Filters require an object' USING ERRCODE='22023';
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(filters)) > 16 THEN
        RAISE EXCEPTION 'At most 16 filters are supported' USING ERRCODE='22023';
    END IF;
    FOR field IN SELECT jsonb_object_keys(filters) LOOP
        IF jsonb_typeof(filters->field) NOT IN ('string','number','boolean','null')
           OR NOT EXISTS (
               SELECT FROM pg_catalog.pg_attribute a WHERE a.attrelid=cfg.source_relid
                 AND a.attname=field AND a.attnum>0 AND NOT a.attisdropped
                 AND a.atttypid IN (16,17,20,21,23,25,114,700,701,1042,1043,
                                   1082,1083,1114,1184,1186,1266,1700,2950,3802)
           ) THEN
            RAISE EXCEPTION 'Unsupported filter column or value' USING ERRCODE='22023';
        END IF;
        conditions := conditions || format(
            ' AND coalesce(pg_catalog.to_jsonb(s.%I),''null''::jsonb)=($4->%L)',field,field);
    END LOOP;
    query_vector := query_input::public.vector;
    IF query_vector IS NULL OR public.vector_dims(query_vector) <> 384
       OR public.vector_norm(query_vector) <= 0 THEN
        RAISE EXCEPTION 'Expected a nonzero 384-dimensional vector' USING ERRCODE='22023';
    END IF;
    statement := format($query$
        SELECT jsonb_build_object(
            'index_id', c.index_id, 'source_key', c.source_key,
            'version', c.version, 'chunk_order', c.chunk_order,
            'char_start', c.char_start, 'char_end', c.char_end,
            'token_start', c.token_start, 'token_end', c.token_end,
            'text', c.text, 'score', 1-(c.embedding OPERATOR(public.<=>) $1)
        )
        FROM opendb_catalog.vector_chunks c
        JOIN opendb_catalog.vector_rows r USING (index_id,source_key,version)
        JOIN data.%I s ON pg_catalog.to_jsonb(s.%I)=c.source_key
                     AND s.%I::pg_catalog.text IS NOT DISTINCT FROM r.source_text
        WHERE c.index_id=$2 AND r.state='ready' %s
        ORDER BY c.embedding OPERATOR(public.<=>) $1,c.source_key,c.chunk_order
        LIMIT $3
    $query$,cfg.relname,cfg.key_name,cfg.text_name,conditions);
    RETURN QUERY EXECUTE statement USING query_vector,wanted,result_limit,filters;
END $$;
GRANT EXECUTE ON FUNCTION opendb_catalog.vector_visible(uuid) TO PUBLIC;
GRANT EXECUTE ON FUNCTION opendb_catalog.vector_status() TO PUBLIC;
GRANT EXECUTE ON FUNCTION opendb_catalog.vector_view_visible(uuid,oid) TO PUBLIC;
GRANT EXECUTE ON FUNCTION opendb_catalog.vector_search(uuid,text,text,integer,jsonb) TO PUBLIC;
REVOKE ALL ON opendb_catalog.vector_indexes, opendb_catalog.vector_rows,
    opendb_catalog.vector_chunks, opendb_catalog.vector_version_seq FROM PUBLIC;
