-- Let a finding say what it relies on instead of being read for clues.
--
-- The file closure used to scan every open finding's evidence JSON for strings
-- that looked like a sha256 and keep any blob or extract of that name. That
-- guesses: a digest recorded for any other reason pinned a file, and a file a
-- finding really needs would be missed if its digest were written in a shape
-- the scan did not recognise. Retention cannot rest on a guess, so support is
-- declared here and the walk reads these rows.
--
-- `kind` says what the digest names: `body` a file under blobs/, `extract` a
-- file under extracts/, `generation` a derivation generation whose output must
-- stay local. A digest can be both a body and an extract, so it gets one row
-- per kind. `sha256` holds a generation id for the `generation` kind, which is
-- not 64 hex characters; the column keeps its name because every other kind is
-- a digest and callers declare all three the same way.
--
-- The Python hook for version 30 in state/db.py backfills the table from the
-- evidence of every finding, using the same pattern match the closure used, so
-- nothing a checkpoint pinned yesterday stops being pinned today. Findings
-- written from now on declare their references through `replace_findings`.
-- Closed findings keep their rows: closing stops a finding pinning anything,
-- and reopening must not have to rediscover what it was about.
CREATE TABLE finding_support_references (
    finding_id TEXT NOT NULL REFERENCES findings(finding_id) DEFERRABLE INITIALLY DEFERRED,
    kind TEXT NOT NULL CHECK(kind IN ('body','extract','generation')),
    sha256 TEXT NOT NULL,
    PRIMARY KEY(finding_id,kind,sha256)
);
CREATE INDEX finding_support_references_digest ON finding_support_references(kind,sha256);
