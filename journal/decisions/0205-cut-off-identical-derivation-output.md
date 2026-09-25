# D-0205: Cut off identical derivation and parse output

Recorded: 2026-09-25  
Decided by: agent, 2026-09-25, under D-0175; source: phase 1 of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Derivation identity  
Supersedes: in part, [D-0022](0022-preserve-supported-event-bases.md) (do not relabel old generations to avoid a replay)  
Superseded by: in part, [D-0212](0212-cut-off-across-the-moved-column-and-held-parses.md) (the moved coverage column and held parses)

## Decision

A project or link unit still replays in full when its inputs change. When its
output equals the selected generation's, it keeps that id and records an
equivalence (new fingerprint, kept id, recipe, dependency set) instead of a
new generation. It cuts off only when all hold:

- **Closure-stable (F1).** Its direct `derivation` entries and `selected_*`,
  `source_observations` and `calendar_observations` groups equal the kept
  generation's, so a kept id never names a stale dancer.
- **Replay witness (F2).** If the replay moved live rows another stage
  enriched, the kind's `replayed` epoch moves and dependents re-run.
- **Not pinned (F3).** A group member whose id a sibling captured never cuts
  off.

Build never cuts off. History owns `events.coverage_tier`.

A parse cuts off the same way at staging when result, report, contract and
manifest equal the kept generation's (the accepted one, or a shadow unit's
undecided one) and the new fingerprint is not revoked; it writes a
`source_equivalences` row. Revoking a generation revokes every fingerprint
cut off to it. For an enforced kind, admission side effects are not replayed
for an identical result, so a change in what the parse writers write for an
unchanged result needs a re-admit or a `PARSER_VERSION` bump. The
snapshot's own status is the exception: a cut-off parse marks it parsed, so
a failure recorded between two identical parses does not outlive the fix.

`[derivation] cutoff = false` turns cutoff off at schema 33, short of the
byte-copy rollback; it stops new cutoffs only
([output cutoff](../../docs/guides/operation.md#output-cutoff)). Output that
differs for identical inputs is a failure on a retry clock
([D-0211](0211-a-derivation-that-raises-retries-on-a-clock.md)).

## Why

328,169 of 328,233 replacing generations had the output of the one they
replaced; each code deploy re-minted the whole graph. Parse repeated 95.65%
of chained results. Three findings narrowed the plan's "output only" rule.

## Alternatives and consequences

A generation now stands for every equivalent fingerprint, so a release's
`first_seen_at`, `last_seen_at`, `asserted_at` and `run_id` come from the
first derivation. Two reads per re-run whose output changed cost CPU.

## Links

- [Derivation invalidation plan, step 1](../../docs/plans/derivation-invalidation.md)
