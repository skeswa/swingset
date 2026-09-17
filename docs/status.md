# Current status

Updated 2026-09-17. **Paused at the owner's request after candidate 005 validation.**
The frozen schema-29 candidate passed 2,536 tests, Ruff and mypy over 224 source
files, with unchanged inventories before and after; its offline NixOS build
passed. It is not deployed or published. All continuation agents stopped;
no commit or push was made. See the
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

The OrbStack NixOS worker now has reviewed extension source
`/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source` active and persistent in
system `/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
The guarded live migration reached schema 28 and passed its preservation
checks at 16:59:15 UTC. Independent post-migration verification passed all 42 checks. Disposable input
acceptance and the first bounded offline replay passed preservation checks.
That turn completed only four units in 550 seconds. Read-only profiling found
repeated mapping and dancer-readiness checks; the reviewed fixes now select a parse in 9.09 seconds on the retained scratch.
This is instrumented selection, not sustained throughput. Further validation
is required before ordinary collection resumes. Scheduled collection, backup, and summary jobs are held by
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
production input acceptance and collection activation remain pending.

## What is blocked

The current continuation adds schema-25 unsupported-page evidence,
schema-26 bounded ordinary-acquisition timing, and schema-27 source-event
retirement with paginated recorded history. Schema 28 additionally preserves
completion-based request spacing and original UTC debit days across restart.
The reviewed candidate-003 runtime passed **2,239 tests**, Ruff, and mypy over
217 source files, with unchanged source inventories before and after. Subsequent
DCN parser and operating-helper edits have separate focused checks and are not
covered by that full run. An actual
held restore from schema 14 through schema 27 passed, preserving all 71 prior
application tables and verifying the public baseline remotely. Schema 28
has also passed its corresponding operational restore and migration rehearsal,
preserving all 71 predecessor tables. Live production preflight passed and the
new system is deployed under hold and its live migration passed. Historical dispatch timing has a separately tested local schema-29 increment.
It is not deployed. Unobserved fleet history, calibrated objectives and operating
acceptance remain unfinished. See the [continuation](../journal/investigations/2026/v2-continuation-2026-09-17.md)
and [acceptance audit](../journal/investigations/2026/event-extension-acceptance-2026-09-17.md).
These increments do not establish V6 acceptance.

The schema-28 checkpoint, including the 15 paid Archive requests recorded
at its cutoff, passed verification and private archive acknowledgment at 17:56 UTC:
`d71060d4f77b6073f797bd7232bf2aa3694ba685`. It preserved the hold and made zero
live database changes. The older schema-14 checkpoint and its restore receipts
remain retained. The new checkpoint's restore validation is separate. A bounded phase-one
resume attempt issued zero requests because 31,821 pending parse units trigger
the ordinary backpressure gate; all 17 captures remain pending. See the
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
Archive usage is 20 requests and 2,952,065 bytes. No PDFs were acquired. The
origin fixture proposal is prepared; its runner has not been implemented.
WP14 is explicitly 2010–2016. H17 human adjudication is deferred to a future
review website; no human labels or precision claims have been added.

The [fresh year-review packet](../journal/investigations/2026/history-review-2026-09-17.md)
confirms 17 pending calendar captures and zero accepted historical years.
The [map-body inspection](../journal/investigations/2026/calendar-map-gaps-2026-09-17.md)
accounts for four acquired map failures: their popups have names and websites
but no event dates. Their findings remain open. The [newsletter repair](../journal/investigations/2026/newsletter-warning-2026-09-17.md)
removes false planning-prose warnings against retained full PDFs while preserving
event observations. It is local; production findings have not been replayed.

| Work                     | Evidence and next requirement                                                                                                                                                  |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Event-completion rollout | Reviewed runtime deployed under hold after full validation and restore rehearsal. Live migration passed; input acceptance, measured service and subsequent publication remain. |
| Historical score sheets  | Source fixtures, event aliases, and year reviews remain necessary. The retained handoff records no years accepted for phase-two acquisition.                                   |
| Identity evaluation      | Human review of the representative sample remains pending. Model-generated review notes do not satisfy it.                                                                     |
| Automatic repairs        | Activation follows the remaining history, recovery, and review gates.                                                                                                          |
| PostgreSQL and Dokploy   | A detailed migration plan exists. It is a target, not the recorded production setup.                                                                                           |

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

Update this page when new evidence changes a current claim. Put detailed
commands, measurements, and receipts in the journal. Local tests, deployment,
and publication are separate milestones; do not infer one from another.
