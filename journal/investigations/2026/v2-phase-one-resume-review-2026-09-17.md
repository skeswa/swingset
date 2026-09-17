# V2 phase-one resume review, 2026-09-17

Scope: offline review of the retained phase-one export and the latest derived
per-year review. A later parser-8 scratch export is recorded below. No
third-party requests, production writes, source changes, year acceptance or
publication occurred. This review does not change the V5 gate.

The [machine-readable reconciliation](../../evidence/admission/v2-phase-one-resume-review-2026-09-17/report.json)
records exact input hashes, the 17 pending targets and row-level checks. Its
source is the retained export at
[`year-review/years.csv`](../../evidence/collection/phase1-review-2026-09-17/year-review/years.csv);
its year details are in
[`current-review`](../../evidence/collection/phase1-review-2026-09-17/current-review/README.md).

## Current gate

No year from 2010 through 2026 is ready for owner review or recorded accepted.
Every year has event findings, and each has a phase-one-incomplete finding.
Backfill acceptance requires complete phase-one evidence, no unresolved event
findings, and an explicit owner decision. Existing local review does not supply
that decision.

The export contains 213 catalog targets: 155 parsed, 33 empty, 17 pending, four
with parse findings, and four duplicate entries. The 17 pending rows are recent
WSDC `events` and `print-event-list` captures dated 2025-02 through 2026-08;
their IDs and exact URLs are preserved in the reconciliation. They have catalog
metadata but no body interpretation. Acquisition remains a production parse
backpressure task. Do not count their Archive timestamps as event dates or
assume every pending capture changes every historical year.

The four captured-body parse failures are the 2016 `events-map` snapshots from
July, September, November and December. The [calendar gap review](calendar-map-gaps-2026-09-17.md)
found event-name-only marker popups without printed dates. Their capture years
do not supply event years. Keep their findings until a reviewed representation
preserves the undated listings and independent dated evidence resolves them.
The fresh parser-8 export has 58 parse warnings: malformed or missing listing dates, prose
approval notices needing event interpretation, and newsletter status/color
fallbacks. They are retained-body work and can be reconciled offline where
independent evidence in the captured fixtures supports a precise conclusion.

## Per-year evidence

The latest export has 2,428 event rows for 2010–2026. Of these, 2,007 have day
precision and 421 have month precision. Month precision is allowed by the
backfill contract and is counted in the year record; it is not itself an
acceptance defect. There are 517 listing-only rows and 23 cancelled rows. All
are represented with explicit `held` states. The 1,888 registry occurrence
rows reconcile to the year summaries, and the exported event IDs are unique.
These checks validate the retained export structure, not the truth of every
listing interpretation.

The 980 year-owned findings comprise 924 unresolved series aliases, 39 event
aliases with multiple dated editions matching a registry occurrence, and 17
phase-one-incomplete findings. The alias report has 658 printed-name groups;
643 have no registry candidate and 15 have multiple candidates. These counts
are triage totals, not alias decisions. Candidate identity must be supported by
retained source evidence; ambiguous cases remain findings until a human resolves
them. The fresh report also retains 58 parse warnings and four parse failures owned
at the source parse level.

| Year | Events | Day / month | Listed / cancelled | Year findings | Alias groups | Recorded accepted |
| ---: | -----: | ----------: | -----------------: | ------------: | -----------: | :---------------- |
| 2010 |     94 |     51 / 43 |             14 / 0 |            32 |           30 | no                |
| 2011 |     87 |     30 / 57 |              2 / 0 |            19 |           17 | no                |
| 2012 |    104 |     70 / 34 |             13 / 0 |            39 |           34 | no                |
| 2013 |    114 |     71 / 43 |              7 / 1 |            32 |           27 | no                |
| 2014 |    125 |     57 / 68 |              8 / 0 |            25 |           22 | no                |
| 2015 |    139 |    102 / 37 |             15 / 1 |            36 |           30 | no                |
| 2016 |    151 |    120 / 31 |             16 / 1 |            35 |           29 | no                |
| 2017 |    163 |     157 / 6 |             15 / 0 |            56 |           52 | no                |
| 2018 |    161 |     159 / 2 |             14 / 0 |            40 |           38 | no                |
| 2019 |    173 |     169 / 4 |             17 / 4 |            55 |           50 | no                |
| 2020 |    114 |     111 / 3 |             80 / 3 |           131 |          130 | no                |
| 2021 |     75 |      74 / 1 |             48 / 1 |            92 |           90 | no                |
| 2022 |    121 |     118 / 3 |             29 / 0 |            48 |           45 | no                |
| 2023 |    158 |    148 / 10 |             31 / 6 |            62 |           56 | no                |
| 2024 |    188 |     181 / 7 |             45 / 4 |           140 |          131 | no                |
| 2025 |    245 |    212 / 33 |             72 / 1 |            94 |           88 | no                |
| 2026 |    216 |    177 / 39 |             91 / 1 |            44 |           41 | no                |

The latest review differs from the older retained-export README for 2026: it
records 91 listed and one cancelled event, rather than 92 listed and none
cancelled. Use the newer `year-review/years.csv` and `current-review` pages for
reconciliation. The exported events also contain 113 future listings in
2027–2030; those are outside V5's 2010–2026 year-acceptance scope.

## Offline work that can proceed

1. Reconcile parse warnings against the frozen captured bodies, matching dates,
   approval notices and status evidence only where a separate retained source
   supports it. Keep uncorroborated dates open; do not convert malformed date
   strings by guess.
2. Resolve series aliases from retained registry/listing evidence. Prepare
   evidence-backed proposals for single-identity cases; preserve all 15
   multi-candidate groups as unresolved until human review.
3. Review the 39 event-alias findings against occurrence month, series identity
   and independent dated listings. Do not merge or choose among multiple dated
   editions without evidence.
4. Preserve the four map marker gaps as explicit undated listings and seek
   independent date matches in already retained captures. Their body contents
   alone cannot yield dates.
5. Once parse capacity permits, acquire and interpret the 17 pending bodies
   through the reviewed phase-one runner and shared Archive controls. Then
   rebuild the year review from a fresh retained export and rerun these checks.

None of these steps grants year acceptance. After findings are cleared and the
review is complete, the owner must explicitly accept each year before its phase
two watch can be created.

## Newsletter parser-8 replay helper

The exact 28 retained newsletter captures now have a narrow offline replay
helper at
[`replay_phase1_newsletter_parser8.py`](../../tools/admission/replay_phase1_newsletter_parser8.py).
It is implemented and locally tested. Seal-only attempts exercised disposable
schema-29 database copies and produced a reviewed before-state seal. The first
sealed execution rolled back at the exact expected-after comparison before
writing a receipt or derived ledger. It left the exact recovery marker as
designed. After the scratch prestate was reverified across all 117 tables, a
diagnostic-only helper revision exposed bounded mismatch paths without changing
the comparison. A new seal passed independent review and its execution matched
all 117 expected tables. The compact [run receipt](../../evidence/runtime/phase1-newsletter-replay-2026-09-17/attempts-001/receipt.json)
retains the failed chain, successful receipt and successor ledger. No attempt
changed production, year acceptance or publication.

The helper binds candidate 006, packet 005, checkpoint 004, the accepted
input-004 bundle, the unchanged 213-row catalog, the predecessor ledger and the
28 exact target/snapshot pairs. It rejects the original input-004 scratch. A
fresh disposable scratch must carry a new packet-005 prepare/accept pair and
match the exact immutable authority in the pinned input-004 scratch marker.
The helper rechecks the marker's protected table hashes, accepted bundle files
and absence of any run after preparation. A marker or metadata string cannot
assert its own authority.

The helper verifies every retained body and denies the full Python `socket`
audit namespace plus subprocess, system, spawn, fork and exec routes. It also
requires the newsletter admission policy to remain in shadow mode. All 28
parser changes and rebuilt `phase1_year` findings share one SQLite transaction.

A separate `--seal-only` step copies the database to an exclusive temporary
SQLite file under `/var/tmp`, fixes one replay clock and performs the complete
replay against that copy. It seals full hashes
for the unchanged before state and the exact expected row state of every SQLite
table. The original database remains unchanged. Execution requires an
independently reviewed SHA-256 for that seal and repeats the replay with the
same clock. Any row difference from the sealed derivation rolls back the whole
transaction.

Before the real transaction, execution writes a recovery marker whose exact
bytes include the entire reviewed seal and its SHA-256, including source,
packet, checkpoint, input, catalog, ledger, 28-pair, before-invariant,
before-table and expected-after-table authority. It never reads authority back
from marker-authored values. The predecessor ledger remains byte-exact. After
the database commit, the helper writes a new sealed parser-8 ledger and then its
receipt at fixed scratch-local paths. A resumed run accepts only the exact
sealed before state or the exact sealed committed state. It rejects a missing
marker after commit, a mixed state, marker tampering or any changed output.

The focused suite has 61 passing tests, including adversarial DNS/process
routes, fabricated and operated scratches, recovery-authority tampering, an
exact committed replay resume, second-target rollback and arbitrary mutations
in every allowed SQLite table plus event stage, cursor and subject state. Ruff
check and format check pass, and mypy reports no issues for the helper. These
local checks establish the helper contract only. The exact seal and completed
packet-005-derived scratch replay also passed independent review: all 117 table
hashes matched, 28 ledger targets changed, 185 remained exact, and the successor
ledger records 20 parsed and eight empty targets with 731 observations. All 28
generations remain in shadow `needs_review`; the 17 unacquired targets remain
pending. A fresh year-review export from this scratch has now passed as
described below.

## Fresh parser-8 year review

The dedicated offline exporter ran on the exact completed schema-29 scratch.
It bound candidate 006, the replay receipt, successor ledger and streamed hashes
for all 117 application tables. It used a WAL-aware private database copy and
left the 5,018,693,632-byte scratch SQLite file exact at SHA-256
`8cbe2f932de4391d9156093267067298db8d343ffad95b37dfc9eb462a0ffe6c`.
The first invocation failed before it created an output because the pinned
candidate import path was not injected soon enough. The corrected helper then
passed independent source review and completed under a private network
namespace. The compact [export receipt](../../evidence/runtime/phase1-year-review-2026-09-17/receipt.json)
preserves the failure and successful output hashes.

The event, occurrence and series-review files are byte-exact with the older
review. The 2,428 events in 2010–2026, 113 later listings, 1,888 occurrences,
924 series aliases, 39 event aliases, 17 pending captures and four parse
failures are unchanged. Parser 8 removes four obsolete approval-notice warnings
and refines four colour-warning descriptions, leaving 58 parse warnings and
1,042 total findings. Every 2010–2026 row still has findings, is not ready for
owner review and records `events_accepted=False`. The exporter made zero
network requests, production operations, year acceptances or publications.

The retained April 9, 2018 newsletter has one reviewed URL canonicalization.
Its catalog and ledger retain the exact `http://www.worldsdc.com/...` URL, while
the bound watch and snapshot retain the same host and path under `https`. The
helper pins both complete URLs to target `0226ef50385bfa517c9ef79c`. The other
27 targets still require exact catalog, ledger, watch and snapshot URL equality;
another scheme, host or path difference fails the seal.

The retained newsletters use the ordinary admission unit identity: the unit key
is the watch ID. Only a qualifying Wayback index capture uses the separate
`watch_id/snapshot_id` identity. Promotion proof now applies that same routing
rule. It requires the newest generation for each unit to carry the sealed replay
run ID and fixed creation time, parser 8, and the exact target snapshot in its
recipe. Under the pinned shadow policy, the source-unit accepted pointer must
remain empty. The retained generation state may be `staged`,
`waiting_for_inputs`, or `needs_review`; the helper recomputes the only valid
state from the immutable report failures and requires the row and report to
agree. The real retained report state is `needs_review`.

Historical non-current snapshots can complete parser 8 without replacing the
watch's materialized observations. Even a current snapshot can retain parser-6
rows when parser 8 produced the same semantic result, because observation
storage does not rewrite evidence solely to change a version field. The helper
therefore derives each updated ledger status and observation count from the
replay generation's immutable parser-8 `result_json`, bound to the exact
snapshot by its recipe and one-item manifest. It records that result's hash and
a separate hash of any materialized rows for the target snapshot. The sealed
full-table before/expected-after hashes protect all older and non-target
observations. Missing result structure, a foreign manifest snapshot or extra
materialized database changes fail closed.

The dry derivation uses an exclusive mode-0700 directory and mode-0600 SQLite
file under the required `TMPDIR=/var/tmp`. SQLite backup copies the read-only
scratch database to that disk-backed file; the fixed-clock replay runs only on
the copy. The helper rejects linked or substituted paths and removes the copy,
WAL and SHM files after success or failure. Full-table seals stream every row in
declared primary-key order followed by complete-column binary tie-breakers.
Those tie-breakers rank null, integer, real, text and blob storage classes
explicitly and override declared text collations. Thus nullable text primary
keys and `NOCASE` values have the same order across reverse insertion. Their
SHA-256 input includes the complete `table_xinfo` schema, row count and
length-delimited canonical value for every row, including blobs and nulls.
This keeps memory bounded without sampling or weakening before/after
comparison.

Float serialization follows SQLite equality for zero: `-0.0` and `+0.0`
produce the same canonical `0.0`. Positive infinity, negative infinity and any
NaN value SQLite can surface use explicit tagged encodings instead of invalid
JSON numbers. Integer and real storage classes remain distinct, and finite
nonzero real values retain their exact decoded value.

Parser replay may update only three cache columns on a target watch:
`current_observation_snapshot_id`, `extract_version` and `fingerprint`. The
invariant hashes every other target-watch column and every complete non-target
watch row. An ordinary target watch also owns its materialized observation
cache, including retained rows whose `snapshot_id` names an older non-target
snapshot. Those rows may change only as part of the exact dry-derived full-table
state; observations on every non-target watch remain invariant. Compact failure
messages name differing invariant hash paths without embedding table contents.

The exact replay and full after-table hashing run in one helper-owned
`BEGIN IMMEDIATE` maintenance transaction. Runtime database helpers see the
active transaction and use savepoints, so the ordinary 45-second worker write
deadline is never installed. The helper compares protected invariants and all
sealed expected-after table hashes before committing. Any `BaseException`
rolls back while the connection is still open; disk-copy cleanup closes it only
after that unwind. This exception applies only to this verified disposable
scratch maintenance path and does not change the runtime deadline.

Schema 29 maintains `work_generations` through triggers on `pending_work`
inserts and updates. Parser replay may therefore change a generation row only
when the corresponding target parse row or a project scope owned by the target
watch observations changes in the queue. Calendar and source-index scopes also
permit their documented `project/map/all` invalidation. The helper snapshots
both complete queue tables before replay and verifies that relation before
commit; every unrelated queue row must remain exact. The full expected-after
table seal still binds each permitted generation and retry field. An
unexpected-table failure reports only the unreviewed set difference.

Expected-after failures now name only the differing table count and hash paths,
bounded by the same compact path limit as other invariant failures. Equality is
still exact. Source inspection found deterministic content-derived generation,
observation and finding identifiers; the replay fixes Python and trigger clocks
and requires the same run ID. The newsletter event parser has no random branch.
A regression repeats the fixed replay on two exact database copies, including
queue-generation triggers, and requires identical full after-table hashes. The
next failed comparison can therefore identify the remaining real table without
loosening the seal or changing recovery-marker behavior.

## Reproduction and an unresolved alias example

The report was regenerated byte-for-byte with
[`generate_report.py`](../../evidence/admission/v2-phase-one-resume-review-2026-09-17/generate_report.py):
all eight structural checks passed and the rebuilt JSON matched the retained
report. Report SHA-256:
`c0efaf94ecdfa73899e0bc755fe36a2494df73dd0a24120679170aa568f473f3`.
No year state or source data was changed.

`Swing Fiction` is one clear example where retained evidence proves ambiguity.
The occurrence export assigns the 2023 July edition (`2023-07-swing-fiction`)
to `wsdc-331`, the 2024 June edition (`2024-06-swing-fiction`) to
`wsdc-331`, the 2025 June edition (`2025-06-swing-fiction`) to `wsdc-321`, and
the 2026 June edition (`2026-06-swing-fiction`) to `wsdc-331`. The 2024
registry name includes “2024”; the other occurrence names are “Swing Fiction.”
Because the alias override has no year key, a global alias for the exact name
would combine two registry identities. Keep this finding open. It needs a
representation that can express the evidence by year or edition before an
override can be safely proposed.
