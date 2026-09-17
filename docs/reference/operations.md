# Operations

This page defines worker behavior: cycles, locks, controls, and recovery. Use the [task guide](../guides/operation.md) for commands and [current status](../status.md) for the recorded deployment. Later event-completion sections describe a pending extension.

[Reference index](README.md)

## On this page

- [Host](#host)
- [Timers](#timers)
- [Cycle](#cycle)
- [Locks and operator commands](#locks-and-operator-commands)
- [Interruption and recovery](#interruption-and-recovery)
- [Backup and restore](#backup-and-restore)
- [Secrets](#secrets)
- [Bootstrap and backfill](#bootstrap-and-backfill)
- [Observability](#observability)
- [Event completion reporting (H14 extension)](#event-completion-reporting-h14-extension)
- [Requirement reporting in shadow (H11)](#requirement-reporting-in-shadow-h11)
- [Work isolation and artifact recovery (H12)](#work-isolation-and-artifact-recovery-h12)

## Host

The pipeline runs on a Linux box as `nix/module.nix`. v1 uses an OrbStack
NixOS VM on the development Mac; [implementation plan](../../journal/archive/v1-implementation-plan.md#2-environment)
owns that setup. The module owns the service user, state directory,
environment file, toolchain, units, and timers. The VM uses UTC. The
same service module installs on production later.

The service uses Python, uv, node, and native libraries from the flake.
`ExecStartPre` runs `uv sync --frozen --no-dev` into the state's disposable
venv. Upgrading code means advancing the checkout with jj and rebuilding
NixOS. `services.swingset.overridesDir` points to the checkout's overrides;
a correction needs only a checkout update. Cycles capture those files
as described in [local state](state.md#invalidation).

The unit starts dry (`dryRun = true`) until the owner provisions
`HF_TOKEN` and explicitly sets `dryRun = false`. The token's presence
alone never enables publication. State layout is defined in
[local state](state.md#state-directory).

## Timers

| Unit                     | Schedule                                                    | What it runs                  |
| ------------------------ | ----------------------------------------------------------- | ----------------------------- |
| `swingset-cycle.timer`   | every 15 min, `RandomizedDelaySec=120`                      | `swingset cycle --budget 12m` |
| `swingset-backup.timer`  | Mon-Thu 04:00; Fri-Sun 04:00, 12:00, 20:00 (box local time) | `swingset backup`             |
| `swingset-summary.timer` | daily 08:00                                                 | `swingset summary`            |

Timers use `Persistent=true`. The 15-minute timer is the floor;
[scheduling](scheduling.md) decides what is due. Backup lock waits do
not count as completed backups. The backup service retries failures
with `Restart=on-failure` and `RestartSec=60`; doctor reports its last
successful checkpoint so retrying cannot conceal stale backups.

## Cycle

1. Acquire the state writer lock. Refuse normal work while
   `RESTORE_PENDING` exists. Capture and accept changed input files.
2. Reconcile any pending publication before creating another candidate.
   This runs even when no fetch or build is due. Respect dry-run mode.
3. Give pending identity corrections priority. Discover seed watches
   and reserve acquisition time even when offline work is pending.
   Select eligible requests fairly within existing host limits,
   controls, and backlog high-water marks.
4. Rotate eligible parse, project, and link units fairly. Check the
   budget between units. Failed units retain their evidence and retry
   eligibility; unrelated work continues. A shared projection may run
   again when later parses change its inputs, but the same input
   fingerprint is attempted at most once in a cycle.
5. Run saved interpretation once its parse and project inputs settle.
   Once all queues drain, build when the input/baseline pair needs work
   and publish when semantic content differs. These actions receive
   offline time before unused time is lent back to acquisition. Leave
   unfinished work for the next cycle. A dry run can build but never commits.
6. Write `runs/<run_id>.json` and release the lock.

There is no "nothing fetched, skip later stages" rule. File changes,
manual findings, and queued work are independent reasons to run. The
cycle runner consumes [durable work](state.md#invalidation); it does not
carry another stage dependency graph. Reserved acquisition prevents
offline backlog from starving collection; fair offline service and
build-before-borrowing prevent a registry sweep from taking every turn.

## Locks and operator commands

One advisory lock in the state directory excludes cycle, backup, restore,
and manual data mutations. The process holds it through each command;
the kernel releases it on death. Read-only doctor and summary use a
consistent database read and do not acquire the writer lock.

| Caller finding the lock held                       | Required behavior                                                                                        |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Timer-triggered duplicate cycle                    | Exit 0 with an explicit skipped-overlap log                                                              |
| Manual data mutation: sweep, reparse, or fetch-one | Wait up to `--lock-timeout` (default 60 s); timeout exits nonzero and says no change was applied         |
| Backup                                             | Wait for the lock; interruption or upload failure is nonzero and retried by the service                  |
| Restore                                            | Wait up to the explicit timeout; require timers disabled and the former writer stopped before activation |

Every data-writing command except restore refuses a state directory
marked `RESTORE_PENDING`; doctor remains available for diagnosis.
Manual mutation success means its transaction committed. There is no
successful skipped pause or sweep. Backup tests overlap a cycle and prove
that the checkpoint eventually runs.

`swingset pause --all | --host <h> | --source <s> | --kind <k>` records
an operator pause. `resume` removes only the selected operator pause.
Both accept `--reason`, `--actor`, `--lock-timeout`, and `--wait [seconds]`.
The actor defaults to the local account and the reason to `operator request`;
blank values and unknown selectors are rejected. `--until` on pause requires
a future timestamp with a timezone. An omitted expiry means indefinite.
Automatic host pauses remain separate and resume never clears them.

Controls use a restricted connection that cannot migrate or accept inputs.
They bypass the whole-cycle writer lock and serialize at the next bounded
admission boundary under `control.lock`. A successful receipt means the control
transaction committed. A servicing timeout is nonzero and means no change was
persisted. Once committed, a pause prevents matching new admissions; already
admitted work may drain. `--wait` waits up to sixty seconds by default for that
drain. A wait timeout or interruption preserves the pause and reports the
remaining admissions; it never reports that no change occurred.

Worker write phases have a 45-second wall-clock limit, leaving time for rollback
and control servicing within the default 60-second bound. The limit interrupts
Python work and long SQLite statements. Nested transactions inherit the outer
deadline. A timed-out derivation rolls back, retains its queue token, and records
`write_deadline_exceeded`; unchanged work waits for a changed input or explicit
retry. Worker writes run on the process main thread. Read snapshots, including
release construction, do not hold SQLite's writer and have no write deadline.
Explicit schema migrations remain held maintenance operations. A shorter
operator timeout may still expire before a valid bounded unit drains.

All and source pauses hold matching requests and post-fetch repairs. Kind
pauses use registered requirement kinds and the actual dependencies of each
action. A shared action waits if any required dependency is paused. Host pauses
hold requests only. A coherent publication waits on any paused source or kind;
its suppression and integrity checks remain mandatory. Manual parse, project and link use the same bounded worker and attempt ledger as
cycles. They skip held units, report their dependencies, and stop cleanly when
no eligible work remains. Explicit and saved registry-crosscheck interpretation
also requires admission; archive-only retention preserves supplied evidence
without interpreting it. Startup recovers abandoned admissions before accepting
inputs or creating a run; uncertain publications still require receipt reconciliation.
Resume does not trigger
a catch-up burst: overdue work remains subject to ordinary scheduling budgets.

Doctor and summary report `running`, `pausing`, or `paused`, the control revision,
pause IDs, actor, reason, expiry, duration and exact resume selectors. They show
draining admissions separately, and flag a drain older than sixty seconds.
Inventory and queue overlays use the same dependency resolver as execution.
`no_progress_clock` and queue `lag_clock` exclude the union of known matching
operator pause intervals, including overlapping controls, selective resume and
expiry. After resume, intentional hold time does not immediately trigger a
no-progress alarm. These are clocks under current dependencies; they do not
reconstruct old dependency ownership, automatic host-pause history or budgets.
A legacy pause with an unknown start makes an overlapping clock unavailable,
with an explicit count and reason. Evidence age, correction age and queue wall
age remain unchanged. Interval histories are read once and cached by scope.
Intentional pauses suppress matching no-progress alarms while retaining ages;
unpaused stalled work and stuck drains remain visible. Status reads do not
expire rows or change work eligibility. Expiry is recorded at the next mutation
boundary. To stop a process immediately, stop its service as well as its timer;
a durable pause does not kill an already admitted action.

## Interruption and recovery

Each unit commits output, downstream work, revision changes, and its own
completion in one transaction. Artifacts are durable before database
references; publication intent is durable before the remote call. See
[local state](state.md) and [publishing](publishing.md#candidate-and-baseline).

On SIGTERM, stop issuing requests, finish or abandon the in-flight
request within its timeout, and stop at a unit boundary. Write a stopped
run summary when possible and exit 0 after a graceful stop. An unhandled
failure exits nonzero. `KillMode=mixed` and `TimeoutStopSec=45` bound the
service stop; forced termination rolls back any unfinished transaction.
A missing final run log after a hard kill is diagnostic loss, not proof
that a data transaction failed. The cycle budget applies to every stage.

A crash harness injects termination before and after each transaction,
artifact completion, and publication marker transition, then restarts.
Assert the same canonical belief, referential integrity, drained work,
and public changelog as an uninterrupted run with the same inputs.
Compare logical results; retry attempts, run ids, logs, timestamps, and
harmless orphan files can differ. Verify one remote commit per candidate,
including lost responses, rather than one HTTP attempt. The harness
also sends SIGTERM and checks graceful completion or rollback of the
current unit. It runs offline in WP3 and WP5.

## Backup and restore

A backup is a complete recoverable checkpoint, not a list of files that
happened to change. While holding the writer lock, make a SQLite backup
API copy and a checkpoint manifest listing each required relative path,
size, and SHA-256, plus the baseline target, pending candidate id if any,
schema and implementation versions, and selected input bundle hash.
Include the following closure of referenced artifacts:

- All raw and manual blobs referenced by snapshots or findings.
- All derived extracts referenced by snapshots.
- Every body and extract named by a retained source generation manifest,
  including staged, blocked, superseded, and revoked generations whose
  extract was never installed on the snapshot pointer.
- Captured input bundles referenced by current state or retained candidates.
- The complete baseline candidate and any pending candidate, including
  `BUILT`, `PUBLISHING`, and `PUBLISHED` when present. A receipt without
  promotion still belongs to a pending candidate.
- Any complete disposable candidate retained by the checkpoint, plus
  run summaries. Disposable candidates may instead be omitted; there
  is no database build-completion record to repair.

Validate every reference against the manifest. The Hub transport puts the
manifest outside a deterministic uncompressed tar containing the checkpoint
files, with a small transport manifest that records the tar's size and SHA-256.
This keeps large registry checkpoints to three upload operations and lets Xet
deduplicate unchanged tar blocks. Upload all three files in one archive commit;
restore also accepts older commits that stored every checkpoint file separately.
Reuse already uploaded content
only after confirming it exists at the archive's selected parent commit.
`backup_uploads` is an optimization, never evidence that a missing file
exists remotely. Record success only after acknowledgment. A failed
upload or lock interruption is retried; an unchanged checkpoint needs
no new commit. The venv, uv cache, lock file, incomplete candidates, and
unreferenced orphan blobs are excluded. Secrets are never captured.

Restore is an activation protocol:

1. Disable timers and stop the former writer. Select a specific archive
   commit, download into an empty staging state directory, and create
   `RESTORE_PENDING` before any service can use it. Resume verification
   after interruption while keeping that marker.
2. Verify the manifest's files, hashes, reference closure, SQLite
   integrity and foreign keys, schema compatibility, and saved input
   bundles. A missing extract or pending candidate is an error. Rebuild
   disposable runtime dependencies separately.
3. Recreate the baseline symlink and read the public repo head, even
   when a local publication receipt exists. A saved pending receipt must
   match that head; a mismatch is an error, never an unsent intent to
   retry. If the head equals the
   saved baseline, retain it. If a pending candidate exactly matches
   head by expected parent, candidate id, manifest, and file hashes,
   finish its acknowledgment and promotion locally. If its commit did
   not land and head still equals its expected parent, retain the intent
   for a later real cycle; restore itself creates no public commit.
4. If head is ahead of those recoverable states, or differs in any other
   way, keep `RESTORE_PENDING`, report expected and actual SHAs, and
   fail without publishing. A matching newer checkpoint can complete
   recovery. If none exists, the operator must recover the missing
   private state or explicitly decide how to handle data loss; normal
   restore never invents observations from public Parquet or resets
   history. Unknown third-party commits are also held for review.
5. Recheck the head before activation, rebuild upload receipts against
   the selected archive commit, and remove `RESTORE_PENDING` durably.
   The next cycle accepts the installed checkout's inputs against the
   saved bundle and enqueues any differences before proceeding. Enable
   timers only after verification succeeds.

Backup and public publication are separate commits. Therefore a machine
loss can lose private observations collected after its last checkpoint,
even when those observations were already published. v1's automatic
restore guarantee covers checkpoint state and its saved pending intent;
it cannot promise unattended recovery of unbacked private evidence.
The runbook must show the remote-ahead report and the recovery choices,
with publishing kept disabled until a choice is made and reconciled.

Acceptance tests restore after intent but before submission, after the
remote commit but before its receipt, and after receipt but before
promotion. Each uses the archived checkpoint alone and must preserve the
candidate and avoid duplicate commits. Also test a missing extract,
missing pending directory, stale upload receipt, interrupted restore,
a public commit made after backup, and an unrelated remote head. The
last two must leave publishing disabled without changing public history.

## Secrets

`HF_TOKEN` with write access to both repos, in a root-only environment
file referenced by the units. Nothing else. No source needs credentials,
by design.

## Bootstrap and backfill

The registry sweep (16 hours) and the historical backfill (weeks) both
run inside normal cycles, driven by cursors in state, with reserved
service for eligible old work. Historical year and admission gates
still apply. `swingset sweep --start 1` seeds the registry cursor. No
special long-running job exists; the box is always on, so the cycle loop
is the long-running job.

## Observability

- Every run writes `runs/<run_id>.json` to the state directory: counts of
  watches checked, 304s, changed bodies, bytes fetched per host, parse
  errors, link status histogram, publish commit SHA, duration. These are
  included in the backup.
- Structured logs go to the systemd journal. `journalctl -u swingset-cycle`
  is the primary debugging tool.
- `swingset summary` writes a short daily digest: events in each watch
  state, new `review_queue` items, links upgraded and downgraded, sources
  paused, budget use per host, and the last publish. It goes to the
  journal and, if `summary_webhook_url` is set in config, to that URL as
  a JSON POST. The destination is the owner's choice and is not decided
  here.
- A handled parse failure marks the snapshot `parse_status = failed`, retains
  its body and last-good observations, and records the attempted versions.
  H12 records the failure durably and keeps the unit pending but ineligible
  for unchanged inputs. Independent eligible fetches and derivations can
  still progress. The run summary lists parse and unit failures; unit
  failures or a source parse failure rate above 10% mark the run failed.
- A host paused for 403 or challenge fails the run with a clear message
  and is the first line of the next daily summary. This is the one
  condition that should interrupt a human.
- Review workflow: the owner reads `review_queue` (published in the
  dataset and printed by `swingset doctor`), adds rows to
  `overrides/*.csv`, and commits. The next cycle picks up the new
  overrides. Contributors can send pull requests against `overrides/`.
- `swingset doctor` prints last success per source, operator pauses and automatic host
  pauses separately, budget use, watches by state, pending work by
  stage, pending candidate, restore status, last backup commit and
  time, review size, and last acknowledged publish SHA.

## Event completion reporting (H14 extension)

Accepted design; the local catalog, single-event artifact drill-down, recorded
request-service history, bounded blocker-change observations, and acknowledged
release coverage are implemented in the working source. Recorded successful
outputs, sampled verified progress, and bounded release-local stage counts are
also implemented locally. Bounded sampled event accounting is implemented locally;
instantaneous fleet verification, unobserved lifetime history, historical dispatch
eligibility and whole-event eligible-work alarms remain pending. Bounded ordinary-acquisition
timing and diagnostic alarms are implemented locally under the
[timing contract](event-timing.md); production objectives remain unset pending
measurement. The catalog
does not hash every artifact during ordinary doctor refreshes; use
`--source SOURCE --source-event SOURCE_REF` for a fresh local check. Unknown
values remain null. Deployment is tracked in [current status](../status.md).

The drill-down's service history reports all recorded event-owned request
charges by actual host, the persisted turn and policy, capacity use and
borrowing, and up to 20 recent requests. Shared requests belong to their selected
event owner. Outcomes and last issuance explain attempts; they do not establish
successful progress or current eligibility. Legacy intervals without receipts
remain unknown. See [D-0014](../../journal/decisions/0014-explain-recorded-event-service.md).

Its `request_blockers` section shows current watch timing, applicable operator
pauses, source configuration, actual request-host cooldowns and daily allowances,
and parsing or work backpressure. Overlapping blockers stay visible. The
assessment is explicitly incomplete: historical dispatch, capture, robots,
request-chain limits, and other gates still govern execution. The report never
acquires a host grant or changes a retry time. See
[D-0017](../../journal/decisions/0017-report-current-event-blockers.md).

`blocker_history` retains observed changes with their policy, enumeration,
control revision, and input bundle. A refresh samples at most eight events by
default, with bounded member and watch sets; unassessed history stays explicit.
Samples do not prove that a blocker remained present or absent between checks.
`publication` reads a single acknowledged baseline, verifies its files and
closure receipt, and reports its own pinned enumeration. A newer local
enumeration cannot advance these published counts. Legacy receipts without
the required binding remain unknown.

`progress_history` separates successful output operations from qualified
observed progress. Output facts commit with the new successful snapshot or
accepted interpretation; failures and unchanged polls add none. Progress also
requires a fresh missing baseline, unchanged enumeration, and a later operation
whose exact artifacts verify. Operation time and observation time are separate.
Restoring old files alone establishes availability, not new operation progress.

The observer shares one resource budget across at most eight events per refresh,
verifies enumeration content with a 128-member limit, and visits at most 32
members per event. Larger enumerations and exhausted scans remain unassessed.
Event and page cursors preserve later work's opportunity to receive a fresh
budget. Parent support is checked through the same session after pages are
observed positive. The observer does not infer continuous eligibility or replace
the fresh inventory drill-down's completion checks.
Historical success before recording began remains unknown.

The nested `accounting` report classifies known enumerations as
`locally_accounted`, `unfinished`, or `unassessed` from full bounded membership
and fresh page and parent observations. Each known page must be interpreted or
have a verified unavailable-origin response or an evidence-backed unsupported
disposition; all required parents must remain usable. A page is definitely
unfinished only when all three accounting branches are false.
Otherwise an unresolved branch remains unknown. A definite missing page or parent
establishes unfinished work even when other counts remain unknown. Legacy and oversized
enumerations remain unassessed. The report reads metadata only and shows the
oldest contributing check and earliest expiry: unrecorded file loss is detected
on subsequent verification, not during ordinary catalog reporting.

Accounting counts describe one catalog page. `coverage_complete=false` and
`next_cursor` identify truncation or metadata-budget exhaustion; never present
those subtotals as fleet totals. `event_accounting_report.report()` accepts
`after_event` and `limit` (1–100) inside the caller's query-only transaction.
The existing outer doctor catalog is unchanged. Historical receipts distinguish
membership changes, reopening, and restored availability without inventing
successful operations. Latest and last definite assessments are shown; historical
reopening totals and whole-event retirement remain explicitly unassessed. Pagination is still
unknown, so local accounting is not whole-event completeness or publication.
See [D-0034](../../journal/decisions/0034-record-bounded-event-accounting.md).

Unavailable observations retain the exact response support checked by the shared
release verifier. They do not increase acquired or interpreted counts and never
produce successful-progress receipts. Reports show unavailable and page-accounted
subtotals separately. A changed same-source snapshot domain invalidates gap
observations, including undeclared aliases; metadata-only reports still do not
detect unrecorded file loss before the next verification. Unknown unavailable
evidence cannot turn a missing interpretation into definite unfinished work.
Parent checks receive priority after every page has a fresh accounting branch.
See [D-0037](../../journal/decisions/0037-account-for-supported-unavailable-page-gaps.md).

Each accounting event's `page_retirement` reports the current immediate predecessor
edge separately. Verified normalized page IDs require an ordered, exact accepted
watch-authoritative replacement, supported prior ownership, and no surviving
independent claim. An admitted child result can preserve a page after an index
omits it. Auxiliary removal lists are not proof. Reports expose the observation
window and latest historical withdrawal receipt; missing or invalid evidence
remains unassessed. A whole-event retirement flag and complete retirement history
stay unknown, even when all known predecessor obligations were withdrawn.

The edge shares the progress budget and H13 artifact controls. Leftover-budget
exhaustion receives a fresh opportunity; a proof too large for a fresh allowance
yields back to page work. Repeated checks, policy reloads, and restored proof files
do not add another withdrawal receipt. The metadata-only report does not detect
unrecorded artifact loss until another verification. See
[D-0035](../../journal/decisions/0035-prove-immediate-event-page-retirements.md).

The complete reporting contract follows. Doctor and summary expose the
[source-event inventory](scheduling.md#event-completion) using the same
consistent, read-only snapshot as other progress reports. Show source reference,
nullable canonical event, enumeration evidence and completeness, listed page
count, acquired and interpreted counts, and pages represented in the last
acknowledged release. Round and identity counts retain their own units.

For every unfinished event, show first discovery, last successful progress,
wall age, eligible service age or unknown, missing pages, current blockers, and
next eligible action. Distinguish budget exhaustion and next reset, operator
hold, host cooldown, retry wait, parsing backpressure, unsupported interpretation,
mapping review, and awaiting publication. Report all applicable blockers;
being selected or repeatedly attempted is not successful progress.

A drill-down explains the last turn's selection reason, policy and enumeration,
issued requests, and blocker transitions. Summaries show events finished,
reopened, explicitly retired, and still waiting without counting failed attempts
or reduced denominators as completed work. Keep unknown scheduling history
explicit rather than reconstructing decisions from today's policy.

Alert when an event exceeds its configured eligible service-gap or no-progress
objective. Wall age continues during pauses and exhausted budgets; eligible age
excludes intervals when no next request could pass the relevant gates. The
operator marker in the runbook counts as a hold even when `operator_pauses` is
empty. Holds suppress eligible-work alarms, not stale-status or evidence-age
reporting. Report overload when admitted demand exceeds measured service;
never promise an ETA for an unbounded or blocked population.

## Requirement reporting in shadow (H11)

Doctor adds a local requirement inventory with `--json`, `--watch`,
`--interval` (five seconds by default), `--source`, `--kind`, and
`--requirement`. JSON includes a versioned inventory schema. Human and JSON
views share one SQLite snapshot and require no writer lock or network. An
older database reports that its inventory migration is pending.

The view shows the scan cursor and age, unmet states, eligibility and pause
overlays, active attempts, oldest unresolved age, last progress, and each
requirement's evidence and next action. A scan or eligible requirement with
no progress for thirty minutes raises a local alert. Intentional pauses
suppress eligible-work alarms while evidence and scan ages remain visible.
Worker state is inferred from durable run times; `stopped_or_stale` does not
claim an operating-system process check.

`pipeline_lag` reports acquisition schedule age from overdue `next_check_at`
values and derivation queue age from the current legacy `enqueued_at` values.
It labels source-wide acquisition and global queue denominators separately
from filtered requirements. Unknown schedules and enqueue times stay explicit.
Diagnostic alerts use the report's staleness threshold and identify the oldest
scope and next action; they do not assert available host budget or establish
H14 service-gap objectives. Acquisition pauses and cooldowns suppress acquisition
diagnostics; matching all/source/kind controls suppress queue-age diagnostics.
A host pause alone does not suppress local derivation alerts. Ages remain visible.
H15 counts unfinished derivations by fingerprint comparison. Their available
queue hint times supply lag ages; replacement inputs can reset those times, and
lost hints leave age unknown rather than reporting zero. Requirement alerts include the last
recorded attempt and blocking reason without counting retries as progress.

Summary adds opened, reopened, satisfied, and retired counts, with attempts
and failures separate. `--since` accepts an explicit timezone. With no
`--since`, each filter resumes from its last report timestamp in
`state/reports/`; the first report covers one day. Reports survive restart.
Cohorts keep a fixed denominator; reopening lowers completion, and retirement
is not a repair. New work outside a cohort counts only requirements in its
source, kind, and policy scope whose first-open transition follows capture.
Previously closed requirements, later retries, and reopened or recreated old
rows do not count as discoveries. The cutoff preserves ordering when capture
and discovery share a clock timestamp. Legacy cohorts without the cutoff show
known later discoveries and the count with unknown same-time ordering; their
exact new-work count is unavailable while that ambiguity exists.
Unbounded, paused, blocked, or incompatible cohorts have no
percentage or ETA. H11 supplies a local shadow inventory only; later rollout
revisions supply repair execution and publication progress.

## Work isolation and artifact recovery (H12)

Ordinary parse, project, and link units run through `schedule.derive`. Each
attempt starts durably, isolates its transaction, and records a classified
outcome. One failed scope does not block healthy acquisition or independent
derivation. Unchanged blocked inputs remain visible and do not run again in
every cycle. Transient I/O failures and interrupted work currently receive a
60-second retry deadline. No additional host budget is created.

`swingset reparse --kind KIND --since TIME` is an explicit operator retry for
its selected snapshots. It enqueues their work and advances retry generation
for prior failed attempts in the same transaction. Doctor and inventory scans
do not request retries. A later verified local artifact restoration can also
be followed by an explicit retry; simply observing the file does not erase
the failed outcome or claim completed derivation.

Cycles share one `LocalCheckpointRecovery` through an optional `Archive`
recovery argument. Healthy artifact reads do not enumerate backups. On a
missing or corrupt body or extract, recovery considers only local private
checkpoints containing that exact digest path. Before first use it verifies
the complete physical file closure, actual SQLite schema and integrity,
foreign keys, retained candidate markers, and referenced artifacts. Checkpoint
SQLite is opened with `mode=ro&immutable=1` and explicitly closed.

Successful qualification is reused within the helper only for identical
manifest bytes. Every restore independently verifies the selected file's
size and stored hash, then its decompressed body hash or serialized-extract
hash against the required historical digest. It atomically restores those
bytes and records checkpoint and manifest provenance in the cycle summary.
Failed qualification is not cached, and modification times never establish
artifact validity. Recovery does not fetch a current replacement page or
rewrite a historical snapshot's digest.

If no valid matching checkpoint is available, `ArtifactUnavailable` retains
artifact kind, required digest, reason, and attempted local checkpoints. The
unit becomes unavailable, its requirement and pending work remain, and other
units continue. Recovery cannot write into a checkpoint. After recovery,
normal parsing and admission still govern whether output can commit.

The offline H12 acceptance cases exercise a real failed parser beside a
healthy fetch and projection within three fake-clock cycles, with one failed
attempt for unchanged inputs. A separate cycle test restores historical bytes
from an actual SQLite checkpoint and commits their interpretation; the case
without a backup retains unavailable evidence while healthy work progresses.
These scenarios use ordinary work, without correction-only publication.
H12 does not enable requirement repair kinds, kind pauses, or H14 scheduling
objectives.
