# Architecture

This page defines which module owns each step of the pipeline. Read the [pipeline walkthrough](../how-it-works/README.md) first for an example. An observation records what a source says; a projection combines observations into shared dataset records.

[Reference index](README.md)

## On this page

- [Observations and projections](#observations-and-projections)
- [Module boundaries](#module-boundaries)
- [Findings and review](#findings-and-review)
- [Requirement reconciliation in shadow (H11)](#requirement-reconciliation-in-shadow-h11)
- [Source generation boundary](#source-generation-boundary)

The pipeline stores source evidence separately from the current dataset.
Parsers describe what a source says. Projections decide which canonical
rows that evidence supports. Linking adds identity assertions. Build
materializes a consistent view; publish advances the public history.

```text
due watches -> fetch and archive -> parse -> observations
                                              |
calendar + index observations + overrides -> matching map
                                              |
                                  project canonical scopes
                                              |
                                             link
                                              |
                                          build -> publish
```

Fetch is the only stage that reads source sites. Parse, project, link,
and build run offline. Reconcile and publish read or write the Hub. The cycle reconciles
existing publication intent before starting the data path shown above.
Every stage is a CLI subcommand and uses the same work and recovery
rules as a cycle. [Operations](operations.md#cycle) owns cycle ordering;
[local state](state.md) owns persistence and invalidation.

Event completion deepens the scheduler module. Callers still request one next
watch; the module owns event grouping, turn rotation, protected acquisition
shares, and selection reasons. The fetch module retains every request gate.
Completion reports reuse parent relationships, source evidence, stage outputs,
and release receipts. They introduce neither a second fetching loop nor a
second canonical matching map. [Scheduling](scheduling.md#event-completion)
owns this accepted extension; it is not yet implemented or operating-verified.

## Observations and projections

A watch owns its current observation set. An EEPro round yields a
`RoundSheet`, a calendar page yields `CalendarRow`s, a registry lookup
one `DancerLookup`, and an index `SourceEventRow`s. Parsers construct
source-vocabulary observations, never canonical rows or canonical ids.
The [parse writer](parsing.md#contract) replaces a watch's observations
transactionally after a successful parse. Failed parses keep the last
good observations.

Observation scopes are source references: `("source_event",
"eepro:asc2025")`, `("source_event", "scoringdance:304")`,
`("dancer", "123")`, `("source_index", "eepro")`, or
`("calendar", "wsdc")`. The matching map resolves source events to
canonical events. It is a projection of calendar and index observations,
`event_aliases.csv`, and `source_urls.csv`.

Each source URL override both seeds a watch and maps its source
reference to the supplied event id, with `match_method = override`
(highest precedence). The source extracts a stable platform key from
the URL, such as `wdr:<uuid>`; generic adapters use `sha256(url)[:16]`.
The watch and map use that same reference. Canonical contest and entry
ids are computed only after resolving the map, so moving an alias
regroups stored evidence without parsing again.

A pure projection consumes all current observations for one canonical
scope: an event, a dancer, or a source index. Its result contains typed
canonical rows and conflict findings. `project/writer.py` upserts by
primary key, preserves `first_seen_at`, and deletes rows the scope no
longer produces. Scope replacement and downstream work creation are
one transaction. The map work unit also replaces every affected old
and new event scope in its transaction; a source event never exists
under both mappings between commits.

An entry seen in prelims and finals survives while either observation
mentions it. `rounds_danced`, `best_round`, `entry_count`, and
`promoted_count` are computed from the union. Judges span contests in
an event in the same way. Tables need no per-page owner columns.

For conflicting facts, a round page wins over an event page, which
wins over an index. Among equally specific pages, later `observed_at`
wins (the capture time for an archived body, the fetch time otherwise;
see [backfill](backfill.md#data-model-changes)); snapshot id breaks
timestamp ties deterministically. Every
conflict names both snapshots. Canonical provenance names the winning
snapshot. Recomputing unchanged evidence does not advance row timestamps
or revision counters.

## Module boundaries

| Owner           | Contract                                                                                                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sources/`      | Pure extract and parse functions, watch seeds, source polling policy; see [parsing](parsing.md#source-interface)                                                                      |
| `schedule/`     | Host and class fairness, durable turns for source events, and page selection through the existing `next_watch(...)` interface; see [event completion](scheduling.md#event-completion) |
| `project/`      | Pure map and scope projections, plus the transactional writer                                                                                                                         |
| `link/`         | Per-event assignment and identity assertions; writes only link-owned fields and tables                                                                                                |
| `state/work.py` | Accept changed inputs, enqueue affected units, and commit a unit's output and completion together                                                                                     |
| `build/`        | Read a settled database and captured files; produce immutable publication contents                                                                                                    |
| `publish/`      | Own candidate markers, remote commit acknowledgment, and baseline promotion                                                                                                           |
| `backup/`       | Copy the complete recoverable state and verify it on restore                                                                                                                          |

`project_map(index_obs, calendar_obs, aliases, source_urls)` returns a
`SourceEventMap`. `project(scope, observations, context)` returns a
`Projection`: canonical rows grouped by table and conflict findings.
The writer selects observations through the current map. Projection
context contains captured overrides and vocabularies. Identity policy
and dancer-dependent matching belong to link, so a registry refresh
does not silently change a projection's undeclared inputs.

`link_event` loads retained evidence and its input versions, calls the pure
`resolve_event(evidence, rules)`, then commits against those versions. The
resolution owns conclusions, candidate assessments, and reasons. The guarded
commit owns history, findings, dependent placement and watch updates, and work
completion. The [identity reference](identity-linking.md#implementation-and-inspection)
owns the exact interfaces and policy precedence.

Canonical rows are frozen dataclasses in `model/canonical.py`, one per
stored canonical table, with a `key()` method. Project owns their source
facts; link owns identity columns. Both use the transactional writer,
which preserves the other owner's fields on an upsert. Removing a
subject removes its links and candidates in the same transaction and
advances the corresponding revisions. Build owns computed tables such
as the review queue and changelog.

## Findings and review

`findings` stores durable evidence needing human attention: parser
warnings, observation conflicts, invalid responses, unknown enums, and
registry cross-check discrepancies. Evidence names its snapshots or
archived manual inputs. Parser findings are replaced with the watch's
observation set; projection conflicts are replaced with the scope's
projection; a cross-check replaces its own findings. Each owner closes
findings that no longer apply, including after an override resolves them.

Build derives ambiguous links, unsupported contests, and unmatched
source events from current state. These are not separate stored queue
rows. `review_queue` is open findings plus those computed items, as
specified in [build](build.md#review-queue). A registry dump used for a
cross-check is archived as a blob; it never directly populates canonical
tables.

## Requirement reconciliation in shadow (H11)

The inventory scan reads retained round watches, source events, printed-ID
links, registry occurrences, first-point finalists, archived artifact digests,
and accepted finding evidence. It checks local postconditions and writes
requirements and transitions in one transaction. It makes no requests,
changes no identity decisions, and starts no repair work. Registry occurrences
are grouped by series and month, so one mapping gap has one requirement.
First-point requirements persist beyond the intensive thirty-day window.

Unknown finding kinds remain `needs_review`. Existing parser and projector
owners decide when their findings no longer apply; their accepted finding
evidence is retained independently from the inventory rows. The periodic
scan can rebuild missing inventory rows without inventing source evidence.
Admission guards, journal contradictions, and publication support checks join
this inventory as those contracts are implemented.

## Source generation boundary

`sources/interpretation.py` attaches pure accounting to adapter results.
`admission/contracts.py` checks those declarations against archived structure;
registry and round contracts own source-specific field rules. The generic
coverage evaluator owns ordered-page, child, count, revision, critical-field,
and manual-sentinel guards.

`admission/generations.py` freezes inputs and stages evidence before output can
change. `admission/select.py` owns the transaction that checks current desired
inputs and policy, writes observations, invalidates old and new scopes, moves
the accepted pointer, and completes the exact work token. The observation
writer implements scoped replacement or preservation of historical snapshots.
Policy activation requires an external corpus review; read-only corpus tooling
cannot activate itself.

`admission/support.py` exposes the publication-facing interpretation check and
selection digest. Publication can withhold unsupported baseline scopes without
admitting new source data. Legacy unassessed selections remain a disclosed
compatibility state, and explicit revocation always defeats apparent support.
