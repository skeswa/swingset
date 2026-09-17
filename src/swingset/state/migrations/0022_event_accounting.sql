-- Sampled parent support and historical accounting never authorize execution.
CREATE TABLE event_accounting_support_observations (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    generation_id TEXT NOT NULL,
    availability INTEGER CHECK(availability IN (0,1)),
    observed_at TEXT NOT NULL, valid_until TEXT NOT NULL,
    token_json TEXT NOT NULL, reasons_json TEXT NOT NULL,
    PRIMARY KEY(source,source_ref,generation_id)
);
CREATE INDEX event_accounting_support_enumeration ON event_accounting_support_observations(enumeration_id);
CREATE TABLE event_accounting_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, source_ref TEXT NOT NULL, enumeration_id TEXT,
    previous_receipt_id INTEGER REFERENCES event_accounting_receipts(receipt_id),
    assessment TEXT NOT NULL CHECK(assessment IN ('locally_accounted','unfinished','unassessed')),
    transition TEXT NOT NULL CHECK(transition IN ('initial_observation','membership_changed','reopened','availability_restored','assessment_changed')),
    observed_at TEXT NOT NULL, earliest_checked_at TEXT, valid_until TEXT,
    token_json TEXT NOT NULL, summary_json TEXT NOT NULL
);
CREATE INDEX event_accounting_receipts_event ON event_accounting_receipts(source,source_ref,receipt_id);
CREATE INDEX event_accounting_receipts_definite ON event_accounting_receipts(source,source_ref,receipt_id) WHERE assessment!='unassessed';
CREATE INDEX event_accounting_receipts_definite_enum ON event_accounting_receipts(source,source_ref,enumeration_id,receipt_id) WHERE assessment!='unassessed';
CREATE INDEX event_accounting_receipts_previous ON event_accounting_receipts(previous_receipt_id);
CREATE TRIGGER event_accounting_receipt_no_update BEFORE UPDATE ON event_accounting_receipts
BEGIN SELECT RAISE(ABORT,'event accounting receipts are immutable'); END;
CREATE TRIGGER event_accounting_receipt_no_delete BEFORE DELETE ON event_accounting_receipts
BEGIN SELECT RAISE(ABORT,'event accounting receipts are immutable'); END;
