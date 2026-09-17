# D-0061: Build a separate schema-14 DCN fixture runner

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Bounded fixture operations  
Supersedes: —  
Superseded by: —

## Decision

Build a new byte-pinned packet for the single DCN index body approved in
[D-0060](0060-approve-exact-dcn-index-fixture.md). Reuse verified schema-14 H13
wrapper code through explicit, checked substitutions; preserve the original
retained packet. Remove the metadata-query execution path and allow only the
exact archived index resource and Archive robots, within the five-request,
16 MiB and 15-minute limits.

Persist a shared host deadline at least ten seconds after exchange completion,
before body retention and H13 settlement. Record transport handoff, exchange
completion and the resulting dispatch floor. This conservative completion
anchor closes the variable bookkeeping gap found in the earlier fixture run.
It preserves stricter host/crawl-delay rules and records no inferred wire time.
An independent process-local monotonic floor preserves the full elapsed gap
when the wall clock changes. A fresh process also waits an initial host floor;
there is no automatic resume of a stopped quarantine.

## Why

Production remains schema 14. Using the integrated later runtime or changing
the original five-body manifest would weaken its reviewed source boundary.
A new fixed packet can preserve the old lifecycle and restricted accounting
while making the new finite allowance and timing correction explicit.

## Consequences

The builder makes no request and opens no production state. The generated
preparer still verifies all frozen source files, actual baseline, schema, hold
and remaining shared budget under the writer lock. The coordinator must review
the packet, prepare actual authorization and serialize execution. No policy,
year acceptance, watch, observation, migration or publication is enabled.
Stopped quarantine remains single-use. Implementation authority is existing;
owner acquisition authority is specifically D-0060, not this proposed record.

## Links

- [Runner preparation and checks](../investigations/2026/dcn-index-runner-2026-09-17.md)
- [Earlier request timing finding](../investigations/2026/fixture-controls-review-2026-09-17.md)
