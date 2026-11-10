-- pending_work's primary key makes each changed unit unique in its transition
-- table. Keep one generation increment per inserted or updated pending row.

DROP TRIGGER "work_enqueued" ON "derive"."pending_work";
CREATE OR REPLACE FUNCTION "derive"."tr_work_enqueued"() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,"fetch",plan,evidence,derive,project,release,ops,pg_temp AS $fn$
BEGIN
 INSERT INTO "derive"."work_generations"(stage,unit_kind,unit_id,generation)
 SELECT stage,unit_kind,unit_id,1 FROM swingset_pending_rows WHERE true
 ORDER BY stage,unit_kind,unit_id
 ON CONFLICT(stage,unit_kind,unit_id) DO UPDATE SET generation=work_generations.generation+1;
 RETURN NULL;
END $fn$;
CREATE TRIGGER "work_enqueued" AFTER INSERT ON "derive"."pending_work"
REFERENCING NEW TABLE AS swingset_pending_rows
FOR EACH STATEMENT EXECUTE FUNCTION "derive"."tr_work_enqueued"();

DROP TRIGGER "work_invalidated" ON "derive"."pending_work";
CREATE OR REPLACE FUNCTION "derive"."tr_work_invalidated"() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,"fetch",plan,evidence,derive,project,release,ops,pg_temp AS $fn$
BEGIN
 UPDATE "derive"."work_generations" AS target SET generation=target.generation+1
 FROM swingset_pending_rows AS changed
 WHERE target.stage=changed.stage AND target.unit_kind=changed.unit_kind
 AND target.unit_id=changed.unit_id;
 RETURN NULL;
END $fn$;
CREATE TRIGGER "work_invalidated" AFTER UPDATE ON "derive"."pending_work"
REFERENCING NEW TABLE AS swingset_pending_rows
FOR EACH STATEMENT EXECUTE FUNCTION "derive"."tr_work_invalidated"();
