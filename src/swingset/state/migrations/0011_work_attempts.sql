-- Queue generations fence completions without changing admission's timestamp token.
CREATE TABLE work_generations (
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    generation INTEGER NOT NULL, retry_generation INTEGER NOT NULL DEFAULT 0,
    retry_reason TEXT, retry_requested_at TEXT,
    PRIMARY KEY(stage,unit_kind,unit_id)
);
INSERT INTO work_generations(stage,unit_kind,unit_id,generation)
    SELECT stage,unit_kind,unit_id,1 FROM pending_work;
CREATE TRIGGER work_enqueued AFTER INSERT ON pending_work BEGIN
    INSERT INTO work_generations(stage,unit_kind,unit_id,generation)
    VALUES(NEW.stage,NEW.unit_kind,NEW.unit_id,1)
    ON CONFLICT(stage,unit_kind,unit_id) DO UPDATE SET generation=generation+1;
END;
CREATE TRIGGER work_invalidated AFTER UPDATE ON pending_work BEGIN
    UPDATE work_generations SET generation=generation+1
    WHERE stage=NEW.stage AND unit_kind=NEW.unit_kind AND unit_id=NEW.unit_id;
END;
CREATE TABLE work_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    queue_generation INTEGER NOT NULL, retry_generation INTEGER NOT NULL,
    work_token TEXT NOT NULL, input_fingerprint TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id), started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('running','succeeded','blocked','transient','unavailable','interrupted','superseded')),
    reason_code TEXT, evidence_json TEXT NOT NULL DEFAULT '{}', retry_at TEXT,
    requirement_id TEXT
);
CREATE INDEX work_attempts_unit ON work_attempts(stage,unit_kind,unit_id,attempt_id DESC);
CREATE INDEX work_attempts_running ON work_attempts(outcome,attempt_id);
CREATE INDEX work_attempts_run ON work_attempts(run_id);
