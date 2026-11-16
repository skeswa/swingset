-- Link replacement deletes by subject_id across both subject kinds.
-- The composite keys lead with subject_kind and leave those deletes scanning
-- the full relation once per changed subject.
CREATE INDEX identity_links_subject_idx ON "project"."identity_links" (subject_id);
CREATE INDEX link_candidates_subject_idx ON "project"."link_candidates" (subject_id);
