-- Request debits own turn usage; selecting a watch never grants host capacity.
CREATE TABLE scheduler_event_policies (
    digest TEXT PRIMARY KEY, policy_json TEXT NOT NULL
);
CREATE TABLE scheduler_event_turns (
    host TEXT NOT NULL, category TEXT NOT NULL, owner_key TEXT NOT NULL,
    source TEXT, source_ref TEXT,
    position INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0 CHECK(used>=0),
    policy_digest TEXT NOT NULL REFERENCES scheduler_event_policies(digest),
    target_requests INTEGER NOT NULL CHECK(target_requests BETWEEN 1 AND 64),
    enrolled_at TEXT NOT NULL, last_issued_at TEXT,
    PRIMARY KEY(host,category,owner_key), UNIQUE(host,category,position)
);
CREATE TABLE scheduler_event_requests (
    action_id TEXT PRIMARY KEY REFERENCES scheduler_requests(action_id),
    selected_host TEXT NOT NULL, category TEXT NOT NULL, owner_key TEXT NOT NULL,
    source TEXT, source_ref TEXT,
    enumeration_id TEXT REFERENCES source_event_enumerations(enumeration_id),
    turn_position INTEGER NOT NULL,
    policy_digest TEXT NOT NULL REFERENCES scheduler_event_policies(digest),
    reason TEXT NOT NULL
);
CREATE INDEX scheduler_event_requests_owner ON scheduler_event_requests(selected_host,category,owner_key);
CREATE INDEX scheduler_event_requests_event ON scheduler_event_requests(source,source_ref,action_id);
CREATE INDEX scheduler_event_turns_event ON scheduler_event_turns(source,source_ref);
CREATE INDEX scheduler_event_turns_policy ON scheduler_event_turns(policy_digest);
CREATE INDEX scheduler_event_requests_policy ON scheduler_event_requests(policy_digest);
CREATE INDEX scheduler_event_requests_enumeration ON scheduler_event_requests(enumeration_id);
