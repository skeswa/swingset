-- Unknown judge roles are unavailable evidence, rather than a mismatch.
CREATE TABLE link_candidates_new (
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    wsdc_id INTEGER NOT NULL,
    score REAL NOT NULL,
    name_similarity REAL NOT NULL,
    name_rarity REAL NOT NULL,
    division_ok INTEGER,
    role_ok INTEGER,
    recency_ok INTEGER NOT NULL,
    geography REAL,
    bib_reuse INTEGER NOT NULL,
    registry_confirms INTEGER NOT NULL,
    source_id_confirms INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    chosen INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id, wsdc_id)
);
INSERT INTO link_candidates_new SELECT * FROM link_candidates;
DROP TABLE link_candidates;
ALTER TABLE link_candidates_new RENAME TO link_candidates;
