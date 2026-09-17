-- Whole source-event withdrawal is distinct from withdrawing its known pages.
CREATE TABLE source_event_retirement_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT NOT NULL, predecessor_id TEXT NOT NULL,
    generation_id TEXT NOT NULL, decision_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL, policy_digest TEXT NOT NULL,
    proof_digest TEXT NOT NULL, proof_json TEXT NOT NULL,
    UNIQUE(source,source_ref,enumeration_id)
);
CREATE INDEX source_event_retirement_receipts_event
ON source_event_retirement_receipts(source,source_ref,receipt_id);
CREATE TRIGGER source_event_retirement_no_update BEFORE UPDATE ON source_event_retirement_receipts
BEGIN SELECT RAISE(ABORT,'source event retirement receipts are immutable'); END;
CREATE TRIGGER source_event_retirement_no_delete BEFORE DELETE ON source_event_retirement_receipts
BEGIN SELECT RAISE(ABORT,'source event retirement receipts are immutable'); END;
-- Revalidation can change its exhaustive evidence domain without a new withdrawal.
CREATE TABLE source_event_retirement_proofs (
    proof_digest TEXT PRIMARY KEY, proof_json TEXT NOT NULL
);
CREATE TRIGGER source_event_retirement_proof_no_update BEFORE UPDATE ON source_event_retirement_proofs
BEGIN SELECT RAISE(ABORT,'source event retirement proofs are immutable'); END;
CREATE TRIGGER source_event_retirement_proof_no_delete BEFORE DELETE ON source_event_retirement_proofs
BEGIN SELECT RAISE(ABORT,'source event retirement proofs are immutable'); END;
-- Retained recipe ownership survives mutable watch reparenting. Invalid ownership
-- enters the NULL bucket and prevents a complete source-domain absence proof.
CREATE INDEX source_generations_retained_source ON source_generations(
    CASE WHEN json_valid(recipe_json) THEN
        CASE WHEN json_type(recipe_json,'$.context.source')='text'
             THEN json_extract(recipe_json,'$.context.source') END
    END
);
