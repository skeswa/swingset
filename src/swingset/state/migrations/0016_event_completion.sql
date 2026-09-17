-- Source identity and retained admission evidence own page obligations, not aliases.
CREATE TABLE source_event_inventory (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT REFERENCES source_event_enumerations(enumeration_id),
    first_known_at TEXT, bootstrap_basis TEXT NOT NULL,
    PRIMARY KEY(source,source_ref)
);
CREATE TABLE source_event_enumerations (
    enumeration_id TEXT PRIMARY KEY,
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    predecessor_id TEXT REFERENCES source_event_enumerations(enumeration_id),
    generation_id TEXT NOT NULL REFERENCES source_generations(generation_id),
    decision_id INTEGER NOT NULL REFERENCES admission_decisions(decision_id),
    parent_support_json TEXT NOT NULL,
    membership_digest TEXT NOT NULL,
    pagination TEXT NOT NULL CHECK(pagination IN ('unknown','complete')),
    pagination_reason TEXT NOT NULL,
    added_json TEXT NOT NULL, removed_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX source_event_enumerations_event ON source_event_enumerations(source,source_ref);
CREATE INDEX source_event_enumerations_parent ON source_event_enumerations(generation_id);
CREATE INDEX source_event_inventory_enumeration ON source_event_inventory(enumeration_id);
CREATE INDEX source_event_enumerations_decision ON source_event_enumerations(decision_id);
CREATE INDEX source_event_enumerations_predecessor ON source_event_enumerations(predecessor_id);
CREATE TABLE source_event_enumeration_members (
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    request_id TEXT NOT NULL, request_json TEXT NOT NULL,
    support_json TEXT NOT NULL, first_known_at TEXT,
    PRIMARY KEY(enumeration_id,request_id)
);
CREATE INDEX event_enumeration_members_request ON source_event_enumeration_members(request_id);
CREATE TABLE event_enumeration_inputs (
    generation_id TEXT PRIMARY KEY REFERENCES source_generations(generation_id),
    decision_id INTEGER NOT NULL REFERENCES admission_decisions(decision_id),
    outcome TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE INDEX event_enumeration_inputs_decision ON event_enumeration_inputs(decision_id);
CREATE TRIGGER event_enumeration_no_update BEFORE UPDATE ON source_event_enumerations
BEGIN SELECT RAISE(ABORT,'event enumerations are immutable'); END;
CREATE TRIGGER event_enumeration_no_delete BEFORE DELETE ON source_event_enumerations
BEGIN SELECT RAISE(ABORT,'event enumerations are immutable'); END;
CREATE TRIGGER event_enumeration_member_no_update BEFORE UPDATE ON source_event_enumeration_members
BEGIN SELECT RAISE(ABORT,'event enumeration members are immutable'); END;
CREATE TRIGGER event_enumeration_member_no_delete BEFORE DELETE ON source_event_enumeration_members
BEGIN SELECT RAISE(ABORT,'event enumeration members are immutable'); END;
CREATE TRIGGER event_enumeration_input_no_update BEFORE UPDATE ON event_enumeration_inputs
BEGIN SELECT RAISE(ABORT,'enumeration bootstrap receipts are immutable'); END;
CREATE TRIGGER event_enumeration_input_no_delete BEFORE DELETE ON event_enumeration_inputs
BEGIN SELECT RAISE(ABORT,'enumeration bootstrap receipts are immutable'); END;
CREATE TABLE source_event_member_watches (
    enumeration_id TEXT NOT NULL, request_id TEXT NOT NULL, watch_id TEXT NOT NULL,
    PRIMARY KEY(enumeration_id,request_id,watch_id),
    FOREIGN KEY(enumeration_id,request_id) REFERENCES source_event_enumeration_members(enumeration_id,request_id)
);
CREATE INDEX source_event_member_watches_watch ON source_event_member_watches(watch_id,enumeration_id);
CREATE TRIGGER event_member_watch_no_update BEFORE UPDATE ON source_event_member_watches
BEGIN SELECT RAISE(ABORT,'event membership watch support is immutable'); END;
CREATE TRIGGER event_member_watch_no_delete BEFORE DELETE ON source_event_member_watches
BEGIN SELECT RAISE(ABORT,'event membership watch support is immutable'); END;
