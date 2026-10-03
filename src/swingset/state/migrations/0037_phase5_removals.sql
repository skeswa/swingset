-- Removals for phase 5 of docs/plans/architecture.md (section 9).
--
-- Part 1 (D-0237): requirement history goes, and unit failures leave the
-- review queue. A work unit's failure lives in work_attempts and work_queue,
-- which the quarantine report reads. Every open work_attempt requirement
-- closes as out of scope, and its retained support stops restoring it. The
-- requirement scan and its cursor stay as the data-quality inventory.
--
-- The two triggers on findings wrote requirement_transitions; they go first,
-- so closing the findings writes no history. Dropping a table drops its
-- indexes and publication fence triggers; the runner reinstalls fences on
-- what remains.
DROP TRIGGER requirement_opened;
DROP TRIGGER requirement_changed;
-- One bump of the findings revision covers parts 1 and 3.
UPDATE revisions SET value=value+1 WHERE name='findings' AND EXISTS(
    SELECT 1 FROM findings
    WHERE closed_at IS NULL AND (
        (owner_kind='requirement' AND kind='work_attempt')
        OR (owner_kind='event_site_backfill' AND kind='history_event_site_review')
    )
);
UPDATE findings
SET state='out_of_scope',
    next_action='unit failures live in the work queue',
    closed_at=strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),
    status_at=strftime('%Y-%m-%dT%H:%M:%S+00:00','now')
WHERE owner_kind='requirement' AND kind='work_attempt' AND closed_at IS NULL;
-- A support row whose findings row was lost would otherwise come back as an
-- open finding on the scan's next pass.
UPDATE finding_support SET active=0
WHERE active=1
    AND json_extract(payload_json,'$.owner_kind')='requirement'
    AND json_extract(payload_json,'$.kind')='work_attempt';
-- Part 3 (D-0236): the event-site review is an offline tool. Its open
-- findings lost their only writer and closer, so they close as out of scope
-- and their support stops restoring them; a reviewer works from the tool's
-- report, not the review queue.
UPDATE findings
SET state='out_of_scope',
    next_action='journal/tools/collection/event_site_review.py reports event sites offline',
    closed_at=strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),
    status_at=strftime('%Y-%m-%dT%H:%M:%S+00:00','now')
WHERE owner_kind='event_site_backfill' AND kind='history_event_site_review'
    AND closed_at IS NULL;
UPDATE finding_support SET active=0
WHERE active=1
    AND json_extract(payload_json,'$.owner_kind')='event_site_backfill'
    AND json_extract(payload_json,'$.kind')='history_event_site_review';
DROP TABLE requirement_cohort_members;
DROP TABLE requirement_cohorts;
DROP TABLE requirement_transitions;
DROP TABLE requirement_attempts;

-- Part 2 (D-0238): leased units carry no admission. A parse, project or link
-- unit's lease and running attempt are its in-flight record, so its rows in
-- the ledger go: about 95 % of execution_admissions and nearly all of
-- execution_dependencies. `_migrate` settled any active row before this
-- script. The kept scope rows are copied into a new table and the old one is
-- dropped, so the WAL holds the kept rows, not the deleted ones; freed pages
-- are not zeroed (W2). The runner restores the connection's own setting
-- after the script. Foreign keys are off during a migration and are checked
-- before it commits.
PRAGMA secure_delete=OFF;
CREATE TABLE execution_dependencies_kept (
    action_id TEXT NOT NULL REFERENCES execution_admissions(action_id),
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('source','kind')),
    scope_id TEXT NOT NULL, PRIMARY KEY(action_id,scope_kind,scope_id)
);
INSERT INTO execution_dependencies_kept
SELECT action_id,scope_kind,scope_id FROM execution_dependencies
WHERE substr(action_id,1,5)<>'work_'
ORDER BY action_id,scope_kind,scope_id;
DROP TABLE execution_dependencies;
ALTER TABLE execution_dependencies_kept RENAME TO execution_dependencies;
CREATE INDEX execution_dependencies_scope ON execution_dependencies(scope_kind,scope_id,action_id);
DELETE FROM execution_admissions WHERE substr(action_id,1,5)='work_';
