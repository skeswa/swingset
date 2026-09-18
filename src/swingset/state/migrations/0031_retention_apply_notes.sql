-- One durable note per `gc --apply`, keyed by the plan fingerprint.
--
-- Apply removes files, and the database cannot undo a file removal. So the note
-- commits before any file goes, and it carries the two lists the apply intends
-- to act on: the files it planned to remove and the payload digests it planned
-- to remove. If the process dies between that commit and the last unlink, the
-- note is what says which files were planned, and the next apply finishes the
-- ones a fresh plan still calls eligible.
--
-- plan_digest is the primary key, so rerunning an already applied fingerprint
-- finds its own note and returns the recorded receipt instead of removing
-- anything a second time. files_completed_at is NULL until the file removal of
-- that apply finished, which is exactly the "crashed partway" state. receipt_json
-- holds the receipt as written, so a rerun returns the same bytes rather than
-- rebuilding a receipt from state that has since moved on.
--
-- removed_payload_bytes is measured before the payloads go and stored with them,
-- so the note alone says how much a crashed apply removed. Nothing can count
-- those bytes again afterwards, and a receipt rebuilt from the note has to say
-- the same thing the first one would have said.
--
-- A note is evidence of a removal that already happened, so it is permanent and
-- may not be rewritten once its receipt is recorded. The update trigger allows
-- only the one transition this step makes: filling in files_completed_at and
-- receipt_json on a note that has neither. Evidence is never rewritten to make a
-- failed step look successful.
CREATE TABLE retention_applies (
    plan_digest TEXT PRIMARY KEY,
    planned_files_json TEXT NOT NULL,
    planned_payloads_json TEXT NOT NULL,
    removed_payload_bytes INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    files_completed_at TEXT,
    receipt_json TEXT
);
CREATE TRIGGER retention_apply_no_delete BEFORE DELETE ON retention_applies
BEGIN SELECT RAISE(ABORT,'retention apply notes are permanent'); END;
CREATE TRIGGER retention_apply_no_rewrite BEFORE UPDATE ON retention_applies
WHEN old.files_completed_at IS NOT NULL
    OR old.receipt_json IS NOT NULL
    OR new.plan_digest <> old.plan_digest
    OR new.planned_files_json <> old.planned_files_json
    OR new.planned_payloads_json <> old.planned_payloads_json
    OR new.removed_payload_bytes <> old.removed_payload_bytes
    OR new.started_at <> old.started_at
BEGIN SELECT RAISE(ABORT,'a retention apply note is written once and finished once'); END;
