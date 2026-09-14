-- Pre-business-label annotation function, for upgrade regression.
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

