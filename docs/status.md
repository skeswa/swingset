# Current status

Updated 2026-09-18. **Candidate 006 is deployed under hold at schema 29.**
[Candidate history](reference/candidate-history.md) compares freezes 001–006.
Candidate 006 passed 2,648 tests, Ruff and mypy over 225 source files, with all
2,939 frozen files verified before and after. Its exact NixOS build, service
bindings, checkpoint-004 packet, migration, restore and bounded scratch-input
rehearsals passed independent review.

The live preflight passed without migration. Candidate 006 was then activated,
and the guarded schema 28→29 migration passed at 23:11:59 UTC. All 116
predecessor application tables were unchanged; the only new table is the
one-row `history_dispatch_fence`. Both schema markers are 29, integrity and
foreign keys pass, and the independent postmigration audit found no blockers.
The active and persistent system since 2026-09-18 06:12 UTC is
`/nix/store/mni8kwh2472nrfljz1v6kydrvx7f2m70-nixos-system-swingset-lxc-25.11.20260630.b6018f8`,
which differs from candidate 006's system
`/nix/store/5d9nlyflv9d4gb89a5wayhiarj01znnh-nixos-system-swingset-lxc-25.11.20260630.b6018f8`
only by the `swingset-scratch-clean` timer and script; the swingset package and
its source `/nix/store/rgyryll4d55rgzscdqhjcwmkr325a76f-source` are unchanged.
The operator hold and seven sidecars are exact, all six ordinary units are
inactive, and unsettled admissions and pending publication are zero. See the
[preflight](../journal/evidence/runtime/held-schema29-migration-2026-09-17/preflight-001/receipt.json),
[migration](../journal/evidence/runtime/held-schema29-migration-2026-09-17/execution-001/receipt.json),
and [independent audit](../journal/evidence/runtime/held-schema29-migration-2026-09-17/postmigration-review-001/receipt.json).

The worker ran the Mac out of disk on 2026-09-18. Rehearsal scratch (142
directories, 238 GB) and eleven pre-H16 checkpoints (28 GB) were removed, the
machine is bounded to 256 GiB, and a daily timer now removes idle rehearsal
scratch. See the [investigation](../journal/investigations/2026/worker-disk-exhaustion-2026-09-18.md)
and [D-0133](../journal/decisions/0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md).

Steps 2 and 3 of the [bounded state plan](plans/bounded-state-and-archive.md)
are implemented and locally tested as of 2026-09-18. **Nothing is deployed.**
Production stays at schema 29 under the operator hold; no database has been
migrated and nothing has been removed, archived or reclaimed anywhere. Schema
30 makes findings declare the support they rely on, schema 31 records one
permanent note per removal, and schema 32 stores each distinct derivation output
row once behind a `derivation_rows` view. One retention walk produces the durable
and local lists; `gc --plan`, `gc --apply`, `gc --reclaim`, `swingset hold` and
doctor work from it, and no payload bytes can be removed until step 4's archive
tables exist. The full suite passes locally except one pre-existing,
environment-caused failure in `tests/publish/test_publication_controls.py`
explained in the [measurement investigation](../journal/investigations/2026/state-storage-measurement-2026-09-18.md#known-local-test-failure).
See the [retention contract](reference/state.md#retention),
[schema history](reference/schema-history.md), and
[D-0127](../journal/decisions/0127-measure-state-storage-read-only-on-a-copy.md)
through
[D-0158](../journal/decisions/0158-build-steps-2-and-3-before-the-measurement-and-gate-the-rest-on-it.md).

Step 1 of that plan, the measurement, **ran on 2026-09-18** against a scratch
restore of held checkpoint 004 on the worker, after steps 2 and 3 were already
written. Rows do not repeat yet (0.9998 distinct), `derivation_rows` and its
indexes are 27% of the 5.0 GB file, and migrating the copy to schema 32 made it
6% larger. [D-0166](../journal/decisions/0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md),
accepted on 2026-09-18, holds interning back, reordering the migrations so step 3 can deploy
alone, and adds a step for the row indexes and source generation JSON. The reorder
was done on 2026-09-18: interning is now schema 32, the last migration, and every
reader of the interned tables falls back to the one table, so schemas 30 and 31
deploy and run `gc`, holds, doctor, checkpoint and restore without it
([D-0167](../journal/decisions/0167-intern-derivation-payloads-in-the-last-migration.md)).
Interning itself still waits for a copy with recomputation history. See
the [investigation](../journal/investigations/2026/state-storage-measurement-2026-09-18.md).
Until that measurement the two retention defaults (8,000,000,000 bytes and a window
of 3) are judgment calls, not measured ones. The order actually taken, and what
still waits for the numbers, is recorded in
[D-0158](../journal/decisions/0158-build-steps-2-and-3-before-the-measurement-and-gate-the-rest-on-it.md);
it does not satisfy step 1's own "done when", which needs the measurement. Step 4, archiving to two object
stores and bringing data back, has not started; neither store exists.

Production input acceptance passed its separately reviewed three-gate operation
at 01:39:24 UTC. The exact 12 candidate-006 inputs changed within the existing
51-row accepted map; 112 of 117 application tables stayed exact, and only the
reviewed five-table transition occurred. The 4,931 named judges were preserved.
The target bundle is
`abdf538777c1e4fc05c7f8b9079701bd5a37f276cabe83a8be023673a941d644`.
Collection resumption, repair activation and a new dataset publication have not
occurred. The acknowledged dataset remains
`cand_8f31cad7226643ae` at commit
`2a6c7dc744fb36eabb5163c0a527d787d3721f4f`. Candidate-005 rehearsals remain
retained history; they do not certify candidate 006. Current checkpoint 004 is
locally verified and privately acknowledged, including all 20 Archive requests
and four DCN requests. See the
[successor evidence](../journal/investigations/2026/schema29-successor-rehearsals-2026-09-17.md#candidate-005-successor).
The dedicated candidate-006 input-acceptance helper is implemented, has 17
focused tests, passes Ruff and mypy, and passed independent source, preflight,
seal, execution-gate and post-execution review. See the compact
[operation receipt](../journal/evidence/runtime/held-schema29-input-acceptance-2026-09-17/receipt.json).

The bounded newsletter parser-8 replay is implemented, has 61 focused tests,
and passed Ruff, mypy, seal review and an independently audited execution on a
fresh packet-005-derived disposable schema-29 scratch. All 117 actual table
hashes matched the reviewed seal. The successor 213-entry ledger changes only
the exact 28 targets: 20 parsed and eight empty, with 731 observations. All 28
generations remain shadow `needs_review`, and the 17 unacquired targets remain
pending. This is tested scratch reconciliation, not production input acceptance,
year acceptance, deployment or publication. See the compact
[run receipt](../journal/evidence/runtime/phase1-newsletter-replay-2026-09-17/attempts-001/receipt.json).

The fresh 2010–2026 review export from that exact scratch also passed. Its
offline helper has 27 focused tests and passed Ruff, mypy and independent
source review. The completed run matched all 117 replay table hashes and left
the scratch SQLite closure exact. Event and occurrence rows are unchanged from
the earlier review. Four obsolete approval-notice warnings closed, leaving 58
parse warnings, four parse failures, 980 year-owned findings and all 17 pending
captures. No year is ready or accepted. The compact
[export receipt](../journal/evidence/runtime/phase1-year-review-2026-09-17/receipt.json)
records the exact output hashes; this remains tested scratch review work, not
production acceptance, collection resumption or publication.

The bounded Step Right control acquired the exact Asian Open 2015 event body
into quarantine after a preserved failed attempt exposed a response-decoding
bug. An independently blocked packet made no request; the corrected runner
then passed 50 focused tests, Ruff, mypy and independent source and packet
review. The final operation made one direct request and retained the complete
33,253-byte body at SHA-256
`bc699e4e88dd8af53f495276dde4e3a2618e65b1f64cf7932d815cef00012357`.
Its URL, HTTP 200 status, Memento timestamp, accounting and production
isolation passed independent audit. The page lists April 23–26, 2015, six
contests and 12 result-round links; its responsive sidebar duplicates those 12
links and exposed a local parser defect. The local event extractor now scopes
the main result panel, yields the visible name/date and 12 correctly owned
links, and passed 37 focused tests, Ruff, mypy and independent review. It bumps
the event extract version to 4 and parser version to 3 but is not deployed.

The following local Step Right increment now gives the index, event and round
kinds version-1 admission contracts with no removal authority. Event detail
metadata follows normal queued source-index invalidation, and projector 20
reconciles Step Right round evidence with other sources before writing a
canonical event scope. Preliminary callback marks and outcomes, generic final
bib ownership, named-roster mark attribution and invented score-sheet URLs stay
withheld. Focused admission, dispatch and projection suites passed independent
semantic review after the real preliminary/final controls caught and closed a
callback-legend defect. This code is implemented and tested locally. It is not
in candidate 006, not deployed, and no Step Right admission policy, watch or
historical year is accepted or enforced. Ordinary collection and publication
remain open.
A separately reviewed disposable schema-29 admission rehearsal then accepted
all five fixture generations (1 index, 2 event, 2 round), added 28
observations, removed none, queued the exact six projection units and had zero
guarded outcomes or foreign-key failures. Its three enforcing policies exist
only in scratch; the run made zero network or production writes. See the
[compact rehearsal receipt](../journal/evidence/admission/stepright-admission-rehearsal-2026-09-18/receipt.json).
See the compact
[operation receipt](../journal/evidence/admission/stepright-body-2026-09-17/receipt.json).

The formatted integrated working tree passed 2,847 tests, Ruff, mypy over 228
source files and the evidence-size gate. This is tested local work, not a new
frozen candidate. Per D-0126, work stopped at this boundary: no candidate 007
freeze or Nix build, production deployment, production input acceptance,
backlog drain, year acceptance or publication occurred. A fresh read-only
production check still found candidate 006 active and persistent under the
exact hold, schema markers 29/29, 117 application tables, clean integrity and
foreign keys, all six ordinary units inactive, and 31,821 queued parse units.

Files over 1 MiB have been archived and removed from unpublished local history;
required local copies remain ignored. See the
[archive restore instructions](../journal/evidence/README.md#restore-archived-large-files)
before testing a clean checkout.
See [D-0093](../journal/decisions/0093-resume-v2-from-candidate005.md), the
[resume checks](../journal/evidence/runtime/v2-resume-2026-09-17/read-only-001/state.json), and the
[pause and resume handoff](../journal/investigations/2026/v2-continuation-2026-09-17.md#pause-after-candidate-005-validation)
and [D-0091](../journal/decisions/0091-pause-v2-after-current-validation.md).

The reviewed extension system was activated at 16:50 UTC
under the existing hold, with all six ordinary units inactive afterward.
Its guarded live schema 14→28 migration passed at 16:59 UTC, preserving all
71 predecessor application tables. The acknowledged public H16
baseline is unchanged.
See the [continuation record](../journal/investigations/2026/v2-continuation-2026-09-17.md)
for the new work and checkpoint attempt. The H16 release evidence below is dated 2026-09-16.
A read-only worker check confirmed the operator hold and
all six ordinary cycle, backup, and summary units inactive. The repaired H16
source was activated at 19:58:29 UTC and its release was published and remotely
verified at 22:37:24 UTC. On 2026-09-16 the owner
withdrew the network-related production deferral and authorized deployment
and publication once H16 passes replay, scratch build, and independent audit.
See [D-0029](../journal/decisions/0029-resume-h16-production-release-after-validation.md).

## What is available

The dataset has published identity corrections, an event inventory, and checks
that reject unsafe source interpretations. These are stages V1–V4 in the
[history and recovery plan](plans/history-and-recovery.md#3-stages).
The public baseline is
[`2a6c7dc744fb36eabb5163c0a527d787d3721f4f`](https://huggingface.co/datasets/skeswa/swingset/tree/2a6c7dc744fb36eabb5163c0a527d787d3721f4f).
The original H16 release-closure work is deployed and published. The reviewed
event-completion runtime is now deployed under hold; migration validation,
operating acceptance and its subsequent publication remain separate.
Detailed historical score-sheet coverage remains incomplete.
[D-0087](../journal/decisions/0087-authorize-remaining-v2-acquisition-and-operations.md)
records standing owner authorization for remaining v2 acquisition and operations;
individual fixture permission requests are no longer required. Concrete review
and runtime gates still apply.

A local [JesAnn Nail history page](../journal/investigations/2026/jesann-history-page-2026-09-17.md)
now shows all recorded participation, source percentages, and scoring details.
It includes the offline Jes Test coverage and agreement panel; the pinned
baseline found individual results for 19/85 registry entries. Jes Test can
canary coverage progress and regressions between dataset releases. Desktop and
mobile browser checks passed. It has not been deployed as a website.

## What is running

The OrbStack NixOS worker runs candidate 006 at schema 29 with the active and
persistent paths listed above. The exact schema-29 migration is deployed and
independently audited. Disposable input acceptance and bounded offline replay
passed preservation checks. The separate production input operation has now
also passed under hold, but it does
not establish live operating acceptance. Read-only
profiling measured selector cost; it does not establish sustained throughput.
Further validation is required before ordinary collection resumes. Scheduled
collection, backup, and summary jobs are held by
`/var/lib/swingset/operator-hold`. That runtime interlock remains during
validation and the controlled release sequence. The owner authorization to
proceed afterward is recorded; another permission request is not required.

The published H16 baseline was produced with repaired source
`/nix/store/z689qy41inndill3d92ym8im852x3649-source` and predecessor system
`/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
Its activation and read-only production acceptance passed with all six ordinary
units inactive. The legacy-input compatibility fix and control-lock ownership
repair are complete. Production preparation passed and accepted the exact
reviewed bundle. The independent post-prepare check passed; guarded project/link
initialization completed at 22:16:45 UTC: all 32,445 projections and 2,541 links
succeeded across seven bounded workers. Independent final verification passed
with no unfinished scopes, database integrity errors, or changes to protected
evidence and parse work. The production build passed in 401.55 seconds overall
(388.23 seconds inside the release driver), with 5.10 GiB peak sampled anonymous
memory. All 54 independent audit checks passed, preserving all 4,931 named judges,
including those without WSDC IDs. Publication passed and the acknowledged
candidate `cand_8f31cad7226643ae` is now the baseline. All phase processes have
stopped. The external overrides now match the reviewed extension source;
collection activation and operating acceptance remain pending.

## What is blocked

The current continuation adds schema-25 unsupported-page evidence,
schema-26 bounded ordinary-acquisition timing, and schema-27 source-event
retirement with paginated recorded history. Schema 28 additionally preserves
completion-based request spacing and original UTC debit days across restart.
The reviewed candidate-006 runtime passed **2,648 tests**, Ruff, and mypy over
225 source files, with unchanged source inventories before and after. Its DCN
PDF parser and manual-registry routing change are included in that full run. An actual
held restore from schema 14 through schema 27 passed, preserving all 71 prior
application tables and verifying the public baseline remotely. Schema 28
has also passed its corresponding operational restore and migration rehearsal,
preserving all 71 predecessor tables. Live production preflight passed and the
new system is deployed under hold and its live migration passed. Historical
dispatch timing now has its tested schema-29 increment deployed under hold.
Unobserved fleet history, calibrated objectives and operating
acceptance remain unfinished. See the [continuation](../journal/investigations/2026/v2-continuation-2026-09-17.md)
and [acceptance audit](../journal/investigations/2026/event-extension-acceptance-2026-09-17.md).
These increments do not establish V6 acceptance.

The schema-28 checkpoint, including the 15 paid Archive requests recorded
at its cutoff, passed verification and private archive acknowledgment at 17:56 UTC:
`d71060d4f77b6073f797bd7232bf2aa3694ba685`. It preserved the hold and made zero
live database changes. The older schema-14 checkpoint and its restore receipts
remain retained. The new checkpoint's restore validation is separate. A bounded phase-one
resume attempt issued zero requests because 31,821 pending parse units trigger
the ordinary backpressure gate. The later isolated parser-8 replay reconciled
the 28 already retained newsletter bodies; all 17 missing captures remain
pending. See the
[continuation receipts](../journal/investigations/2026/v2-continuation-2026-09-17.md).
The five approved fixture bodies were captured. The separately
[approved DCN index](../journal/decisions/0060-approve-exact-dcn-index-fixture.md)
was also captured within its two-request, 2,804,642-byte actual usage; its
independent acquisition audit passed. The separately approved Riga metadata lookup passed independent audit. Its
exact results HTML body was captured at 17:43 UTC using two HTTP requests and
16,839 response-body bytes; independent acquisition review passed. These
are quarantined parsing controls, not production acquisition or new source-kind
activation. The later exact two-PDF metadata lookup passed independent audit:
five requests and 156 bytes, with no capture rows returned. Total retained
Archive usage through September 17 was 220 requests and 16,415,117 bytes. The
separate September 18 robots refresh and two Step Right body attempts used
three requests and 66,652 bytes, bringing retained Archive accounting to 223
requests and 16,481,769 bytes. The failed body attempt remains failed even
though its response bytes match the later successful quarantine capture. The
earlier PDF metadata lookup acquired no PDFs. A later reviewed origin fixture
operation captured both exact PDFs with four HTTP requests (including the
allowed robots redirect) and 132,764 bytes. Independent acquisition audit passed; bodies remain quarantined pending
interpretation. No source kind or year was accepted. See the [origin receipt](../journal/evidence/admission/dcn-origin-runner-2026-09-17/quarantine-001/receipt.json).
WP14 is explicitly 2010–2016. H17 human adjudication is deferred to a future
review website; no human labels or precision claims have been added.

The [fresh year-review packet](../journal/investigations/2026/history-review-2026-09-17.md)
confirms 17 pending calendar captures and zero accepted historical years.
The [map-body inspection](../journal/investigations/2026/calendar-map-gaps-2026-09-17.md)
accounts for four acquired map failures: their popups have names and websites
but no event dates. Their findings remain open. The [newsletter repair](../journal/investigations/2026/newsletter-warning-2026-09-17.md)
removes false planning-prose warnings against retained full PDFs while preserving
event observations. It is local; production findings have not been replayed.

| Work                     | Evidence and next requirement                                                                                                                   |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Event-completion rollout | Reviewed runtime, schema 29 and exact inputs are deployed under hold. Measured service, operating acceptance and subsequent publication remain. |
| Historical score sheets  | Source fixtures, event aliases, and year reviews remain necessary. The retained handoff records no years accepted for phase-two acquisition.    |
| Identity evaluation      | Human review of the representative sample remains pending. Model-generated review notes do not satisfy it.                                      |
| Automatic repairs        | Activation follows the remaining history, recovery, and review gates.                                                                           |
| PostgreSQL and Dokploy   | A detailed migration plan exists. It is a target, not the recorded production setup.                                                            |

The memory and completion fixes passed a full scratch replay and build. That
build took 368.36 seconds with about 4.40 GiB peak sampled anonymous memory.
Its independent audit passed 50 of 54 checks, exposing two preservation bugs:
mapping overwrote inventory-backed event identities, and reconstruction
discarded valid event bases when an alternative row failed support checks.
The failed candidate is retained and is not accepted. Repairs preserve judge
names without requiring WSDC IDs. See
[the dated outcome](../journal/investigations/2026/h16-proof-reuse-2026-09-16.md).

The corrected frozen source passed all 1,333 tests, including 129 build tests,
Ruff, mypy, and independent source/service-binding checks. Its NixOS system
built offline without activation. Its fresh checkpoint-based scratch replay
completed all 34,986 projection/link scopes and passed independent verification,
with no unfinished work or database integrity errors. A resource-log pathname
replacement left an explicitly retained sampling gap; separate lifetime peak
records verify every worker stayed below the memory limit. The coordinator
accepted replay for the next scratch build. That build passed in 441.81 seconds
overall (385.26 seconds building), with a 5.11 GiB sampled anonymous-memory
peak. All 54 independent candidate audit checks passed, including preservation
of all 4,931 named judges and registry-occurrence associations. Production
preflight passed against the retained predecessor checkpoint and baseline.
Production deployment, initialization, and publication are authorized after
those checks pass. Production validation and publication have now passed; see
[the release outcome](../journal/investigations/2026/h16-production-release-2026-09-16.md).

The H14 event-completion extension now has local enumeration, durable event
turns, protected listed-page capacity, conservative expansion controls, and a
doctor drill-down for artifacts, recorded service, and current blocker facts.
Blocker-change history and pinned release coverage are now implemented locally.
After correcting discovery to include `tests/build`, the combined schema-21
run passed all 1,870 tests, Ruff, and mypy over 197 source files. Two subsequent
reporting fixes passed 123 affected tests and the same static checks. A full-data
diagnostic of the reconstruction repair preserved all 4,931 baseline judges.
The separate frozen H16 source has now passed its full scratch replay, build,
and all 54 independent audit checks; production initialization and its independent
final verification have passed.
Bounded artifact-backed stage counts and recorded progress are now integrated
locally. Historical-page rotation and shared evidence lookup in the doctor
drill-down passed 243 combined focused tests. Lost parse-hint reconstruction
is integrated locally, including explicit retries after failure; 91 focused
tests passed. After correcting three test fixtures using outdated interfaces,
the final combined run passed all 1,933 tests in 401.21 seconds, Ruff, and mypy
over 198 source files. Source hashes remained unchanged during validation.
Eligible-time reporting and operating acceptance remain pending.
The first schema-22 fleet accounting increment passed independent review and
all 1,957 tests in 405.52 seconds, Ruff, and mypy over 201 source files. Source
hashes stayed unchanged during validation. It adds bounded parent checks and
observed completion transitions. Schema-23 immediate-edge retirement proofs and
pinned unavailable-origin release coverage passed independent review and all
2,014 tests in 420.71 seconds, Ruff, and mypy over 205 source files. Source hashes
stayed unchanged during validation. The first combined run exposed one stale
test assertion, corrected before this passing run. Whole-event retirement and
skipped history remain unknown. Schema-24 local accounting for verified
unavailable-page gaps passed independent review and all 2,063 tests in 419.36
seconds, Ruff, and mypy over 206 source files. Source hashes stayed unchanged
during validation. Fresh inventory checks and sampled accounting use the same
evidence rule; unavailable pages do not count as acquired or interpreted.
Eligible-time reporting remains pending. These dated schema-24 increments are included in the reviewed schema-28 runtime
deployed under hold on September 17; operating acceptance and publication remain open.
Six additional offline tests passed after formatting, including actual 33-page
acquisition and admission under continuous discovery/current refresh, pressure
drain, and activated restore of schema-24 accounting. The earlier full-run
source and tests remained unchanged. See
[the cohort results](../journal/investigations/2026/event-completion-cohort-2026-09-16.md).
An offline policy comparison exercises
the retained backlog shape under continuous synthetic discovery; it does not
measure ordinary-host throughput. This code is outside the frozen H16 release
and has not been deployed. See
[the implementation outcome](../journal/investigations/2026/event-completion-2026-09-16.md).

## Where to continue

Start operating work at the [current extension handoff](../journal/investigations/2026/event-extension-operating-handoff-2026-09-17.md).
It records the schema-28 rollout and remaining held activation checks.

The identity linker’s evidence → resolution → persistence refactor is included
in the held candidate-003 deployment. Its earlier standalone full suite passed
2,088 tests, including 195 focused checks, plus an original-versus-refactored
output comparison. Candidate 003 has its separate 2,239-test integrated receipt.
No subsequent release has published the refactor’s rebuilt output. See the
[dated validation outcome](../journal/investigations/2026/link-resolution-refactor-2026-09-17.md)
for scope and evidence.

- [Release handoff](../journal/investigations/2026/2026-09-15-release-handoff.md): exact source pins, scratch paths, holds, and release gates.
- [Current production release](../journal/investigations/2026/h16-production-release-2026-09-16.md): repaired source, authorization, and actual phase outcomes.
- [Build validation investigation](../journal/investigations/2026/h16-validation-investigation-2026-09-15.md): why isolated timings did not prove full-build completion. Its VM measurements are dated 2026-09-16 UTC / 2026-09-15 Denver.
- [Active plans](plans/README.md): remaining work and acceptance criteria.

The reviewed core test selection is implemented and locally validated:
**989 passed in 2 minutes 54 seconds**, serially on the development Mac on
2026-09-18. Another 2,039 cases remain in the opt-in extended suite. No runtime
or deployment changed for this test-selection work. See the
[testing guide](guides/testing.md) and
[timing receipt](../journal/evidence/runtime/core-test-suite-2026-09-18/receipt.json).

Update this page when new evidence changes a current claim. Put detailed
commands, measurements, and receipts in the journal. Local tests, deployment,
and publication are separate milestones; do not infer one from another.
