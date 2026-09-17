-- Shares divide existing new-work allowance; no new host request capacity.
CREATE TABLE scheduler_capacity_service (
    selected_host TEXT PRIMARY KEY,
    credit INTEGER NOT NULL DEFAULT 0 CHECK(credit BETWEEN -8000 AND 8000)
);
CREATE TABLE scheduler_capacity_policies (
    digest TEXT PRIMARY KEY, policy_json TEXT NOT NULL
);
CREATE TABLE scheduler_capacity_requests (
    action_id TEXT PRIMARY KEY REFERENCES scheduler_requests(action_id),
    selected_host TEXT NOT NULL,
    lane TEXT NOT NULL CHECK(lane IN ('listed_result','discovery')),
    purpose TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(reason IN ('competing','borrowed')),
    policy_digest TEXT NOT NULL REFERENCES scheduler_capacity_policies(digest),
    credit_before INTEGER NOT NULL, credit_after INTEGER NOT NULL
);
CREATE INDEX scheduler_capacity_requests_host ON scheduler_capacity_requests(selected_host,lane);
CREATE INDEX scheduler_capacity_requests_policy ON scheduler_capacity_requests(policy_digest);
