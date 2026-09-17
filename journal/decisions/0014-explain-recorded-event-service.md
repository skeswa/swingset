# D-0014: Explain event service from issued-request receipts

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: The owner authorized continued implementation of the history and recovery plan. This reporting choice has not been separately accepted.  
Topic: Event completion reporting  
Supersedes: —  
Superseded by: —

## Decision

The event doctor drill-down will join existing event-turn and capacity receipts
by request action. It will report the captured selection policy, turn usage,
actual host charges, capacity lane, borrowing, and last issued-request time.
Aggregate totals cover all retained receipts; recent request detail is bounded.
Old schemas remain readable without migration.

Index event receipts and current turns by source/reference so a drill-down need
not scan unrelated events. Aggregate work still grows with the selected event's
retained history. This index addition is in the local, undeployed migration.

## Why and limits

The durable debit already records who owned each issued request. Reusing it
avoids a second service ledger and prevents a shared request from being charged
to every event that references it. A retry or failed request is service, not
successful progress. These receipts do not establish eligible waiting time,
completion, or the reason for every interval without service; those require
additional stage and blocker history.
