ALTER TABLE operator_pauses ADD COLUMN pause_id TEXT;
ALTER TABLE operator_pauses ADD COLUMN actor TEXT;
ALTER TABLE operator_pauses ADD COLUMN control_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE operator_pauses ADD COLUMN created_at TEXT;
UPDATE operator_pauses SET pause_id='legacy_' || lower(hex(randomblob(16))),actor='legacy:unknown';
CREATE UNIQUE INDEX operator_pauses_id ON operator_pauses(pause_id);
CREATE TABLE control_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL);
INSERT INTO control_state VALUES (1,0);
CREATE TABLE control_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    control_revision INTEGER NOT NULL, pause_id TEXT,
    scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('pause','resume','expire','legacy_import')),
    actor TEXT NOT NULL, reason TEXT NOT NULL, until_at TEXT, occurred_at TEXT NOT NULL
);
INSERT INTO control_events(control_revision,pause_id,scope_kind,scope_id,action,actor,reason,until_at,occurred_at)
SELECT 0,pause_id,scope_kind,scope_id,'legacy_import',actor,reason,until_at,strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM operator_pauses;
CREATE INDEX control_events_pause ON control_events(pause_id,event_id);
CREATE TABLE execution_admissions (
    action_id TEXT PRIMARY KEY, action_kind TEXT NOT NULL,
    admitted_at TEXT NOT NULL, control_revision INTEGER NOT NULL,
    run_id TEXT REFERENCES runs(run_id), work_attempt_id INTEGER REFERENCES work_attempts(attempt_id),
    candidate_id TEXT, host TEXT, all_sources INTEGER NOT NULL, all_kinds INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','uncertain','settled')),
    settled_at TEXT, outcome TEXT
);
CREATE INDEX execution_admissions_run ON execution_admissions(run_id);
CREATE INDEX execution_admissions_work ON execution_admissions(work_attempt_id);
CREATE INDEX execution_admissions_state ON execution_admissions(state,admitted_at);
CREATE UNIQUE INDEX execution_one_publication ON execution_admissions((1))
    WHERE state='active' AND action_kind IN ('publish','publication');
CREATE TABLE execution_dependencies (
    action_id TEXT NOT NULL REFERENCES execution_admissions(action_id),
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('source','kind')),
    scope_id TEXT NOT NULL, PRIMARY KEY(action_id,scope_kind,scope_id)
);
CREATE INDEX execution_dependencies_scope ON execution_dependencies(scope_kind,scope_id,action_id);
CREATE INDEX findings_subject_id_control_idx ON findings(subject_id);
