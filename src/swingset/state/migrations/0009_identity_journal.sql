CREATE TABLE identity_decisions (
    decision_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_event TEXT NOT NULL,
    contest TEXT NOT NULL,
    round TEXT NOT NULL,
    participant TEXT NOT NULL,
    wsdc_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('same_person','different_person','insufficient_evidence','hold_unlinked')),
    evidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    author TEXT NOT NULL,
    date TEXT NOT NULL,
    supersedes TEXT REFERENCES identity_decisions(decision_id),
    row_json TEXT NOT NULL,
    row_sha256 TEXT NOT NULL,
    accepted_digest TEXT NOT NULL,
    accepted_at TEXT NOT NULL
);
CREATE INDEX identity_decisions_reference_idx ON identity_decisions(source,source_event,contest,round,participant);
CREATE INDEX identity_decisions_supersedes_idx ON identity_decisions(supersedes);
CREATE TABLE identity_journal_acceptances (
    digest TEXT PRIMARY KEY,
    bundle_digest TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    decision_count INTEGER NOT NULL,
    generation INTEGER NOT NULL
);
CREATE TABLE identity_source_refs (
    ref_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_event TEXT NOT NULL,
    contest TEXT NOT NULL,
    round TEXT NOT NULL,
    participant TEXT NOT NULL,
    subject_kind TEXT NOT NULL CHECK(subject_kind IN ('entry','judge')),
    locator_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source,source_event,contest,round,participant)
);
CREATE TABLE identity_reference_bindings (
    binding_id TEXT PRIMARY KEY,
    ref_id TEXT NOT NULL REFERENCES identity_source_refs(ref_id),
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    semantic_hash TEXT NOT NULL,
    bound_at TEXT NOT NULL
);
CREATE INDEX identity_reference_bindings_subject_idx ON identity_reference_bindings(subject_kind,subject_id);
CREATE INDEX identity_reference_bindings_ref_idx ON identity_reference_bindings(ref_id);
CREATE TABLE identity_reference_migrations (
    migration_id TEXT PRIMARY KEY,
    from_ref_id TEXT NOT NULL REFERENCES identity_source_refs(ref_id),
    to_ref_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('approved','pending','ambiguous')),
    evidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    author TEXT NOT NULL,
    date TEXT NOT NULL,
    supersedes TEXT REFERENCES identity_reference_migrations(migration_id),
    recorded_at TEXT NOT NULL
);
CREATE INDEX identity_reference_migrations_from_idx ON identity_reference_migrations(from_ref_id);
CREATE INDEX identity_reference_migrations_supersedes_idx ON identity_reference_migrations(supersedes);
CREATE TABLE identity_link_resolutions (
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    ref_ids_json TEXT NOT NULL,
    decision_ids_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    journal_digest TEXT NOT NULL,
    journal_generation INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('accepted','unresolved','revoked','superseded')),
    reason TEXT NOT NULL,
    asserted_at TEXT NOT NULL,
    PRIMARY KEY(subject_kind,subject_id)
);
CREATE TABLE identity_link_history (
    history_id INTEGER PRIMARY KEY,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('accepted','unresolved','revoked','superseded')),
    assertion_json TEXT NOT NULL,
    resolution_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX identity_link_history_subject_idx ON identity_link_history(subject_kind,subject_id,history_id);
INSERT INTO revisions(name,value) VALUES ('identity_decisions',0);
INSERT INTO meta(key,value) VALUES ('identity_journal_digest','');

CREATE TRIGGER identity_decisions_no_update BEFORE UPDATE ON identity_decisions BEGIN SELECT RAISE(ABORT,'identity decisions are append-only'); END;
CREATE TRIGGER identity_decisions_no_delete BEFORE DELETE ON identity_decisions BEGIN SELECT RAISE(ABORT,'identity decisions are append-only'); END;
CREATE TRIGGER identity_acceptances_no_update BEFORE UPDATE ON identity_journal_acceptances BEGIN SELECT RAISE(ABORT,'journal acceptances are append-only'); END;
CREATE TRIGGER identity_acceptances_no_delete BEFORE DELETE ON identity_journal_acceptances BEGIN SELECT RAISE(ABORT,'journal acceptances are append-only'); END;
CREATE TRIGGER identity_migrations_no_update BEFORE UPDATE ON identity_reference_migrations BEGIN SELECT RAISE(ABORT,'reference migrations are append-only'); END;
CREATE TRIGGER identity_migrations_no_delete BEFORE DELETE ON identity_reference_migrations BEGIN SELECT RAISE(ABORT,'reference migrations are append-only'); END;
CREATE TRIGGER identity_bindings_no_update BEFORE UPDATE ON identity_reference_bindings BEGIN SELECT RAISE(ABORT,'reference bindings are append-only'); END;
CREATE TRIGGER identity_bindings_no_delete BEFORE DELETE ON identity_reference_bindings BEGIN SELECT RAISE(ABORT,'reference bindings are append-only'); END;
CREATE TRIGGER identity_history_no_update BEFORE UPDATE ON identity_link_history BEGIN SELECT RAISE(ABORT,'identity history is append-only'); END;
CREATE TRIGGER identity_history_no_delete BEFORE DELETE ON identity_link_history BEGIN SELECT RAISE(ABORT,'identity history is append-only'); END;

CREATE TRIGGER identity_links_retain_deleted AFTER DELETE ON identity_links BEGIN
    INSERT INTO identity_link_history(subject_kind,subject_id,state,assertion_json,resolution_json,recorded_at)
    VALUES (
        OLD.subject_kind,OLD.subject_id,'superseded',
        json_object('link_id',OLD.link_id,'wsdc_id',OLD.wsdc_id,'method',OLD.method,'status',OLD.status,'confidence',OLD.confidence,'constraints_applied',OLD.constraints_applied,'asserted_at',OLD.asserted_at,'run_id',OLD.run_id,'linker_version',OLD.linker_version),
        COALESCE((SELECT json_object('ref_ids',ref_ids_json,'decision_ids',decision_ids_json,'policy_version',policy_version,'journal_digest',journal_digest,'journal_generation',journal_generation,'state',state,'reason',reason) FROM identity_link_resolutions WHERE subject_kind=OLD.subject_kind AND subject_id=OLD.subject_id),'{}'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now')
    );
    UPDATE identity_link_resolutions SET state='superseded' WHERE subject_kind=OLD.subject_kind AND subject_id=OLD.subject_id;
END;
