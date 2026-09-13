ALTER TABLE findings ADD COLUMN policy_version TEXT NOT NULL DEFAULT '1';
ALTER TABLE findings ADD COLUMN desired_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE findings ADD COLUMN state TEXT NOT NULL DEFAULT 'needs_review';
ALTER TABLE findings ADD COLUMN source TEXT;
ALTER TABLE findings ADD COLUMN next_action TEXT NOT NULL DEFAULT 'review';
ALTER TABLE findings ADD COLUMN blocking_reason TEXT;
ALTER TABLE findings ADD COLUMN next_eligible_at TEXT;
ALTER TABLE findings ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE findings ADD COLUMN last_progress_at TEXT;
ALTER TABLE findings ADD COLUMN status_at TEXT;
UPDATE findings SET state=CASE WHEN closed_at IS NULL THEN 'needs_review' ELSE 'satisfied' END,
    status_at=coalesce(closed_at,opened_at),last_progress_at=closed_at;
CREATE TABLE requirement_transitions (
    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id TEXT NOT NULL, kind TEXT NOT NULL, source TEXT,
    old_state TEXT,new_state TEXT NOT NULL,event TEXT NOT NULL,at TEXT NOT NULL
);
INSERT INTO requirement_transitions(requirement_id,kind,source,old_state,new_state,event,at)
    SELECT finding_id,kind,source,NULL,state,'opened',opened_at FROM findings;
CREATE INDEX requirement_transitions_time ON requirement_transitions(at,transition_id);
CREATE TRIGGER requirement_opened AFTER INSERT ON findings
WHEN NOT EXISTS (SELECT 1 FROM requirement_transitions WHERE requirement_id=NEW.finding_id)
BEGIN
    INSERT INTO requirement_transitions(requirement_id,kind,source,old_state,new_state,event,at)
    VALUES(NEW.finding_id,NEW.kind,NEW.source,NULL,NEW.state,'opened',NEW.opened_at);
END;
CREATE TRIGGER requirement_changed AFTER UPDATE OF state ON findings
WHEN OLD.state<>NEW.state
BEGIN
    INSERT INTO requirement_transitions(requirement_id,kind,source,old_state,new_state,event,at)
    VALUES(NEW.finding_id,NEW.kind,NEW.source,OLD.state,NEW.state,
        CASE WHEN NEW.state='satisfied' THEN 'satisfied'
             WHEN NEW.state='out_of_scope' THEN 'retired'
             WHEN OLD.state IN ('satisfied','out_of_scope') THEN 'reopened'
             ELSE 'state_changed' END,coalesce(NEW.status_at,NEW.opened_at));
END;
CREATE TABLE finding_support (finding_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,active INTEGER NOT NULL);
INSERT INTO finding_support SELECT finding_id,json_object(
    'owner_kind',owner_kind,'owner_id',owner_id,'kind',kind,'subject_kind',subject_kind,
    'subject_id',subject_id,'severity',severity,'summary',summary,'evidence_json',evidence_json,
    'suggested_override',suggested_override,'watch_id',watch_id,'snapshot_id',snapshot_id,
    'opened_at',opened_at,'run_id',run_id),closed_at IS NULL FROM findings;
CREATE TABLE requirement_scan (singleton INTEGER PRIMARY KEY CHECK(singleton=1),cursor TEXT NOT NULL DEFAULT '',started_at TEXT,last_scanned_at TEXT,last_completed_at TEXT,scanned INTEGER NOT NULL DEFAULT 0);
INSERT INTO requirement_scan(singleton) VALUES(1);
CREATE TABLE requirement_cohorts (cohort_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,policy_version TEXT NOT NULL,source TEXT,kind TEXT,bounded INTEGER NOT NULL);
CREATE TABLE requirement_cohort_members (cohort_id TEXT NOT NULL REFERENCES requirement_cohorts(cohort_id),requirement_id TEXT NOT NULL,PRIMARY KEY(cohort_id,requirement_id));
CREATE TABLE requirement_attempts (attempt_id TEXT PRIMARY KEY,requirement_id TEXT NOT NULL,at TEXT NOT NULL,outcome TEXT NOT NULL);
