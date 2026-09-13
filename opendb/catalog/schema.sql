CREATE TABLE IF NOT EXISTS opendb_catalog.ingestion_operations (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    idempotency_key text NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    source jsonb NOT NULL CHECK (jsonb_typeof(source) = 'object'),
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now()
);

CREATE TABLE IF NOT EXISTS opendb_catalog.annotations (
    relation_oid oid NOT NULL,
    column_number smallint NOT NULL DEFAULT 0,
    description text NOT NULL,
    metadata jsonb NOT NULL CHECK (jsonb_typeof(metadata) = 'object'),
    operation_id uuid NOT NULL REFERENCES opendb_catalog.ingestion_operations(id),
    PRIMARY KEY (relation_oid, column_number)
);

CREATE OR REPLACE FUNCTION opendb_catalog.begin_ingestion(
    p_key text, p_hash text, p_source jsonb
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, opendb_catalog, pg_temp AS $body$
DECLARE existing opendb_catalog.ingestion_operations%ROWTYPE;
BEGIN
    -- One database-local lock shared by all structured ingestions and SQL DDL.
    PERFORM pg_catalog.pg_advisory_xact_lock(6847392015641::bigint);
    SELECT * INTO existing FROM opendb_catalog.ingestion_operations
        WHERE idempotency_key = p_key;
    IF FOUND THEN
        IF existing.payload_hash <> p_hash OR existing.result IS NULL THEN
            RAISE EXCEPTION 'Idempotency conflict' USING ERRCODE = 'POD01';
        END IF;
        RETURN pg_catalog.jsonb_build_object('replay', true, 'result', existing.result);
    END IF;
    IF p_source IS NULL OR jsonb_typeof(p_source) <> 'object'
       OR NOT p_source ? 'content' OR NOT p_source ? 'media_type' THEN
        RAISE EXCEPTION 'Invalid source' USING ERRCODE = 'POD02';
    END IF;
    INSERT INTO opendb_catalog.ingestion_operations (idempotency_key, payload_hash, source)
        VALUES (p_key, p_hash, p_source) RETURNING * INTO existing;
    RETURN pg_catalog.jsonb_build_object('replay', false, 'operation_id', existing.id);
END
$body$;

CREATE OR REPLACE FUNCTION opendb_catalog.finish_ingestion(p_id uuid, p_result jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, opendb_catalog, pg_temp AS $body$
BEGIN
    IF p_result IS NULL OR jsonb_typeof(p_result) <> 'object' THEN
        RAISE EXCEPTION 'Invalid result' USING ERRCODE = 'POD02';
    END IF;
    UPDATE opendb_catalog.ingestion_operations SET result = p_result
        WHERE id = p_id AND result IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Invalid ingestion state' USING ERRCODE = 'POD02';
    END IF;
END
$body$;

CREATE OR REPLACE FUNCTION opendb_catalog.annotate(
    p_id uuid, p_table text, p_column text, p_description text, p_metadata jsonb
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, opendb_catalog, pg_temp AS $body$
DECLARE target oid; column_id smallint := 0;
BEGIN
    SELECT c.oid INTO target FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='data' AND c.relname=p_table AND c.relkind IN ('r','p','v','m');
    IF target IS NULL OR NOT EXISTS (
        SELECT 1 FROM opendb_catalog.ingestion_operations WHERE id=p_id AND result IS NULL
    ) THEN
        RAISE EXCEPTION 'Invalid annotation target' USING ERRCODE = 'POD02';
    END IF;
    IF p_column IS NOT NULL THEN
        SELECT a.attnum INTO column_id FROM pg_catalog.pg_attribute a
            WHERE a.attrelid=target AND a.attname=p_column AND a.attnum>0 AND NOT a.attisdropped;
        IF column_id IS NULL THEN
            RAISE EXCEPTION 'Invalid annotation column' USING ERRCODE = 'POD02';
        END IF;
    END IF;
    IF p_description IS NULL OR length(p_description)>10000 OR p_metadata IS NULL
       OR jsonb_typeof(p_metadata)<>'object'
       OR (p_metadata - ARRAY['purpose','units','conventions']::text[]) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'Invalid annotation' USING ERRCODE = 'POD02';
    END IF;
    INSERT INTO opendb_catalog.annotations
        (relation_oid, column_number, description, metadata, operation_id)
        VALUES (target, column_id, p_description, p_metadata, p_id)
        ON CONFLICT (relation_oid, column_number) DO UPDATE SET
            description=EXCLUDED.description, metadata=EXCLUDED.metadata,
            operation_id=EXCLUDED.operation_id;
END
$body$;

CREATE OR REPLACE FUNCTION opendb_catalog.read_annotations()
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, opendb_catalog, pg_temp AS $body$
    SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
        'relation_oid', a.relation_oid, 'column_number', a.column_number,
        'description', a.description, 'metadata', a.metadata
    )), '[]'::jsonb)
    FROM opendb_catalog.annotations a
    JOIN pg_catalog.pg_class c ON c.oid=a.relation_oid
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='data'
$body$;
