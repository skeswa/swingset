-- A grant is an accounting boundary, not proof of HTTP dispatch or completion.
-- Retain the exact effective gap across a crash, including robots crawl delay.
CREATE TABLE host_request_spacing (
    host TEXT PRIMARY KEY REFERENCES hosts(host),
    reservation_id TEXT NOT NULL UNIQUE,
    gap_seconds REAL CHECK (gap_seconds >= 2),
    reserved_at TEXT,
    released_at TEXT
);
-- Prior grant timestamps cannot reconstruct the former robots crawl delay or
-- establish that a request completed. Adoption needs a reviewed stopped-worker
-- baseline, without refunding usage or removing holds.
INSERT INTO host_request_spacing(host,reservation_id)
SELECT host,'legacy_' || host FROM hosts
WHERE EXISTS (SELECT 1 FROM host_budget b WHERE b.host=hosts.host AND b.requests>0);
CREATE TABLE host_request_spacing_baselines (
    reservation_id TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    gap_seconds REAL NOT NULL CHECK (gap_seconds >= 5),
    stopped_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    prior_reservation_id TEXT
);
CREATE TRIGGER host_spacing_baselines_no_update BEFORE UPDATE ON host_request_spacing_baselines
BEGIN SELECT RAISE(ABORT,'request spacing baselines are immutable'); END;
CREATE TRIGGER host_spacing_baselines_no_delete BEFORE DELETE ON host_request_spacing_baselines
BEGIN SELECT RAISE(ABORT,'request spacing baselines are immutable'); END;
