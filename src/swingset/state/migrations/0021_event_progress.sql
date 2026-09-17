-- Successful outputs are facts; sampled request progress is not completion authority.
CREATE TABLE event_stage_operations (
    operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL CHECK(stage IN ('acquired','interpreted')),
    source TEXT NOT NULL, request_id TEXT, watch_id TEXT NOT NULL,
    snapshot_id TEXT, generation_id TEXT, decision_id INTEGER,
    occurred_at TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
    CHECK((stage='acquired' AND snapshot_id IS NOT NULL AND request_id IS NOT NULL AND generation_id IS NULL AND decision_id IS NULL)
       OR (stage='interpreted' AND generation_id IS NOT NULL AND decision_id IS NOT NULL AND snapshot_id IS NULL)),
    UNIQUE(snapshot_id), UNIQUE(generation_id)
);
-- Evidence identifiers deliberately survive deletion or corruption of their targets.
CREATE INDEX event_stage_operations_run ON event_stage_operations(run_id);
CREATE INDEX event_stage_operations_request ON event_stage_operations(source,stage,request_id,operation_id);
CREATE INDEX event_stage_operations_source ON event_stage_operations(source,stage,operation_id);
CREATE INDEX event_stage_operations_watch ON event_stage_operations(watch_id,operation_id);
CREATE TABLE event_progress_policies(digest TEXT PRIMARY KEY, policy_json TEXT NOT NULL);
CREATE TABLE event_progress_observations (
    source TEXT NOT NULL, source_ref TEXT NOT NULL, request_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('acquired','interpreted')),
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    observed_at TEXT NOT NULL, valid_until TEXT NOT NULL,
    availability INTEGER CHECK(availability IN (0,1)),
    operation_frontier INTEGER NOT NULL, token_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    PRIMARY KEY(source,source_ref,request_id,stage)
);
CREATE INDEX event_progress_observations_enumeration ON event_progress_observations(enumeration_id);
CREATE TABLE event_progress_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, source_ref TEXT NOT NULL, request_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('acquired','interpreted')),
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    operation_id INTEGER NOT NULL REFERENCES event_stage_operations(operation_id),
    missing_observed_at TEXT NOT NULL, verified_at TEXT NOT NULL,
    token_json TEXT NOT NULL,
    UNIQUE(source,source_ref,request_id,stage,operation_id)
);
CREATE INDEX event_progress_receipts_event ON event_progress_receipts(source,source_ref,receipt_id);
CREATE INDEX event_progress_receipts_operation ON event_progress_receipts(operation_id);
CREATE INDEX event_progress_receipts_enumeration ON event_progress_receipts(enumeration_id);
CREATE TABLE event_progress_scans (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT, after_request_id TEXT, assessment_reason TEXT, assessed_at TEXT NOT NULL,
    PRIMARY KEY(source,source_ref)
);
CREATE TABLE event_progress_cursor (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_rowid INTEGER NOT NULL DEFAULT 0,
    high_water INTEGER NOT NULL DEFAULT 0
);
INSERT INTO event_progress_cursor(singleton) VALUES(1);
CREATE TRIGGER event_stage_operation_no_update BEFORE UPDATE ON event_stage_operations
BEGIN SELECT RAISE(ABORT,'event stage operations are immutable'); END;
CREATE TRIGGER event_stage_operation_no_delete BEFORE DELETE ON event_stage_operations
BEGIN SELECT RAISE(ABORT,'event stage operations are immutable'); END;
CREATE TRIGGER event_progress_receipt_no_update BEFORE UPDATE ON event_progress_receipts
BEGIN SELECT RAISE(ABORT,'event progress receipts are immutable'); END;
CREATE TRIGGER event_progress_receipt_no_delete BEFORE DELETE ON event_progress_receipts
BEGIN SELECT RAISE(ABORT,'event progress receipts are immutable'); END;
CREATE TRIGGER event_progress_policy_no_update BEFORE UPDATE ON event_progress_policies
BEGIN SELECT RAISE(ABORT,'event progress policies are immutable'); END;
CREATE TRIGGER event_progress_policy_no_delete BEFORE DELETE ON event_progress_policies
BEGIN SELECT RAISE(ABORT,'event progress policies are immutable'); END;
-- Normal construction inserts members before moving the inventory pointer.
-- A later append to an already current enumeration invalidates in-flight hints.
CREATE TRIGGER event_progress_current_member_insert AFTER INSERT ON source_event_enumeration_members
WHEN EXISTS(SELECT 1 FROM source_event_inventory WHERE enumeration_id=NEW.enumeration_id)
BEGIN UPDATE event_pressure_state SET epoch=epoch+1; END;
