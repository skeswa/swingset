# D-0224: Build releases from partition artifacts selected by current pointers

Recorded: 2026-09-29  
Decided by: agent, 2026-09-29, under D-0175; source: phase 3 of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Release selection  
Supersedes: in part, [D-0205](0205-cut-off-identical-derivation-output.md) (the closure-stable rule, F1)  
Superseded by: —

## Decision

A release is assembled from partition artifacts (one per event group, dancer
batch and year) built from the current pointers, never from a dependency
walk. A partition that fails a check is withheld with its reason; the rest
publish. A release manifest (`release-manifest-v1`) names every built
partition's artifact digest, every withheld partition's reason, the override
files the assembly reads, the build code and environment, the baseline
commit, the correction token and `evidence_cutoff`, the newest `created_at`
among the generations and accepted source generations the artifacts name. It
has no clock, run id or path. The candidate's `release_policy` names it by
digest (`mode = "partitioned"`) instead of carrying the public closure.
Publication checks the candidate files, the manifest, every artifact's
digest, the correction token (identity journal, decisions, admission
policies, revocations) and the input bundle. Its admission,
`publication_narrow`, fences only the rows that check reads, so acquire, plan
and derive keep running; a closure-mode publication keeps the full fence.

Output alone now decides a cutoff: F1 is dropped. F2 replay epochs, F3
pinning and D-0212's moved columns stay. A kept generation may therefore name
a superseded upstream one. The closure path, kept for one release by
`[release] path = "closure"`, skips an anchor whose walk reaches a project
generation other than that scope's own anchor, so such a year's events are
withheld, never published stale.
`path = "whole"` assembles the same artifacts in memory; `partitioned`
streams them one partition at a time and publishes the same rows. Both close
a join to a WSDC id the release has no `dancers` row for: the entry or judge
loses it, identity links and candidates naming it go, and placements follow
(counted in `release_policy.partitions`). The closure path does not.
Closure-mode candidates and the closure-mode baseline validate as before.

Provenance runs through the sidecars: the candidate names the manifest, the
manifest each artifact, each sidecar the generations and the snapshot and
parser pairs it read. [D-0022](0022-preserve-supported-event-bases.md) holds
per partition: a rejected legacy events row never removes an event another
generation supports, and a revoked or unsupported row withholds its
partition, no more. Support is matched per observation, as the closure
matched it.

Amended 2026-09-30 (phase 3 review). An event partition is built only when
its event, resolution and link units are all current; otherwise it keeps
the artifact it last built, which was consistent, or is withheld. A listing
two event partitions claim stays with one. A partition whose stage 1 raises,
or whose rows fail a builder check once assembled, is withheld with the
reason, never the release. A partitioned build holds the heavy lock while
`[release] streamed_lock` is true, the default, until the worker measures it
under 2 GB beside derive: the rehearsal measured 6.7 GB before F26's fix.
After the phase 3 switch `closure` builds a release with no events (F30),
which the gate refuses, so it is kept for no release; `whole` is the
fallback.

## Why

F1 existed because the release closure and the retention walk followed a
kept generation's dependencies. Without it a dancer change ends at the first
unchanged unit instead of re-minting a year and every link. A manifest from
inputs, not a clock, is the same on every attempt, so an unchanged release
rebuilds nothing.

## Links

- [Build reference, partitioned releases](../../docs/reference/build.md#partitioned-releases)
- [State reference, publication fences](../../docs/reference/state.md#publication-fences)
