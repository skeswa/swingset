-- NULL means a legacy capture did not retain commit-order discovery evidence.
ALTER TABLE requirement_cohorts ADD COLUMN first_open_transition_cutoff INTEGER;
