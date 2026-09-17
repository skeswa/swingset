# Retained event pages and bounded turns

Date: 2026-09-16 UTC  
Type: Implementation outcome in progress  
Topic: H14 event-completion extension  
Related decisions: [D-0009](../../decisions/0009-retained-source-event-enumerations.md), [D-0010](../../decisions/0010-bounded-event-turns.md), [D-0011](../../decisions/0011-explicit-event-evidence-drilldown.md)

## What changed

The working source can reconstruct page obligations from admitted parent
interpretations without fetching anything. Each source event retains an
enumeration, its supporting generations and snapshots, distinct requests, and
membership changes. Registry profiles and calendar references do not become
events. An untrusted omission cannot reduce an event's denominator.

Within the existing host and class selection, a durable event queue grants
bounded turns. New arrivals join behind waiting events; blocked events keep
their place. Every issued request in a scheduler-selected fetch consumes its
turn, including robots checks, redirects, retries, and failures. The actual
host still receives exactly one debit. Canonical event matching does not own
queue identity or discovery age.

The working implementation reserves a share of the existing new-work class for listed
result requests. Its initial target is 50 percent, with borrowing when either
side has no eligible work. Credit and request-purpose receipts survive restart;
robots, redirects, and retries use the same selected side and actual host debit.
The shared parser-purpose map distinguishes event directories from results,
including EEPro autoindexes and WDR JSON requests.

Doctor and summary expose the retained membership catalog. A drill-down with
`--source SOURCE --source-event SOURCE_REF` checks the event's actual local
artifacts. Missing or corrupt files reopen their stage. Membership counts in
the ordinary catalog do not establish acquisition, interpretation, or publication.

The drill-down also explains recorded request service: turn selection and
captured policy, usage, actual host charges, capacity borrowing, and recent
outcomes. Totals include all event-owned receipts; detail is bounded to 20
requests. Shared requests are charged only to their selected owner. An issued
request does not advance the reported completion stage or eligible age. This
addition has focused validation after the full-suite receipt described below.

Current request-blocker facts now show overlapping source, watch, operator,
host-budget, cooldown, and parsing-backlog conditions. The actual request host
comes from the archive URL when present. No grant is acquired, and the output
explicitly leaves historical dispatch, capture, robots and other gates
unassessed; no eligible-age or eligibility claim follows from an empty list.

Body verification in the drill-down now hashes decompressed 64 KiB chunks
instead of allocating the entire HTML body. It retains the same digest and gzip
integrity checks, and does not change extract JSON validation. This bounds a
read buffer, not total bytes or execution time. The separate bounded watermark
observer described below implements the first slice of
[the expansion investigation](event-expansion-watermark-2026-09-16.md).

Migrations 16 through 18 retain enumeration history, event queue positions,
captured policies, per-request ownership, and new-work capacity credit.
Existing schema-14/15 doctor reads report
the event inventory as unavailable rather than migrating it.

Schema 19 adds conservative observations for new-event admission. An event
enters pressure after issued event-specific work, retained event evidence, or
admitted page obligations. Unfetched indexes alone cannot create an initial
deadlock. Refresh rotates through bounded page batches without source requests
or artifact recovery. Partial or unknown evidence retains pressure; verified
known pages can reduce it without asserting whole-event completion.

The initial per-host high/low thresholds are 100/80 started events, with
24-hour observation expiry, eight events refreshed per cycle, and 32 page
members per probe. These are proposed values, not measured acceptance. Existing
host/class limits, protected listed-page capacity, essential discovery, and
started-event continuations remain effective. Selection and the manual request
gate both enforce new-event deferral before charging a request.

Observations are fenced by parent dependencies, admission and artifact changes,
enumeration, policy, and accepted input bundle. Verified restore activation
recovers abandoned admissions with publication uncertainty intact, then
invalidates observations before removing the restore barrier. Request usage,
turns, and retained checkpoint bytes are preserved. Oversized evidence can stay
unknown indefinitely; retained historical host associations can over-defer an
old host after a URL move. See
[D-0018](../../decisions/0018-gate-expansion-with-conservative-observations.md).

## Validation

Focused checks cover additions and removals, shared requests, alias changes,
lost queue hints, restarts, body and extract loss, revoked evidence, direct
result payloads, and altered generation payloads. The tests use retained
scoring.dance fixtures as well as isolated synthetic fixtures; any fixture
admission occurs only in temporary test databases.

The real event fixture lists 12 round requests. The inventory initially reports
zero acquired and interpreted pages. Admitting one retained round advances
both counts to one; it leaves publication unknown. Losing the parent artifact
invalidates its support without erasing the 12-page denominator.

A fake-clock cohort of 33-, 65-, and 9-page events drains under continuous new
arrivals within 300 issued requests. An exhaustive mocked fetch exercises 80
wire requests in one redirect/robots/retry chain. With three requests already
used in a four-request target turn, that proves the conservative 83-request
turn bound. This is a finite test bound, not measured operating service.

The first combined run passed 1,426 tests and failed 23. Twenty-two failures
came from historical migration fixtures assuming the latest runtime was still
schema 15. Those fixtures now pin their intended schema, preserving the
production helper's exact-source guard. The remaining failure exposed seven
missing foreign-key child indexes in the new tables; the migrations now add
them. All 66 targeted migration checks pass. The second full run, including the
protected-capacity increment, passes all 1,486 tests in 323.17 seconds. Its
validation receipt records hashes for 341 source, migration, test, and dependency
files. See
[D-0013](../../decisions/0013-preserve-historical-migration-tests.md).

The third combined run adds service history, current blocker reporting, streaming
body verification, source-event query indexes, and capacity restore checks.
All 1,524 tests pass in 320.08 seconds. Independent code reviews found no material
issues in the blocker report or streaming verifier within their stated limits.

Ruff and mypy pass; mypy now checks 182 source files. An initial Ruff failure was
import ordering in the new retained-fixture test; its corrected rerun and focused
test pass.

Two checkpoint regressions pass: restoring preserves the enumeration and a
partially used turn, and deleting only the restored extract reopens interpretation
without refunding requests. A schema-15 restore remains unchanged and reports
inventory unavailable. These tests do not establish H18 operating acceptance.

## Retained demand observation

The schema-19 integration first passed 1,572 tests and failed seven. The gate
misclassified retained continuations while bounded metadata enrollment lagged;
it now recognizes retained event evidence and admitted obligations directly.
Historical wait/redirect tests now settle the independent metadata gate during
fixture setup, as a cycle does. One pause test assumed an operation order that
changed when artifact observation was added.

The corrected source-bound run passes all 1,582 tests in 335.58 seconds, Ruff,
and mypy over 185 source files. It includes interrupted restore activation with
an abandoned publication reservation. Source hashes were unchanged throughout
that run. These checks establish local behavior only; thresholds remain
unmeasured and production remains held.

A read-only query of the original H15 checkpoint took 0.053 seconds and found
6,318 event-associated watch rows across 468 source-event references. Of these,
3,757 scoring.dance result watches across 218 references have neither a recorded
check nor a retained response. EEPro and WDR have no watches in that category.

These are watch-row counts, not distinct admitted page obligations or verified
completion. The query did not check artifacts, admissions, current eligibility,
or the live worker. References need not correspond one-to-one with canonical
events. The checkpoint manifest matched the earlier verified replay preparation;
this small query did not rehash the database. It made no network requests or
writes. This observation supports investigating retained result demand but does
not calibrate the turn size, capacity share, or unfinished-event watermark.

An offline rehearsal then used those 218 event sizes and 3,757 watch rows as
synthetic demand for the actual scheduler and request ledger. At the initial
four-request turn and 50-percent listed-page share, the fixed cohort drained
after 7,513 issued requests: 3,757 results and 3,756 discovery requests. The last
event's first service was request 1,689; the largest request gap, including
initial wait, was 1,690. New discovery remained continuously eligible.

The model assumes one successful request per watch, synthetic membership, one
host, and one work class. It instantiates no HTTP client and verifies no source
interpretations or artifacts. Counts may duplicate distinct source requests.
The five-second fake clock is not an operating forecast. This finite rehearsal
supports the local rotation and share behavior; it does not establish ordinary
host service, release completeness, or an unfinished-event watermark.

Independent review recomputed the cohort sizes, request totals, and service-gap
maxima. The model keeps only the next result watch per event outstanding, so it
does not measure a scheduler scan over all 3,757 watches at once. Its request
control context uses the round kind for discovery too; capacity selection reads
the correct stored parser, but parser-specific controls and historical dispatch
are outside this rehearsal.

## Blocker history and release evidence

Schema 20 retains bounded observations of blocker changes and the captured
inputs behind them. Observations use a fixed catalog boundary per pass, so
continuing discovery cannot starve existing events. Bookkeeping can record an
all-work pause without starting paused artifact or derivation work. These are
observed changes, not proof of continuous eligibility between samples.

Release closure now pins source-event enumeration membership and parent
evidence. Coverage reports the known listed denominator, selected interpreted
support subset, and pages represented in emitted results. Full cutoff-local
stage counts remain unknown. Publication reporting reads one acknowledged,
verified baseline and binds its receipt to the candidate, closure, and cutoff.
It never adopts a newer local candidate or sums overlapping transport rows.

The source-bound reporting run passed 1,601 default-discovered tests in 319.18
seconds, Ruff, and mypy over 188 source files. A subsequent discovery audit found
that pytest's default exclusions skip `tests/build`, which held another 145
tests at that point. Focused build tests had run separately, but the default
count was not exhaustive. The setting is now corrected; a new combined run
including that directory is recorded below. Retain the earlier receipt as evidence
for the tests it actually ran, not as current full-suite acceptance.

The corrected collection and combined suite passed all 1,802 tests in 345.53
seconds, Ruff, and mypy over 189 source files. Source and test hashes were
unchanged throughout the run. This includes both event-preservation repairs,
strict publication receipt binding, and the unexposed cutoff-local verifier.
The verifier checks exact source and artifact support, searches alternative
watch anchors for aggregate generations within fixed budgets, and returns
unknown when its candidate universe cannot be exhausted. It is not yet release
or scheduling authority.

After that combined run, an additional offline restore/resume regression passed
with the 56-test checkpoint, pressure, and blocker subset. It activates a
disposable checkpoint using an offline empty-head stub, preserves paid partial
turns and request ledgers, resumes pending enumeration and interpretation, and
respects operator pause, cooldown, and daily-budget gates. The restored and
uninterrupted branches finish with equal local inventory, projected result rows,
and host usage. Checkpoint files remain unchanged. The
[focused receipt](../../evidence/runtime/event-completion-2026-09-16/restore-convergence-validation-001.json)
does not establish full cycle/link/build/publication convergence or V7 activation.

## Artifact-backed coverage and observed progress

The schema-21 integration passed all 1,870 collected tests in 413.76 seconds,
Ruff, and mypy over 197 source files, with unchanged source and test hashes.
Its [receipt](../../evidence/runtime/event-completion-2026-09-16/event-progress-validation-005.json)
includes the shared artifact verifier, exact release-local observations,
per-boundary file validation, atomic successful-output facts, and bounded
qualified progress. Capture records the actual injected observation clock,
separately from the release cutoff, and pins its resource policy.

A subsequent review found that metadata exhaustion could reset a saved page
cursor and malformed observation tokens could crash reporting. Both are fixed.
The final [affected-suite receipt](../../evidence/runtime/event-completion-2026-09-16/event-progress-followup-validation-006.json)
records 123 passing tests plus Ruff and mypy with unchanged source hashes.
It supplements the preceding exhaustive run; it is not another full-suite run.

Progress facts survive checkpoints and commit atomically with their successful
outputs. A sampled progress receipt additionally requires a missing baseline
and verification of the exact later operation. Restored old files alone do not
count as new operation progress. Parent support is explicitly unassessed by
this observer, and enumerations over 128 members remain unassessed.

## Ready-backlog policy comparison

The second offline model starts with all 3,757 modeled watch rows ready across
218 source references, while discovery continues. Each policy runs for 512
issued requests using the real scheduler, request gates, and accounting.
Membership and successful outcomes are synthetic; these watch-row sizes do not
establish distinct admitted page obligations.

| Requests per turn | Listed-page share | Listed requests served | Events served | Events finished | Events still unserved |
| ----------------- | ----------------- | ---------------------- | ------------- | --------------- | --------------------- |
| 1                 | 50%               | 256                    | 218           | 1               | 0                     |
| 4                 | 50%               | 256                    | 65            | 2               | 153                   |
| 8                 | 50%               | 256                    | 33            | 1               | 185                   |
| 1                 | 75%               | 384                    | 218           | 2               | 0                     |
| 4                 | 75%               | 384                    | 98            | 5               | 120                   |
| 8                 | 75%               | 384                    | 49            | 1               | 169                   |

The one-request policies reached every modeled event by request 435 at a
50-percent share and request 291 at a 75-percent share. The other runs ended
before every event received service; their full-cohort first-service times
remain unknown. These finite prefixes show the early-service tradeoff, not
whole-backlog completion or ordinary-host throughput. Synthetic host and
pressure limits leave those gates open. No operating defaults changed.

All six runs passed, with unchanged source hashes and 512 entries in each of
the three request ledgers and the host-budget total per run. Matching totals
do not independently establish action-by-action attribution. The experiment
took 1,106.36 seconds on the host. An independent result check recomputed
per-event aggregates, protected shares, and script/source hashes. The retained
[report](../../evidence/runtime/event-completion-2026-09-16/ready-backlog-shadow-001.json)
has SHA-256 `4ddbdcf901fc1983f0adfd2ea8bc1a18516256a8ee199f12f56ceb7e7e37f6dd`.

## Limits and next work

The operator hold remains. This code has not been deployed, and it is outside
the original H16 release's frozen source. Existing captured-document terminal
markers do not prove whole-event pagination, so that completeness stays unknown.
Legacy service ages and successful-progress times are not invented.

The initial watermark implementation still needs retained-demand calibration
and operating acceptance. Fleet-wide verified progress, eligible-time alarms,
unavailable/unsupported release classification, and acceptance of published
source-event coverage remain pending. The bounded verifier leaves large or
unassessed local stage totals null. Initial turn values
have finite retained-backlog shape comparisons, but still need observed service
under ordinary limits. V6 and V7 acceptance remain open.

A read-only scenario audit also identified four concrete gaps for follow-up:

- Historical dispatch offers only its first eligible page to the scheduler;
  competing historical events need to participate in rotation together.
- The fresh doctor inventory still searches declaration watches and their
  anchored generations. It can miss aliases and aggregate support recognized
  by the shared verifier.
- Enumeration survives deleted queue hints, but acquired, uninterpreted
  children do not yet reconstruct lost parse work while preserving retry gates.
- Fleet reports do not yet summarize evidence-backed finished, reopened, and
  explicitly retired events. Catalog counts and pressure hints cannot supply
  those completion transitions.

The historical-offer gap is repaired locally: all eligible candidates from the
finite retained plan enter ordinary selection together. Newest-first plan order
still enrolls new demand, while stored event turns decide among waiting owners.
Offering remains read-only, archive-first source precedence is unchanged, and
dispatch repeats the original gates. Seventy-one focused history, origin,
cycle, and event-turn tests pass, including a real mocked-transport cycle that
offers two historical candidates before interpretation begins. Ruff and mypy
also pass for the changed modules. Integration acceptance remains separate.

A further rotation regression uses admitted membership for two waiting events
and a third arriving event. It checks bounded consecutive service, newcomer
placement, and ten request debits. The doctor now uses the shared bounded
verifier for enumeration, parent support, aliases, and aggregate evidence.
Unknown or incomplete evidence leaves stage totals null, and response
diagnostics respect the assessment cutoff. Seventy-four focused doctor tests
passed; the combined history, inventory, restore, progress, turn, and local
release-coverage selection passed 243 tests in 23.13 seconds. These runs follow
the source-bound exhaustive result above; they do not replace a final combined
validation of the subsequent queue-recovery integration.

Lost parse-hint recovery is integrated at cycle bookkeeping, before ordinary
work selection. A real paused cycle reconstructs the missing hint without
executing it or issuing requests. Independent review found two follow-ups:
recognize a durable explicit retry for a failed snapshot whose replacement
hint was lost, and reject cursor integers outside SQLite's rowid range.
Both fixes passed 91 focused tests. The combined validation then ran all 1,933
tests: 1,930 passed and three failed. Two direct report fixtures lacked the
query-only read transaction already used by production doctor; the third
mocked the old single historical offer rather than the new offer list. These
fixtures are corrected without runtime changes; 54 focused tests pass. Ruff
and mypy over 198 source files passed in the combined run, with source hashes
unchanged. The failed receipt remains
[event-recovery-validation-007.json](../../evidence/runtime/event-completion-2026-09-16/event-recovery-validation-007.json).
The final exhaustive rerun passed all 1,933 tests in 401.21 seconds, Ruff, and
mypy over 198 source files. The source and test hashes remained unchanged.
See [event-recovery-validation-008.json](../../evidence/runtime/event-completion-2026-09-16/event-recovery-validation-008.json)
and
[D-0028](../../decisions/0028-reconstruct-retained-snapshot-parse-hints.md).

## Evidence

- [Combined tests](../../evidence/runtime/event-completion-2026-09-16/combined-tests.log).
- [Ruff rerun](../../evidence/runtime/event-completion-2026-09-16/combined-ruff-002.log).
- [Mypy](../../evidence/runtime/event-completion-2026-09-16/combined-mypy.log).
- [Retained fixture rerun](../../evidence/runtime/event-completion-2026-09-16/fixture-test-002.log).
- [Corrected migration checks](../../evidence/runtime/event-completion-2026-09-16/migration-tests-002.log).
- [Checkpoint regressions](../../evidence/runtime/event-completion-2026-09-16/restore-tests.log).
- [Second combined run](../../evidence/runtime/event-completion-2026-09-16/combined-tests-002.log).
- [Combined validation receipt](../../evidence/runtime/event-completion-2026-09-16/combined-validation.json): SHA-256 `240a4cfae9ff39fb306182821a8ac166be5d1b70ba44f2da3996e017f5b87fca`.
- [Latest Ruff](../../evidence/runtime/event-completion-2026-09-16/combined-ruff-003.log) and [mypy](../../evidence/runtime/event-completion-2026-09-16/combined-mypy-002.log).
- [Retained watch demand](../../evidence/runtime/event-completion-2026-09-16/watch-demand.json): SHA-256 `5504f1e2e813ff260b6d6eef2bdd695cb110791e60d078b2b7bbdef9d5ff1ff6`.
- [Demand-shape rehearsal](../../evidence/runtime/event-completion-2026-09-16/demand-shape-rehearsal.json): SHA-256 `41f6c53446680f56dcd54c4d3ecbaee28c6b293be030e1741e89fe7824b7bf5d`.
- [Third combined run](../../evidence/runtime/event-completion-2026-09-16/combined-tests-003.log) and [source-bound validation receipt](../../evidence/runtime/event-completion-2026-09-16/combined-validation-002.json).
- [Latest reporting Ruff](../../evidence/runtime/event-completion-2026-09-16/combined-ruff-004.log) and [mypy](../../evidence/runtime/event-completion-2026-09-16/combined-mypy-003.log).
- [Expansion integration failure record](../../evidence/runtime/event-completion-2026-09-16/combined-tests-004.log).
- [Corrected expansion tests](../../evidence/runtime/event-completion-2026-09-16/combined-tests-005.log) and [source-bound validation](../../evidence/runtime/event-completion-2026-09-16/pressure-validation-002.json).
- [Reporting tests before the discovery correction](../../evidence/runtime/event-completion-2026-09-16/combined-tests-006.log) and [source-bound receipt](../../evidence/runtime/event-completion-2026-09-16/event-reporting-validation-003.json).
- [Corrected exhaustive collection](../../evidence/runtime/event-completion-2026-09-16/combined-collection-007.log), [1,802-test run](../../evidence/runtime/event-completion-2026-09-16/combined-tests-007.log), and [source-bound validation receipt](../../evidence/runtime/event-completion-2026-09-16/event-preservation-validation-004.json).
