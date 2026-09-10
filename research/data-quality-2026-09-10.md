# First-pass data quality audit

Audited 2026-09-10. **Useful for exploratory result lookup; not ready for
callback-rate, dancer-history, or registry-points analysis.** File integrity is
strong. Semantic correctness and coverage need further work.

The audit pins public commit
[`b4dc135e07d7dbe8d62ef4eb7182e0223f5ec403`](https://huggingface.co/datasets/skeswa/swingset/tree/b4dc135e07d7dbe8d62ef4eb7182e0223f5ec403),
built at 13:22:31 UTC with code `9bade45d`, projector 11, and linker 4.
Collection continued during the audit; later live-state counts are excluded.

## What is present

| Measure | Published count |
|---|---:|
| Event metadata rows | 573 |
| Events with contest results and placements | 96 |
| Contests / rounds | 1,792 / 2,733 |
| Entries / placements | 50,120 / 13,904 |
| Callback summaries / individual callback marks | 37,133 / 187,958 |
| Final judge marks | 78,410 |
| Registry dancers / registry placements | 2,228 / 21,230 |
| Review items | 2,922 |
| Heat assignments | 0 |

Entries are contest records, sometimes representing couples; they are not a
count of unique people. Placements include 13,195 classified as WCS, 452 as
other, 168 as country, and 89 as lindy.

| Results source | Metadata events | Events with results | Placements |
|---|---:|---:|---:|
| EEPro | 72 | 61 | 8,785 |
| scoring.dance | 319 | 22 | 1,975 |
| WDR | 13 | 13 | 3,144 |
| WSDC calendar | 169 | 0 | 0 |

These are coverage counts within our current event table, not recall against
all competitions. The table mixes historical and future editions. Of 399
events whose end date is before the audit date, 92 have results (23.1%).
Malformed dates described below also affect time-based coverage queries.

## What passed

- Every downloaded file matches its manifest hash. All 17 table schemas and
  row counts match the manifest; no duplicate published primary keys.
- All 23 tested structural foreign-key relationships resolve. Event, contest,
  round, placement, and callback relationships agree in the tested joins.
- All canonical snapshot references resolve, with the documented `override`
  exception. Published placements have contiguous, unique ordinal positions.
- Placement role references are consistent; retained partner links are mutual.
- The 1,149 nameless WDR entries remain anonymous. Masked names do not appear
  as literal asterisks in the entry name field.

These checks establish internal consistency and traceability. They do not
establish agreement with every source score sheet or verify inferred identities.

## Confirmed defects and losses

### 1. Callback outcomes need correction before analytical use

All **6,427 scoring.dance callback summaries say `eliminated`**, and all 209
callback rounds report zero promoted entrants. This includes 2,192 callback
rows for 1,648 entries whose `best_round` is `final`.

The adapter retains source row statuses such as `CB`, `Alt1`, and `Alt2` in
cell attributes, but the projector ignores those attributes and defaults to
eliminated. This is a verified implementation defect, not incomplete collection.
See [adapter.py](../src/swingset/sources/scoringdance/adapter.py) and
[`_outcome`](../src/swingset/project/contests.py).

Separately, **37 WDR callback summaries across seven rounds and four events**
disagree with their published individual marks. Individual marks accumulate
across tables while summaries are overwritten for the same entry. The data
disagreement is verified; that overwrite behavior is the likely mechanism.

WDR's published callback outcomes are also selective: all 3,759 are promoted.
Unknown `S<n>` outcomes are deliberately withheld. These rows cannot serve as
the denominator for a promotion rate.

### 2. Registry-to-results reconciliation cannot currently succeed

All **21,230 registry placements have null `event_id`**. Their series IDs use
`wsdc-<number>`, while event series IDs use `slug-<name>`; there is zero overlap.
The linker compares those IDs for equality, so every result placement has null
registry points, false registry confirmation, and null expected-points agreement.
There are already 345 registry rows with exact case-insensitive event-name and
month matches, showing that missing collection is not the sole cause.

A second implementation mismatch blocks expected field-size calculation:
`entries.rounds_danced` stores round types such as `prelim`, but the linker
searches it for full round IDs. Both defects need correction; finishing the
sweep alone will not repair them. See
[registry projection](../src/swingset/project/registry.py) and
[link service](../src/swingset/link/service.py).

### 3. Registry projection loses evidence and hides conflicting source rows

For the exact snapshots backing the 2,228 published dancers, captured
observations contain 23,428 placement rows:

- **2,048 rows are omitted** because the division vocabulary lacks `PRO`
  (1,958 rows) and `TCH` (90 rows), affecting 273 dancers. Their meanings need
  verification before normalization.
- Of the remaining rows, 150 collapse into existing primary keys, yielding
  the published 21,230. There are 145 duplicate-key groups; **124 disagree on
  result or points**. The scope writer retains the last record without a
  conflict finding. Distinguish source duplicates from separate competitions
  and corrections before selecting a winner.
- There are also 668 findings for the unrecognized level spelling `Advance`.
  That spelling occurs in dancer-level fields in the examined observations;
  it should not be counted as 668 omitted placements. Unknown levels currently
  fall back to `none`, which obscures uncertainty.

See [registry projection](../src/swingset/project/registry.py) and
[scope writer](../src/swingset/project/writer.py).

### 4. Event dates and identities need validation

Four EEPro events have start dates after their end dates: Countdown Swing
Boston, Spotlight New Year's Celebration, Swingcouver, and UCWDC Worlds.
The source gives December-to-January ranges ending in 2026; our parser assigns
2026 to both ends. Three of these events carry 477 placements in total.

City Of Angels **2026** is published under `2027-04-city-of-angels`, with 177
placements. The archived source page itself says April 29, 2027, so this is a
source contradiction that our pipeline accepts, not evidence for blindly
changing the year. Resolve it against trusted event evidence.

All 13 WDR event rows retain month-wide override dates. All 96 events with
results lack city and country metadata. The manifest's
`latest_event_covered = 2030-03-10` is a calendar horizon, not results freshness.

### 5. Participant and contest reconciliation remains incomplete

There are 1,852 WDR, 196 EEPro, and 31 scoring.dance groups with the same
contest, role, and normalized name but multiple entry IDs. These are review
candidates, not proof that every group should merge. The common WDR pattern
is a bib-based preliminary entry plus a name-based final entry. Counts by
entry ID and inferred round progression can therefore be inflated or split.

157 EEPro contest names include text such as competitor counts or tie-break
instructions. This contaminates contest identifiers and can separate rounds
that belong together. Of 403 conflict findings, 385 concern contest names and
18 concern entry names; the latter deserve individual review.

## Identity and parsing coverage

Only **6,596 of 50,120 entries (13.2%)** carry a WSDC ID: 5,840 confirmed by a
source-provided ID and 756 probable. A further 423 are possible matches and
43,101 are unmatched. Confirmed source IDs are not equivalent to independently
verified registry records: 5,270 confirmed entry rows refer to dancers not yet
present in our registry table.

Linked-entry coverage is 76.9% for scoring.dance, 2.1% for EEPro, and 1.0% for
WDR. The registry sweep is incomplete and is not a representative sample;
aggregate participant histories and comparisons across sources would be biased.

The review queue contains 2,220 unknown-value findings, 403 conflicts, 128
unsupported contests, 80 parse failures, 64 event-alias findings, 26 warnings,
and one missing-identity finding. These are findings, not independent bad rows
or an error-rate estimate. All 128 unsupported contests are EEPro (9.9% of its
1,296 contests). Parse failures comprise 73 scoring.dance pages with missing
expected links and seven EEPro pages without result tables. Empty/unpublished
pages and actual layout failures still need to be distinguished.

## Recommended use and repair order

Use this release for source-attributed lookup of individual placements and
inspection of supported score sheets. Pin the commit, check contest status,
handle couple and one-sided placements, and inspect relevant review items.
Do not yet use it for callback success rates, unique participant counts,
geographic comparisons, complete dancer histories, or registry-point validation.
`rounds.judge_count` represents a union of detected judge columns; it is not a
reliable per-entry voting-panel denominator.

Repair in this order:

1. Scoring.dance callback state and WDR aggregate/mark agreement; replay
   archived inputs and validate against source row status and mark sums.
2. Registry series/event mapping, round-type field-size lookup, division/level
   vocabulary, and duplicate-key conflict handling.
3. Cross-year dates and contradictory edition metadata; add checks before
   publishing dates and event IDs.
4. Contest-heading extraction and participant reconciliation, using evidence
   rather than merging solely on names.
5. Finish collection and triage unsupported/failed pages, then measure coverage
   against a defined historical event cohort. Audit identity precision on a
   labeled sample after registry completion.

No numerical accuracy claim is justified yet: this audit checked all published
files and selected semantic relationships, traced defects through code and
archived evidence, and used an independent reviewer. It did not manually label
a representative sample of source results or identity assignments.

Local reproducibility artifacts are in
[`tmp/data-quality-2026-09-10/`](../tmp/data-quality-2026-09-10/): the pinned
snapshot, `audit-context.json`, `audit.py`, `metrics.json`, `extra-metrics.json`,
`semantic-checks.json`, `raw-evidence.json`, and `registry-projection-loss.json`.
Run `.venv/bin/python tmp/data-quality-2026-09-10/audit.py
tmp/data-quality-2026-09-10/snapshot` to repeat the main snapshot checks.
These ignored local artifacts are not distributed with the repository.
