-- Observed withdrawal evidence does not authorize removal or whole-event retirement.
CREATE TABLE event_retirement_observations (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    predecessor_id TEXT REFERENCES source_event_enumerations(enumeration_id),
    assessment TEXT NOT NULL CHECK(assessment IN ('verified','unassessed','not_applicable')),
    observed_at TEXT NOT NULL, valid_until TEXT NOT NULL,
    token_json TEXT NOT NULL, result_json TEXT NOT NULL,
    retry_fresh INTEGER NOT NULL CHECK(retry_fresh IN (0,1)),
    PRIMARY KEY(source,source_ref)
);
CREATE INDEX event_retirement_observations_enumeration ON event_retirement_observations(enumeration_id);
CREATE INDEX event_retirement_observations_predecessor ON event_retirement_observations(predecessor_id);
CREATE TABLE event_retirement_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proof_digest TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT NOT NULL, predecessor_id TEXT NOT NULL,
    generation_id TEXT NOT NULL, decision_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL, policy_digest TEXT NOT NULL,
    proof_json TEXT NOT NULL
);
-- Evidence identifiers survive loss of their targets for diagnosis.
CREATE INDEX event_retirement_receipts_event ON event_retirement_receipts(source,source_ref,receipt_id);
CREATE TRIGGER event_retirement_receipt_no_update BEFORE UPDATE ON event_retirement_receipts
BEGIN SELECT RAISE(ABORT,'event retirement receipts are immutable'); END;
CREATE TRIGGER event_retirement_receipt_no_delete BEFORE DELETE ON event_retirement_receipts
BEGIN SELECT RAISE(ABORT,'event retirement receipts are immutable'); END;
