# Rehearse and transfer the held worker

Part of the [PostgreSQL migration plan](README.md). These are target
requirements, not evidence that migration has happened. Keep the numbered
steps in order and preserve the recorded operating holds.

## 11. M7 — Dokploy provisioning and full-size rehearsal

### Provisioning implementation

Use Dokploy raw Compose mode (`composeType: docker-compose`, `sourceType: raw`)
with auto-deploy disabled. Complete the CLI contract's rehearsal against a
run-owned disposable application, then use `compose create`, `compose one`,
`compose update`, and `compose deploy` through the pinned CLI wrapper. Create
in the supplied environment; record `composeId`; read back the effective
configuration. Ensure fresh-volume behavior is false through the verified
omission/default contract or an explicit false-valued HTTP fallback; never
pass `--freshVolumes false` to an unverified presence-only CLI flag.
Set `autoDeploy=false` through a verified CLI encoding or the narrow HTTP
fallback before deploying. Preserve the documented API contract as the
reference for fallback request bodies, not a second default implementation.
[Dokploy Compose API](https://docs.dokploy.com/docs/api/compose).

Use a run-owned description/label to recover an ambiguous create response.
Before retrying a mutation, inspect existing objects and compare desired state;
never blindly repeat create/deploy after a timeout. Use the CLI contract's
operation/deployment IDs and reconciliation rules. If neither the CLI nor its
verified HTTP fallback supports a required operation, return code 3 with
sanitized details. Do not upgrade Dokploy or create a second unmanaged Compose
deployment as an automatic fallback. Poll with `deployment all-by-compose`;
verify running containers through CLI discovery and host inspection over SSH.

Build and test the image on AMD64. Transfer an OCI/Docker image archive with
SHA-256 verification over SSH and load it on sandile.dev. Use a unique immutable
local tag plus verified image ID with `pull_policy: never`; inspect the running
image ID against the archive receipt. This avoids needing registry publication.
Base PostgreSQL uses its resolved registry digest. Dokploy must deploy on the
same host where the worker image was loaded. No source checkout/push is needed.

Generate distinct run-owned external volume names for PostgreSQL, artifacts,
and temporary space. Create them once over SSH, label with run ID, and declare
`external: true` in Compose so replacing the application cannot recreate them.
PostgreSQL 18 mounts at `/var/lib/postgresql` with explicit
`PGDATA=/var/lib/postgresql/18/docker`. Verify effective mount and persistence
against the pinned official image; do not use the older data-only mount path.
Use SCRAM passwords, never trust authentication. Init secrets live in a private
host directory mounted only to initialization/maintenance containers; normal
PostgreSQL startup retains only its necessary secret. Worker role secrets are
readable by UID 10001 only. Do not dump rendered secret configuration to logs.

Start with `postgres` limited to 2 CPUs/8 GiB and `worker` to 4 CPUs/16 GiB.
Enforce actual Compose memory/CPU limits and verify through container inspect.
PostgreSQL defaults for this deployment: `shared_buffers=2GB`, `work_mem=8MB`,
`maintenance_work_mem=256MB`, `max_connections=30`,
`max_parallel_workers_per_gather=0`, `max_wal_size=4GB`, `min_wal_size=1GB`.
Keep fsync, full-page writes, synchronous commit, and autovacuum enabled.
Set UTC and C collation explicitly. These are conservative starting limits,
not a claim that every 8-core/60-GB host has room for them.

### Capacity and performance gates

Before allocating, observe host resources for 5 minutes without stopping other
workloads. Require at least 30 GiB MemAvailable at every sample and enough CPU
headroom for the 6-CPU cap; on an 8-core host require existing average utilization
at most 25% across that interval. Fail with code 5 otherwise; do not resize
other applications. During rehearsal run heavy migration/build/audit phases
serially and use the same limits intended for production.

Let `S` be checkpoint SQLite bytes and `A` be included artifact bytes. Before
measurement require target free disk of at least `max(200 GiB, 8*S + 3*A +
50 GiB)` and at least 20% filesystem space/inodes free. After rehearsal, compute
the production requirement from measured database/index/WAL, artifacts, build
temporary peak, two local complete checkpoints, image space, and a disposable
restore drill, plus 30% margin. Use the larger of measured requirement and the
20%-free floor. Fail safely before every heavy phase if free disk falls below
the reserved remaining requirement. PostgreSQL `max_wal_size` is not a hard
disk cap; monitor actual WAL growth during long snapshots.

Required measured limits: worker peak container memory <12 GiB (75% of limit),
PostgreSQL peak <6 GiB, no OOM or sustained swap-in, full import/audit/restore
each completes within 4 hours, complete remote checkpoint within 2 hours,
and full retained-evidence replay plus build within 24 hours. A normal eligible
cycle must stop admitting work at its 12-minute budget and exit within another
60 seconds. Inspect regressions with query counts/EXPLAIN on the clone; fix
round-trip loops, missing indexes, or unbounded buffering before increasing
limits. Do not silently relax these gates to obtain a passing receipt.

Add a synthetic 256 MiB dependency manifest with small members. Streaming
reader/hash and importer/auditor additional peak RSS must stay below 128 MiB
over their small-manifest baseline and must not grow proportionally to total
manifest size. Also exercise the largest real retained field and fail if an
unsupported single member exceeds the defined bound.

### Rehearsal sequence

1. Acquire an authorized complete checkpoint and freeze its identity. Import
   into run-owned rehearsal resources; no source acquisition or public writes.
   Apply M3 independent storage and artifact audit before any runtime acceptance.
2. Make a separate replay clone. Inject a fixed clock, identical accepted policy,
   retained source evidence, and deterministic IDs into both frozen legacy and
   PostgreSQL runs. Replay only offline parse/project/link/build work, using
   the same checkout code semantics in both engines. Retain the actual deployed
   baseline separately. Do not compare ARM64/Nix Parquet file bytes to AMD64
   Docker bytes as the equivalence criterion.
3. Compare published logical table schemas, ordered IDs/rows, nulls, identities,
   references, coverage, provenance relationships, findings, and lineage.
   Allow only explicitly named run/runtime/receipt metadata differences. Any
   changed public domain row relative to the engine oracle is a failure. A
   difference caused by undeployed policy/code relative to the public baseline
   remains an unapproved release difference; it does not block a byte-faithful
   held import or authorize production acceptance.
4. Extend `HuggingFaceArchive` with an explicit revision, default `main`, for
   head queries, writes, and downloads. Use the run-specific branch
   `migration-rehearsal/<run_id>` in the existing private archive, created from
   its recorded head, and parent-commit compare-and-swap on that branch. Never
   advance production `main` from rehearsal. Add transport tests for branch
   isolation and lost upload acknowledgment. The replay worker receives no HF
   token; a separate archive process has a hardcoded allowed repository/branch
   for this run. Create/upload/download the rehearsal checkpoint there, restore
   into another disposable target, and rerun the audit. All writes to the
   public dataset repository are rejected by the rehearsal transport interface.
5. Kill a worker mid-write; restart PostgreSQL; restart/recreate containers with
   the same volumes; lose a backup connection; restart during restore marker
   installation. Verify no lost committed state, duplicate issued requests,
   silently cleared hold, or mismatched activation. Source/Hub effects are fake.
6. Produce SCALE/HOST receipts, precise peak-space accounting, timings, and
   the calculated cutover duration. Duration is the sum of measured final
   checkpoint, transfer, import, audit, restore drill, and 30% contingency.

**M7 exit:** full-size storage parity, offline engine parity, complete checkpoint
restore, lifecycle faults, resource floors, and actual Dokploy configuration all
pass, including OBS log/health visibility and an explicit record of unavailable
monitoring/delivery features. ADMIN must pass against this server, including
rehearsal create/update/deploy/read/log/stop and volume-preserving cleanup or
its verified fallback. Rehearsal image/schema/config and admin-toolchain hashes
become the immutable cutover inputs.

## 12. M8 — Resumable production transfer under hold

Execute only with migration authorization and M0–M7 receipts for these exact
inputs. Recheck source schema/pin and installed target contracts; changes
invalidate dependent receipts. Each step writes its postcondition before the
next step. Hold the orchestrator's own run lock to prevent duplicate invocations.

1. **Preflight again.** Confirm intended source and target identities, holds,
   public head, capacity, credentials, and deployment hashes. A public-head or
   source semantic change returns code 4; do not reconcile by guessing which
   release is authoritative. Require the recorded production operator hold.
2. **Fence the source persistently.** Preserve/create the source hold as
   authorized; stop and mask `swingset-cycle.service`, `.timer`,
   `swingset-backup.service`, `.timer`, `swingset-summary.service`, `.timer`.
   Record their previous masks/states. If NixOS-generated persistent unit paths
   prevent masking, do not overwrite them: retain the durable `operator-hold`
   enforced by all three service conditions and apply runtime masks to all six
   units. Verify effective conditions and that a reboot cannot bypass the file
   hold; record this exact fencing mechanism. Verify all inactive, no manual writer,
   and ownership lock acquisition. Keep the final-checkpoint helper's file lock
   while it snapshots; do not nest a CLI opener that reacquires the same lock.
   Nix rebuilds/activation are excluded from this window. The new host's PG
   locks cannot fence this SQLite writer.
3. **Capture the final source checkpoint.** Invoke the frozen source's existing
   checkpoint interface in a maintenance process under the verified hold.
   Do not accept the current checkout's runtime inputs or auto-migrate source.
   Remotely verify the private checkpoint commit and complete artifact manifest.
   Record source/public identities again. This checkpoint supersedes rehearsal
   input for transfer; its fingerprint becomes the production attempt identity.
4. **Import into fresh production resources.** Create a separate held Compose
   application/volume set from rehearsal. Verify worker image ID. Load only the
   final checkpoint using M3. Keep worker CONNECT withheld until load/audit
   finish. Runtime container stays in inactive supervision with restore hold.
   Store the source's configured publication mode, cycle budget, and scheduling
   settings in the target configuration without launching their commands.
5. **Verify independently.** Check every row/table/file, ordering sequence,
   budget/control cursor, baseline/pending publication, profile, and identity
   binding. Grant normal roles and run read-only doctor. Do not run `cycle
--dry-run`: it performs acquisition. Do not run normal input acceptance,
   build, or publication on production under this migration plan.
6. **Complete restore activation while retaining operator hold.** Reconcile
   only existing public outcomes. Clear restore markers in the M4 order.
   Recreate the worker container and verify `held`, correct identity, live
   supervisor, no active jobs, and zero source/public writes. Do not clear or
   expire imported application pauses manually.
7. **Prove new recovery.** Create and remotely verify a complete PostgreSQL
   checkpoint through the permitted held-maintenance path. Restore that actual
   checkpoint to isolated resources and rerun the logical/artifact audit.
   Restore must not update production archive pointers or public data.
8. **Record relocation.** Update operating docs with Dokploy application/server,
   external volume names, image identity, database semantic/storage versions,
   final old/new checkpoint commits, current public head, holds, and tested
   restore command. Include the CLI pin, command-contract receipt, application
   and server IDs, and the run directory needed by `inspect` and `logs`; another
   agent must be able to resume without a dashboard session. Record old writer as retired-but-preserved and persistently
   fenced. Never describe a held supervisor as completed collection progress.

Idempotency: inspect phase postconditions before repeating any remote step.
If a final checkpoint upload timed out, reconcile its manifest/commit. If
import timed out, use transactional table receipts. If database/volume binding
or public reconciliation is ambiguous, leave both writers held and return
code 4. Never perform a destructive “reset and retry” against a resource whose
ownership or activation state is uncertain.

**M8 exit:** the original dataset is installed and independently verified on
Dokploy/PostgreSQL, still held; the old writer is persistently fenced; a new
complete remote checkpoint has passed a fresh restore drill. This completes
the hosting/database migration. Existing H16/H17/history release work remains
at its recorded gates and does not require intervention to finish this scope.

## 13. M9 — Recovery, cleanup, and handoff

Automatic failure handling favors intact evidence. Before any new target
acquisition, control mutation, input acceptance, or publication, reverting the
hosting selection can point back to the unchanged source. Stop/fence target,
verify source checkpoint/public head still match, and restore only the old
unit enable/mask configuration recorded before migration. Keep its original
operator hold. Never resume collection as an implicit rollback operation.

After any new target domain mutation, the SQLite source is stale. Do not use
image rollback or restore old unit scheduling to recover service. Repair
forward or restore a compatible PostgreSQL complete checkpoint under hold,
then reconcile public state. Reverse PostgreSQL-to-SQLite transfer is outside
this implementation and must not be improvised. Do not erase uncertain remote
outcomes or reset source budgets to make recovery proceed.

Clean only run-labeled rehearsal/test containers, databases, temporary volumes,
and temporary images after their receipts are captured and production recovery
passes. Verify ownership labels before every delete. Never run global Docker
prune, `compose down -v`, Dokploy fresh-volume deployment, or source-state
deletion. Retain source VM/state/runtime and final SQLite checkpoint for at
least 30 days; this plan does not schedule their automatic destruction.
Retention keeps the newest two verified new complete checkpoints as they become
available, plus the migration pair. Never delete the sole new checkpoint merely
because the second has not yet been produced while operation remains held.

Remove private scratch credentials that no longer serve retained resources;
retain runtime secrets in the target's private secret directory. Delete local
transferred image archives after hash verification and successful load. Keep
redacted gate receipts and the migration status document; do not commit raw
production rows or credentials. Run formatting and inspect `jj diff` for
unrelated changes. Commit/push only if explicitly requested.

Final handoff must name: completed packages; exact source/target identities;
database/schema/image pins; every acceptance group and evidence path; first
new checkpoint/restore result; current hold and public-head state; old writer
fence; measured resource/timing results; and any blocked condition. A local
implementation-only run must say production transfer was not performed.

The agent is finished when M0–M9 have their actual required receipts. If an
external prerequisite is unavailable, finish all independent code/test work,
leave a precise `blocked.json`, and report that limitation once. Never invent
an approval, relax an invariant, or claim migration completion to satisfy an
unattended execution goal.
