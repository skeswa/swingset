# D-0284: Count bulk history-fence changes per statement

Recorded: 2026-11-10  
Decided by: agent, 2026-11-10; source: D-0172  
Topic: Native history-fence updates  
Supersedes: —  
Superseded by: —

## Decision

Native migration 39 changes four history-timing triggers: watch updates,
work-generation updates, and pending-work inserts and updates. Each counts
its transition rows and advances the history fence by that count once per
statement. Zero affected rows leave the fence untouched. Other row triggers,
including publication fencing and work-generation behavior, stay in place.

The fence has the same value when each SQL statement returns and after a
commit or rollback. No trigger reads the fence during these row operations;
the application checks it between statements. Applied migrations 37 and 38
remain immutable.

## Why

The full-size acceptance profile spends 47.53 of 60.02 seconds in these four
functions' self time and reaches the unchanged write limit. A separate local
counter measurement shows that counting transition rows avoids repeated
updates to one row. The native fix still needs equivalence and scale checks.

## Links

[Stage 2 measurement and implementation gate](../../docs/plans/postgres-migration/7-stage-2.md#measured-history-fence-follow-up)
