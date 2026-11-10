-- Count affected rows at the SQL statement boundary. Publication and work
-- generation row triggers remain unchanged; the revision delta stays per row.

DROP TRIGGER "history_timing_watches_update" ON "fetch"."watches";
CREATE OR REPLACE FUNCTION "fetch"."tr_history_timing_watches_update"() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,"fetch",plan,evidence,derive,project,release,ops,pg_temp AS $fn$
DECLARE affected_rows bigint;
BEGIN
 SELECT count(*) INTO affected_rows FROM swingset_changed_rows;
 IF affected_rows > 0 THEN
  UPDATE history_dispatch_fence SET revision=revision+affected_rows WHERE singleton=1;
 END IF;
 RETURN NULL;
END $fn$;
CREATE TRIGGER "history_timing_watches_update" AFTER UPDATE ON "fetch"."watches"
REFERENCING NEW TABLE AS swingset_changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION "fetch"."tr_history_timing_watches_update"();

DROP TRIGGER "history_timing_work_generations_update" ON "derive"."work_generations";
CREATE OR REPLACE FUNCTION "derive"."tr_history_timing_work_generations_update"() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,"fetch",plan,evidence,derive,project,release,ops,pg_temp AS $fn$
DECLARE affected_rows bigint;
BEGIN
 SELECT count(*) INTO affected_rows FROM swingset_changed_rows;
 IF affected_rows > 0 THEN
  UPDATE history_dispatch_fence SET revision=revision+affected_rows WHERE singleton=1;
 END IF;
 RETURN NULL;
END $fn$;
CREATE TRIGGER "history_timing_work_generations_update" AFTER UPDATE ON "derive"."work_generations"
REFERENCING NEW TABLE AS swingset_changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION "derive"."tr_history_timing_work_generations_update"();

DROP TRIGGER "history_timing_pending_work_insert" ON "derive"."pending_work";
CREATE OR REPLACE FUNCTION "derive"."tr_history_timing_pending_work_insert"() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,"fetch",plan,evidence,derive,project,release,ops,pg_temp AS $fn$
DECLARE affected_rows bigint;
BEGIN
 SELECT count(*) INTO affected_rows FROM swingset_changed_rows;
 IF affected_rows > 0 THEN
  UPDATE history_dispatch_fence SET revision=revision+affected_rows WHERE singleton=1;
 END IF;
 RETURN NULL;
END $fn$;
CREATE TRIGGER "history_timing_pending_work_insert" AFTER INSERT ON "derive"."pending_work"
REFERENCING NEW TABLE AS swingset_changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION "derive"."tr_history_timing_pending_work_insert"();

DROP TRIGGER "history_timing_pending_work_update" ON "derive"."pending_work";
CREATE OR REPLACE FUNCTION "derive"."tr_history_timing_pending_work_update"() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,"fetch",plan,evidence,derive,project,release,ops,pg_temp AS $fn$
DECLARE affected_rows bigint;
BEGIN
 SELECT count(*) INTO affected_rows FROM swingset_changed_rows;
 IF affected_rows > 0 THEN
  UPDATE history_dispatch_fence SET revision=revision+affected_rows WHERE singleton=1;
 END IF;
 RETURN NULL;
END $fn$;
CREATE TRIGGER "history_timing_pending_work_update" AFTER UPDATE ON "derive"."pending_work"
REFERENCING NEW TABLE AS swingset_changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION "derive"."tr_history_timing_pending_work_update"();
