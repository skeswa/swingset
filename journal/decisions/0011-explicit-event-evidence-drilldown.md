# D-0011: Verify event artifacts in an explicit doctor drill-down

Recorded: 2026-09-16  
Decided by: agent  
Topic: Event completion reporting  
Supersedes: —  
Superseded by: —

## Decision

Doctor and summary show the retained source-event enumeration catalog. The
catalog labels its page counts as membership only. Local stage totals,
publication counts, and eligible service ages remain unknown.

`--source SOURCE --source-event SOURCE_REF` adds a local evidence check for one
event. It hashes the actual retained files inside the report's existing database
read snapshot, without recovery, migration, requests, or scheduling. The
operator-hold marker remains visible separately from database controls.

## Why and limits

Scanning every retained artifact on each five-second doctor refresh would make
ordinary diagnostics depend on the archive's total size. A database-only count
would miss missing or corrupt files. The explicit drill-down gives a fresh
stage check without presenting cached counters as successful completion.

This is the first reporting increment. Fleet-wide verified progress, eligible
time, service history, and release-represented counts remain work in the
[event-completion extension](../../docs/plans/recovery/README.md#event-completion-extension).
