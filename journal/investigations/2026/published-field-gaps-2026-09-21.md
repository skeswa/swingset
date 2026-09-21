# Why published points, identity tables, and event metadata are missing

Date: 2026-09-21, measurements at 15:52–15:54 UTC  
Diagnosis added: 2026-09-21, with replay of the deployed release code  
Type: Investigation  
Topic: Published data quality  
Related plan: [Data quality recovery](../../../docs/plans/data-quality.md)

## Summary

The reported empty fields are real in dataset
[`138352e`](https://huggingface.co/datasets/skeswa/swingset/tree/138352ef5e9ec25e12b60dc6acb85db808c5e77f),
published at 10:26 UTC. Identity links and registry points disappeared in
that release. They were populated in its immediate predecessor. Heat
collection is unimplemented. Location enrichment drops available information.
Missing day-level dates are explicitly represented as month-only precision.

The regression is now reproduced. At 10:02 UTC a new history generation
became selectable before populated link results were refreshed. The next
build selected it, withheld all old populated links, and published without
a check for lost coverage. A separate baseline-only policy then prevents
automatic restoration of the lost IDs. See the
[diagnosis receipt](../../evidence/quality/published-field-gaps-2026-09-21/regression-diagnosis.json).

This investigation changes documentation only. It does not repair or publish
data. Counts and query text are in the [measurement receipt](../../evidence/quality/published-field-gaps-2026-09-21/receipt.json).

## What the published files contain

The worker's acknowledged baseline is candidate `cand_80719e178655435d`,
with an evidence cutoff of 10:19:25 UTC. Its manifest pins source
`uncommitted:pff4khpvr0paq74fs55g8d6q2vhkwm5j-source`. Measurements use the
worker's retained Parquet files and publication receipt; the Hub head was
not independently queried.

| Field or table                               | Latest published measurement            |
| -------------------------------------------- | --------------------------------------- |
| `placements.registry_points_leader`          | 0 populated out of 14,115               |
| `placements.registry_points_follower`        | 0 populated out of 14,115               |
| `placements.registry_confirmed`              | 14,115 false; 0 true                    |
| `placements.points_matches_expected`         | 14,115 null                             |
| `heats`, `identity_links`, `link_candidates` | 0 rows in each                          |
| `entries.wsdc_id`                            | 58,558 null; every entry is `unmatched` |
| `events.city`                                | 1,936 null out of 2,105 (92.0%)         |
| `events.region`                              | 1,956 null (92.9%)                      |
| `events.country`                             | 1,936 null (92.0%)                      |
| `events.start_date`, `events.end_date`       | 254 null in each (12.1%)                |

## Identity and points: a publication regression

| Measurement                        | Previous release `0923f27`, 09:45 UTC | Latest `138352e`, 10:26 UTC |
| ---------------------------------- | ------------------------------------: | --------------------------: |
| Events                             |                                 2,541 |                       2,105 |
| Entries                            |                                93,820 |                      58,558 |
| Entries with WSDC IDs              |                                31,817 |                           0 |
| Identity links                     |                                98,751 |                           0 |
| Link candidates                    |                                92,499 |                           0 |
| Placements                         |                                20,236 |                      14,115 |
| Leader registry points populated   |                                 2,348 |                           0 |
| Follower registry points populated |                                 2,310 |                           0 |
| Registry-confirmed placements      |                                 2,209 |                           0 |
| Expected-points checks populated   |                                 2,154 |                           0 |

The older `35db6a7` release that status previously called current also had
98,751 links and 2,209 confirmed placements. These tables and fields have
worked before. The latest release also has 436 fewer event rows and 35,262
fewer entry rows. The diagnosis below accounts for the event removals and
reproduces the published event set and entry/placement counts.

### Trigger: the history snapshot advanced before linking caught up

Both releases ran the **same** Nix source:
`/nix/store/pff4khpvr0paq74fs55g8d6q2vhkwm5j-source`.
The changed input was the selected derivation graph:
the saved outputs and exact input generations used to produce them.

| UTC      | What happened                                                                                                                                                |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 09:39    | The last populated release selected history generation `dg_ee55aa83…`, completed on September 16. It retained 2,541 link generations.                        |
| 10:02:38 | The same cycle finished history generation `dg_238a7f74…`, incorporating the reprojected registry, inventory, maps, and event results.                       |
| 10:19:25 | The next build selected that new history generation. Only 685 links had been refreshed; all had zero output rows. Another 2,231 link units remained pending. |
| 10:26:05 | It published `138352e`, with no identity or registry-points output. The cycle reported `failed=false`.                                                       |
| 10:59    | The following cycle failed during its initial build, before offline work, on a missing Budafest entry. Its offline phase ran for zero seconds.               |

The earlier runtime and override changes had requeued projection work
([candidate 010](../../evidence/runtime/candidate-010-2026-09-21/receipt.json)).
The new graph replaced all 29,048 dancer generations, although **all 29,048
had identical output digests**. It also replaced all 2,541 old event
generations; 2,490 had identical output digests. The sampled recipes changed
their runtime and event/series-alias inputs. Project recipes include these
inputs broadly, including for dancers.

Each link depends on the entire dancer generation cohort, its event, and
the history generation
([dependency definitions](../../../src/swingset/state/derivation_dependencies.py)).
Compatibility requires exact generation IDs, not merely unchanged row values.
Using the exact deployed `_Selection.add` code against each release's saved
structural graph, all **178 nonempty old link generations** passed against
the old graph and failed against the new one with
`mixed_dependency_generations`. This establishes the direct cause of the
missing identity output. It does not justify bypassing provenance checks
when output hashes happen to match.

### Why that incomplete build was allowed to publish

The release builder selects saved outputs whose inputs agree. It can publish
event results while withholding incompatible identity outputs
([selection code](../../../src/swingset/build/closure.py)). The latest manifest
selects 685 link generations, but **every one has zero output rows**. None of
the 107 published events with entries has a selected link generation.

`select()` chooses the newest internally consistent history generation
first. It then tries compatible links, records missing ones as withheld,
and does not reconsider an older history generation when coverage falls.
The cycle offers a build/publication opportunity before draining offline
work. Its backpressure was false with 2,231 pending links, so the backlog
delay did not intervene.

[Publication validation](../../../src/swingset/publish/safety.py) checks files,
provenance consistency, accepted inputs, corrections, controls, and the
expected parent. It does not compare populated identities or result counts
with the prior release. Consequently, a consistent partial candidate can
replace a richer release. This missing release-quality check is the defect
that turned temporary derivation lag into a public regression.

Without a link output, reconstruction initializes placement points and IDs
to null, confirmation to false, and entries to unmatched
([reconstruction code](../../../src/swingset/build/closure_rows.py)). The empty
public tables therefore do not establish that matching failed or that the
registry has no points. At 15:52 UTC, live state had 97,421 identity links,
91,047 candidate rows, 2,300 confirmed placements, and 1,063 pending link
units. Live state is newer and covers more events than the public release.
The [candidate 011 receipt](../../evidence/runtime/candidate-011-2026-09-21/receipt.json)
also records stale links and build failures interrupting their refresh.

The existing incompatibility test checks that an old link is withheld after
its dependencies change. It does not require the publisher to retain the
last populated release. Candidate 011's non-fatal build change lets offline
work continue after a failed build; it does not add a quality check to a
build that succeeds with empty identity output.

### Why event and result rows also disappeared

Replaying reconstruction with the recorded new graph and its actual prior
baseline reproduced the published event set, 58,558 entries, 14,115 placements,
and both empty identity tables exactly. The saved manifest records 810
unsupported event scopes. These include 435 events present in the prior
release and previously unpublished scopes.

For those 435 old events, new projected facts failed
`legacy_owned_values_not_published`: their source interpretations were still
`legacy_unassessed`, and the new values did not exactly match the published
baseline. Reconstruction rejects such new claims, then withholds the event
and its dependent results if no allowed alternative supports them. It does
not restore the old published event as a fallback.

Examples make the two paths concrete:

- Floorplay New Years Swing Vacation 2010 gained exact dates and a website
  from an unassessed historical listing. The attempted enrichment replaced
  the selected registry-only version; rejection removed the event instead
  of retaining its old month-only row.
- Budafest 2020 gained partner references and changed follower entry IDs
  in placements. The affected source interpretations were unassessed, so
  the changed rows could not use the exact-baseline exception. Across the
  rejected output, 75 entry rows and 1,330 placement rows failed that check.

The remaining removed event, `2026-01-monterey-swinfest`, is an intentional
alias consolidation. The selected map moved `scoringdance:272` to
`2026-01-monterey-swing-fest` by an explicit override. It should not be
restored as a duplicate. There were no added event IDs in this release.

Rejecting unassessed changes is a separate source-acceptance safeguard.
The diagnosis does not establish that those changes are correct, or that
they should be restored without review. The release defect is allowing
their rejection to replace previously published coverage without a
regression check.

### Why refreshing links alone cannot restore the release

There is a second obstacle to recovery. The current
[publication policy](../../../src/swingset/build/identity_policy.py) requires
each proposed default ID to equal that subject's ID in the immediate public
baseline. Otherwise it returns `accuracy_gate_pending`; the manifest says
`accuracy_expansions=withheld_pending_H17`. With an all-null baseline,
restored IDs are treated as new expansions.

A controlled replay of the existing identity-policy fixture now verifies
this in normal build mode: identical confirmed source evidence retained ID
100 and its points with a populated baseline, but returned a null ID, null
points, and `accuracy_gate_pending` when only the baseline ID changed to
null. This is a tested policy mechanism, not a production recovery release.
Repair must address both generation readiness and restoration authority;
draining the queue alone is insufficient. This policy does not require
candidate evidence tables to be empty.

### What the placement fields actually mean

[The points updater](../../../src/swingset/link/effects.py) needs a linked
individual entry, a points-eligible contest, and a registry row matching
person, role, event, division, dance style, and exact placement. Registry
records such as `F` do not satisfy its exact numeric-place lookup.

- Each points column is nullable when that side cannot be joined. A missing
  value does not mean zero points.
- `registry_confirmed` requires points for **both** leader and follower.
  False also covers missing evidence, ineligible contests, and couple-only
  rows; it is not a recorded registry rejection.
- `points_matches_expected` compares available registry points against
  preliminary field sizes. It stays null without points or adequate prelim
  evidence, including a missing-identity finding. Both roles' points need
  not be available for a check, but every available side needs a field size.
  The previous wording “null until the registry posts” was incomplete.

`identity_links` records identity assertions and their publication acceptance.
`link_candidates` retains alternatives and matching signals, including
unaccepted matches. They have distinct uses and real producers; removing
them would hide a release regression rather than resolve it.

## Heats: schema without collection

The model and export define heat assignments, but no current source adapter
or projector constructs a `Heat` record. Live and published counts are zero.
The table is a reserved part of [milestone M5](../../../docs/plans/milestones.md),
not an implemented feed. The generated card's statement that public heat
assignments are “rarely available” omits this implementation gap
([card generator](../../../src/swingset/publish/card.py)).

## Locations: missing enrichment as well as missing source facts

Only 169 published events have a city and country; all use `wsdc_calendar`
as their provenance source. All 107 events with score sheets lack city,
region, and country. Source is the row's recorded provenance, not a claim
that no other source observed that event.

The direct [calendar projector](../../../src/swingset/project/events.py)
writes location fields. The historical
[inventory projector](../../../src/swingset/project/history.py) reads calendar
and platform listings but omits city, region, and country from its insert
and update. Existing values can survive; new historical rows get none.
This explains why an event may gain dates and still lack its available location.

Saved calendar observations contain location text for 913 published events
with missing cities, matched on exact event name and end date. Examples
include Chicago Classic 2010 (`Chicago, IL`) and Dance Camp Chicago 2010
(`Elmhurst, IL`). This is a pool for enrichment, not 913 approved fixes:
source admission, conflicts, and location parsing still need checking.

Other gaps are upstream. Current `source_events` contains no location values;
ordinary `SourceEventRow` processing does not pass a location into that table.
Only the Step Right path supplies one in the current projector. Calendar
location parsing also assumes comma-separated parts, sets region only with
more than two parts, and uses an explicit country attribute rather than
normalizing free text. Thus `Chicago, IL` would lose its region even through
the direct calendar path. Legacy calendar formats and newsletter rows often
lack a structured country. Missing regions are not always errors, and country
must not be guessed from an event name.

## Dates: all missing pairs are month-only

Of 2,105 published events, 1,851 have both dates and `date_precision=day`.
The remaining 254 have neither date and `date_precision=month`: 218
registry-only events and 36 index-only `swingdancecouncil` events. All 107
events with score sheets have dates in this release.

Registry occurrences establish a reporting month, not the exact weekend.
Historical listings can also establish only a month. Null day-level dates
are correct for those inputs; consumers should use `event_month` together
with `date_precision`, not invent first/last days of the month. The
[history contract](../../../docs/reference/backfill.md) requires dated listing
evidence before upgrading precision.

At inspection time, no mapped `source_events` row with both dates joined to
an event missing a date. This narrow check does not prove that no usable date
exists in archived pages or unresolved listings. Acquisition and series
reconciliation remain possible blockers.

## Evidence and reproduction

The [receipt](../../evidence/quality/published-field-gaps-2026-09-21/receipt.json)
records candidate IDs, full dataset commits, manifest hashes, code hashes,
read-only commands, SQL and counts. One-off scripts and test logs remain in
scratch. No third-party sources were fetched and production was not changed.

To repeat the measurement, resolve `/var/lib/swingset/baseline` once, read its
`PUBLISHED` and `_meta/manifest.json`, verify each measured Parquet file against
the manifest, then count nulls across all files in each table. Compare the
previous candidate identified by `baseline_commit`. Group event counts by
`source`, `date_precision`, and `coverage_tier`. Using SQLite `mode=ro` and
`PRAGMA query_only=ON`, run the receipt's SQL and join the manifest's
`selected_generations` to `derivation_generations` and `derivation_row_refs`.
Keep live counts separate from counts at the release cutoff.

The additional [diagnosis receipt](../../evidence/quality/published-field-gaps-2026-09-21/regression-diagnosis.json)
records the historical selection replay, reconstruction counts, changed
recipes, cycle summaries, alias mapping, and recovery-policy fixture.
Production reads used SQLite read-only mode. To reproduce selection, hydrate
each manifest's retained private closure and try the old nonempty link
generations with `_Selection.add` against each pinned structural graph.
Use the exact Nix source recorded above, not a later working tree.

The row replay used `closure_rows.reconstruct` with the failed release's
recorded graph and `0923f27` as baseline. It checked the saved closure
commitment, unchanged revocation digest, and baseline file hashes, but
skipped full raw-artifact validation to isolate reconstruction. It wrote
only disposable scratch output and is not a certified release rehearsal.
The identity-policy fixture mocked accepted source support and changed
only the prior ID; its result proves the baseline gate in isolation.

The [follow-up plan](../../../docs/plans/data-quality.md) makes identity recovery
the first release task and defines checks for the other gaps. No schema
removal, identity-policy change, or deployment is decided by this report.

Documentation validation: `mise run fmt` completed. The core suite returned
992 passed, 1 failed, and 1,453 deselected in 152 seconds. The failure is
`test_process_death_retains_active_pause_drain_until_recovery`: the child
process exited without the expected `reserved` line. The same case is
already recorded as failing in the candidate 011 receipt; this documentation
change does not diagnose or repair it.

Diagnosis validation: 12 focused tests passed, covering incompatible link
generations, the baseline identity restriction, and unsupported structural
alternatives. These run existing tests; the production replay above is the
evidence connecting those mechanisms to this particular release. No runtime
code was changed.
