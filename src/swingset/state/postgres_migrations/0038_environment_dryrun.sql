-- Native Postgres revision; SQLite's retained logical schema remains 37.
ALTER TABLE ops.environment_identity
 DROP CONSTRAINT environment_identity_environment_check;
ALTER TABLE ops.environment_identity
 ADD CONSTRAINT environment_identity_environment_check
 CHECK(environment IN ('dev','dryrun','production'));

CREATE OR REPLACE FUNCTION ops.schema_identity() RETURNS TABLE(key text,value text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,ops AS $fn$
SELECT 'schema_version',obj_description('ops'::regnamespace,'pg_namespace')::json->>'schema_version'
UNION ALL SELECT 'source_schema_version',meta.value FROM ops.meta WHERE meta.key='schema_version'
UNION ALL SELECT 'postgres_migration_checksum',obj_description('ops'::regnamespace,'pg_namespace')::json->>'migration_checksum'
$fn$;
