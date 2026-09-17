# Current status

Updated 2026-09-17. The operating evidence below is dated 2026-09-16.
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
The original H16 release-closure work is deployed and published; the later
event-completion extension remains local.
Detailed historical score-sheet coverage remains incomplete.

## What is running

The last operating handoff records an OrbStack NixOS worker using SQLite
schema 14. Scheduled collection, backup, and summary jobs are held by
`/var/lib/swingset/operator-hold`. That runtime interlock remains during
validation and the controlled release sequence. The owner authorization to
proceed afterward is recorded; another permission request is not required.

The worker now runs repaired H16 source
`/nix/store/z689qy41inndill3d92ym8im852x3649-source`, with active and persistent
system `/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
Activation and read-only production acceptance passed with all six ordinary
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
stopped. Ordinary scheduled jobs remain held while external override paths
differ from the frozen reviewed bundle; resuming collection needs separate
input and activation checks.

## What is blocked

| Work                     | Evidence and next requirement                                                                                                                                                                               |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Event-completion rollout | Schema-24 local implementation and offline acceptance pass. Deployment rehearsal, eligible-time reporting, unsupported-page classification, measured service, and a subsequent coverage publication remain. |
| Historical score sheets  | Source fixtures, event aliases, and year reviews remain necessary. The retained handoff records no years accepted for phase-two acquisition.                                                                |
| Identity evaluation      | Human review of the representative sample remains pending. Model-generated review notes do not satisfy it.                                                                                                  |
| Automatic repairs        | Activation follows the remaining history, recovery, and review gates.                                                                                                                                       |
| PostgreSQL and Dokploy   | A detailed migration plan exists. It is a target, not the recorded production setup.                                                                                                                        |

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
Eligible-time reporting remains pending. These increments have not been deployed.
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

The identity linker has a local evidence → resolution → persistence refactor.
The full offline suite passed 2,088 tests, including 195 focused checks; the
original-versus-refactored output comparison also passed. It has not been
deployed or published. See the
[dated validation outcome](../journal/investigations/2026/link-resolution-refactor-2026-09-17.md)
for scope and evidence.

- [Release handoff](../journal/investigations/2026/2026-09-15-release-handoff.md): exact source pins, scratch paths, holds, and release gates.
- [Current production release](../journal/investigations/2026/h16-production-release-2026-09-16.md): repaired source, authorization, and actual phase outcomes.
- [Build validation investigation](../journal/investigations/2026/h16-validation-investigation-2026-09-15.md): why isolated timings did not prove full-build completion. Its VM measurements are dated 2026-09-16 UTC / 2026-09-15 Denver.
- [Active plans](plans/README.md): remaining work and acceptance criteria.

Update this page when new evidence changes a current claim. Put detailed
commands, measurements, and receipts in the journal. Local tests, deployment,
and publication are separate milestones; do not infer one from another.
