-- Store each distinct derivation output row once.
--
-- derivation_rows held one full JSON copy of every output row of every
-- generation, so recomputing a scope that changed ten rows cost a full new
-- copy of all of them. The bytes move into derivation_payloads, addressed by
-- the sha256 of the canonical payload text, and derivation_row_refs keeps the
-- order and identity of each generation's rows. A view named derivation_rows
-- restores the old five-column shape, so existing readers are unchanged.
--
-- Filling the new tables writes every row again before the old table is
-- dropped, so the migration needs roughly twice the old table's bytes free on
-- disk while the write-ahead log holds both copies. Dropping the old table
-- returns its pages to SQLite's free list; the file on disk does not shrink.
-- Reclaiming that space is `gc --reclaim` (plan step 3), not this migration.
--
-- The fill, the digest comparison against every generation label, and the drop
-- run in the Python hook for version 32 in state/db.py, because SQLite has no
-- sha256. Until that drop the old table is intact and the migration rolls back.
--
-- This is the last migration on purpose. Interning is not worth deploying until
-- rows repeat, so schemas 30 and 31 have to run without it, and everything that
-- names the tables below asks derivations.interned() first and falls back to the
-- one table (journal/decisions/0167-intern-derivation-payloads-in-the-last-migration.md).
ALTER TABLE derivation_rows RENAME TO derivation_rows_legacy;
CREATE TABLE derivation_payloads (
    payload_sha256 TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);
-- payload_sha256 names a row in derivation_payloads but carries no SQL foreign
-- key. References are permanent and payload bytes are not: plan step 4 archives
-- the bytes and removes the local copy while every reference stays, and a real
-- foreign key would make that removal impossible, so the delete gate below
-- could never fire. Residency is therefore a fact the removal plan checks, not
-- one SQLite enforces. The index is what makes "which generations still name
-- this payload" cheap for that plan. A reference whose payload is not local
-- disappears from the derivation_rows view, so every reader of that view sees
-- no rows rather than short data and fails its own row-count and digest check.
-- The scope pointer is not such a reader: derivations.current answers from the
-- pointer and its signature alone, so keeping a current generation's bytes
-- local is plan step 3's job, not this schema's.
CREATE TABLE derivation_row_refs (
    generation_id TEXT NOT NULL REFERENCES derivation_generations(generation_id) DEFERRABLE INITIALLY DEFERRED,
    ordinal INTEGER NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(generation_id,ordinal),
    UNIQUE(generation_id,table_name,record_key)
);
CREATE INDEX derivation_row_refs_payload ON derivation_row_refs(payload_sha256);
-- Row references are permanent, exactly as the inline rows were.
CREATE TRIGGER derivation_row_ref_no_update BEFORE UPDATE ON derivation_row_refs
BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
CREATE TRIGGER derivation_row_ref_no_delete BEFORE DELETE ON derivation_row_refs
BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
CREATE TRIGGER derivation_payload_no_update BEFORE UPDATE ON derivation_payloads
BEGIN SELECT RAISE(ABORT,'derivation payloads are immutable'); END;
-- Payload bytes may move to an archive later, so they are the one derivation
-- record that can be deleted. A deleting transaction must carry its own
-- permission row, inserted and removed inside that transaction, in the spirit
-- of removal_authority on source_generations. Nothing in this migration writes
-- it; the removal plan in plan step 3 does.
--
-- The grant table below is always empty, and the permission row's deferred
-- foreign key points at it. SQLite checks a deferred key at COMMIT, so a
-- transaction still holding the permission row cannot commit: the row has to go
-- before the commit that removes the payloads. A grant therefore cannot outlive
-- its transaction and leave the gate open for every later process. If one is
-- ever written with foreign keys off, PRAGMA foreign_key_check reports it,
-- which checkpoint verification, recovery and every migration already run, and
-- opening the database clears it.
CREATE TABLE derivation_payload_removal_grant (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1)
);
CREATE TABLE derivation_payload_removal_authority (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    reason TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    FOREIGN KEY(singleton) REFERENCES derivation_payload_removal_grant(singleton) DEFERRABLE INITIALLY DEFERRED
);
CREATE TRIGGER derivation_payload_guarded_delete BEFORE DELETE ON derivation_payloads
WHEN NOT EXISTS (SELECT 1 FROM derivation_payload_removal_authority)
BEGIN SELECT RAISE(ABORT,'derivation payload removal requires authority'); END;
CREATE VIEW derivation_rows(generation_id,ordinal,table_name,record_key,payload_json) AS
SELECT r.generation_id,r.ordinal,r.table_name,r.record_key,p.payload_json
FROM derivation_row_refs r JOIN derivation_payloads p ON p.payload_sha256=r.payload_sha256;
-- The history dispatch fence watched derivation_rows. Its triggers followed the
-- rename and go with the old table, so they move to the tables that now hold
-- the same facts.
CREATE TRIGGER history_timing_derivation_row_refs_insert AFTER INSERT ON derivation_row_refs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_row_refs_update AFTER UPDATE ON derivation_row_refs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_row_refs_delete AFTER DELETE ON derivation_row_refs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_payloads_insert AFTER INSERT ON derivation_payloads BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_payloads_update AFTER UPDATE ON derivation_payloads BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_payloads_delete AFTER DELETE ON derivation_payloads BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
