# Package, schedule, and monitor the worker

Part of the [PostgreSQL migration plan](README.md). These are target
requirements, not evidence that migration has happened. Keep the numbered
steps in order and preserve the recorded operating holds.

## 9. M5 — Worker image and persistent scheduler

### Packaging and configuration

Build a multi-stage image from pinned Debian-based Python 3.12 and PostgreSQL
18 client tooling. Install locked dependencies with uv at build time, including
Psycopg C/libpq. Keep `/app/src/swingset`, `/app/pyproject.toml`, `/app/uv.lock`,
`/app/config`, and `/app/overrides` together; install the project editable so
the imported source remains there, then make `/app` read-only at runtime.
Assert `capture_runtime()` includes both lock inputs, SQL migrations, and the
actual imported Python sources. Record interpreter/distribution and PG schema
implementation identities without credentials or database hostnames. Stable
semantic SQL/type/collation contracts enter recipe identity; volatile server
statistics, DSNs, and harmless server patch labels do not.

Run worker as UID/GID 10001 with umask 0077, read-only root filesystem, dropped
capabilities, no-new-privileges, and no Docker socket. Use a one-shot init
process to set ownership of fresh volumes, then exit. Persist
`/var/lib/swingset`; use a separate disk-backed `/var/tmp/swingset` volume for
build/import temporary space. Set Python/uv/cache/temp paths explicitly so
startup performs no downloads or writes to `/app`. Mount worker/control/reader
secret files read-only at `/run/secrets`; worker has no superuser/migration
password. Supervisor invocation and CLI dispatch select the appropriate role.

### Supervisor contract

Add `swingset supervise` and `swingset health --json`. Supervisor runs as PID 1,
reaps children, checks holds, and invokes existing CLI commands. It does not
reimplement watch scheduling. It owns a lifetime `supervisor.lock` on the
artifact volume, so a second supervisor fails before launching jobs. Use
stop-before-start replacement and `stop_grace_period: 60s` in Compose.

Persist a versioned schedule journal under `state/scheduler/` using durable
atomic writes and a separate file lock. Track each job's last due slot,
eligible time, start, finish, exit code, retry deadline, and child identity.
Do not depend on wall-clock process uptime. Define times in UTC explicitly:

| Job     | Schedule and invocation                                                                                                                                                                                                                                                              |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| cycle   | Quarter-hour UTC slots; deterministic jitter `SHA256(dataset_id, slot)` modulo 121 seconds; `cycle --budget 12m --timer` with the recorded source mode (`--publish` or `--dry-run`). New test installations default to `--dry-run`; production preserves the source mode under hold. |
| backup  | Mon–Thu 04:00 UTC; Fri–Sun 04:00, 12:00, 20:00 UTC; `backup`; wait for command ownership and retry a failed checkpoint after 60 seconds.                                                                                                                                             |
| summary | Daily 08:00 UTC; `summary`; retain existing report cursor semantics.                                                                                                                                                                                                                 |

On restart coalesce missed slots to one outstanding invocation per job. When
a hold suppresses a slot, mark it held/consumed; do not accumulate a burst for
later. On hold release wait for the next normal slot. A child interrupted by a
restart produces an interrupted receipt; ordinary cycle recovery runs at the
next eligible slot, backup retries its pending checkpoint, summary retries
once if its report cursor shows no completion. Wall-clock rollback must not
execute a consumed slot twice; forward jumps coalesce.

Allow one child per job, with database/file locks serializing data work. If a
cycle overlaps a backup, its zero-wait lock result is a skip. Backups get
priority over launching a cycle when both are due in the same scheduler turn.
Reports may run concurrently with data work but serialize their own cursor
files. Never create multiple waiting backup children. A failed backup remains
visible while its single retry is pending.

`operator-hold` prevents all three scheduled jobs. `RESTORE_PENDING`, missing
identity, schema mismatch, or an external deployment restore hold also prevents
them. Preserve this distinction in health output. Manual mutating CLI commands
must check `operator-hold`; an explicit `--maintenance-under-hold` capability
is allowed only for named migration/checkpoint/verification operations under
the migration driver. It never enables fetch, cycle, discover, or publish.
Do not expose a blanket `--ignore-hold` switch. Pauses/status remain available
when no restore is pending; restore pending blocks control changes too.

On SIGTERM stop launching work and send SIGTERM to active child process groups.
Let workers finish/roll back their bounded write. Kill surviving groups at
50 seconds, reap them, and exit before Docker's 60-second limit. Do not release
the parent's protection while a child is alive. After DB loss, child stops
work and exits; supervisor records failure and leaves normal recovery for a
fresh invocation. No infinite rapid restart or automatic source-request retry.

Liveness checks supervisor heartbeat age (<30 seconds) and ability to reap;
they do not restart a correctly held application. Readiness separately checks
DB connectivity/version, identity binding, restore state, and resource floors.
Expose `held` as an intentional operating status. Progress reports last
successful cycle/backup/summary, skips, retries, and pending publication. An
active worker with no successful cycle for 45 minutes is degraded; a backup
overdue by 2 hours is degraded. Log structured JSON to stdout with rotation at
Docker (10 MiB × 5 files). Use the Dokploy integration below for inspection;
no new public HTTP endpoint or application notification sender is required.

**M5 exit:** the final image passes source-capture checks, non-root/secret
checks, held startup, fake-clock schedules, restart/missed-slot/clock-jump
cases, duplicate supervisor, overlap, backup retry, and shutdown tests.

### Monitoring and observability through Dokploy

Expanded 2026-09-14. Use Dokploy as the first place an operator goes to inspect
Swingset: select its Compose application, identify `worker` or `postgres`,
inspect logs and deployment history, and run the read-only health command in
the selected container terminal. Keep application-specific health computation
in Swingset and expose its result through these existing interfaces. Do not
build a second operations dashboard in the pipeline.

#### Establish what this installation provides

Dokploy documents separate Compose service logs, service monitoring, and a
deployment view. These are useful for correlating a failing job with its
container and last deployment.
[Compose operations](https://docs.dokploy.com/docs/core/docker-compose).

The dedicated monitoring guide labels its history/collection system Cloud-only;
the production guide repeats that restriction for self-hosted installations.
The general feature list also describes service resource monitoring. Therefore
M0 must record actual edition/version and test live resource views separately
from historical charts, retention, and threshold notifications. Treat historical
monitoring as unavailable on sandile.dev until verified; do not promise Cloud
features from a self-hosted login page.
[Monitoring](https://docs.dokploy.com/docs/core/monitoring),
[self-hosted guidance](https://docs.dokploy.com/docs/core/guides/production-hardening).

Add `observability-capabilities.json` to the run receipts with the installed
version/edition and each capability marked `verified`, `unavailable`, or
`not_tested`: service logs, deployment logs/history, container terminal,
container health display, live service resources, host resources, historical
metrics/retention, native notification events, and external monitoring. Record
how each verified result was observed. Documentation alone is not a receipt.

| Question                                     | Use Dokploy for                                                                                                                    | Swingset must supply                                                                        |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Did a deployment fail or change the runtime? | Deployment result/logs and the running container/image identity.                                                                   | Startup event with source, image, schema, dataset, and restore-generation identities.       |
| Which process is failing?                    | Separate worker/PostgreSQL logs and container terminal.                                                                            | Structured errors with job, run, attempt, stage, and stable error code.                     |
| Is the host or a container under pressure?   | Verified host/service CPU, memory, disk, and network views. Historical charts only when available.                                 | Job durations/peaks and disk safety checks to connect pressure to pipeline work.            |
| Is the process alive?                        | Container state and health result where the installed UI exposes it. Otherwise inspect Docker health over the existing SSH access. | Cheap Docker HEALTHCHECK with explicit liveness semantics.                                  |
| Is collection progressing correctly?         | Read `pipeline_status` events in Logs or run the health command in Terminal.                                                       | Hold-aware job freshness, authoritative backlog age, source gates, and last committed work. |
| Is recovery current?                         | Native backup events only for backups Dokploy actually manages.                                                                    | Last remotely acknowledged complete Swingset checkpoint and last successful restore drill.  |
| Is PostgreSQL blocked?                       | PostgreSQL logs and resource view.                                                                                                 | Bounded database diagnostics for connections, transactions, waits, and storage.             |

The custom PostgreSQL container in this Compose application is not a native
Dokploy database resource. Its application checkpoint uploads do not automatically
produce Dokploy's database-backup notifications. Never use an unrelated successful
Dokploy backup as proof that Swingset can be restored.

#### Make the Logs view useful

Extend `src/swingset/log.py` rather than adding another logger. It currently
writes structured `key=value` records to stderr; the migration's JSON output is
an explicit format change. Update consumers/tests identified by the port ledger.
Emit one JSON object per line, with `format=swingset-log-v1`, UTC `timestamp`,
`level`, `event`, `component`, and applicable `run_id`, `attempt_id`, `job`,
`stage`, `dataset_id`, `restore_generation`, `duration_ms`, `outcome`, and
`error_code`. Startup also names the build/source identity. Keep identifiers
in logs; do not use unbounded run/watch/event IDs as future metric labels.

Emit these events at their actual transaction/operation boundaries:

- `runtime_started`, `runtime_stopping`, `job_started`, `job_finished`,
  `job_failed`, `job_interrupted`, `job_skipped_overlap`, and `job_held`.
- `write_deadline_exceeded`, `database_connection_lost`, `lock_wait_finished`,
  `publication_uncertain`, and `restore_verification_failed`.
- `checkpoint_local_complete`, `checkpoint_remote_verified`,
  `checkpoint_upload_failed`, and `checkpoint_restore_verified`.
- `pipeline_status` once per minute and immediately on state changes;
  `health_condition_opened` and `health_condition_resolved` for actionable
  conditions. Repeat an unresolved condition at most once per 30 minutes.

`job_finished` means the child succeeded; separately report whether authoritative
work advanced. A cycle containing only polite deferrals is not failed, and a
successful process with a growing eligible backlog is not proof of progress.
Only `checkpoint_remote_verified` updates recovery freshness. Flush records
promptly so a crash does not hide the last completed transaction boundary.

Keep each record below 16 KiB. Log counts, hashes, source names, hostnames,
and error codes; omit credentials, DSNs, response bodies, raw identity evidence,
URL query strings, and SQL bind values. Sanitize exception chains before output.
The PostgreSQL container writes operational messages to its container stream;
keep query-text/parameter logging off by default, including SQL statements in
error logs where they could contain imported data. Use application query labels
and durations for routine slow-path diagnostics.

Configure Docker `json-file` logging with `max-size: 10m` and `max-file: "5"`
for both containers and verify Dokploy can read it. This is a size budget,
not a promise of a number of days. Container replacement can remove access to
old logs. Keep durable job/checkpoint receipts in the artifact volume and its
complete checkpoints; capture bounded, redacted log excerpts with migration
and incident receipts. If an existing external log destination is available,
shipping can be configured separately without changing the event format.

Use the official CLI's `compose read-logs` through the migration wrapper, backed
by the documented `compose.readLogs` operation, for bounded tail/time filters.
M7's diagnostic collection uses at most 500 lines from the last hour per affected
container, then redacts them locally. Resolve actual container IDs through the
verified CLI discovery command after every replacement; use the recorded narrow
HTTP fallback only if this CLI read is incompatible. The log
viewer is not assumed to index JSON fields or retain logs indefinitely.
[Compose log API](https://docs.dokploy.com/docs/api/compose).

#### Separate liveness, readiness, and progress

Extend the planned health command with `--check live|ready|progress` and
`--json`. Without `--check`, print the complete status and exit zero if the
status was read successfully; individual checks return zero only when their
own predicate passes. A held application can be live and ready while its
progress state is `held`. `--check progress` succeeds for a ready, intentionally
held application with fresh telemetry and no unsuppressed critical condition,
or for an active application with no overdue/stalled-work condition. Unknown
telemetry fails the progress check. Restore-pending can be live but not ready.

The supervisor writes a lightweight heartbeat every 10 seconds. Docker runs
`swingset health --check live --json` every 30 seconds, with a 5-second timeout,
three retries, and a 60-second startup grace. The probe reads only the heartbeat
and process identity and verifies freshness (<30 seconds); it never queries
the full database, runs doctor, accepts inputs, or contacts a source/Hub.
PostgreSQL gets `pg_isready` at the same interval/timeout/retries, with a
120-second startup grace. That proves server responsiveness; it does not prove
the application role, schema, or evidence are valid.

Readiness sampling runs independently of expensive pipeline work, once per
minute, with a two-second query timeout and a bounded read-only connection.
It checks role connectivity, expected schema, database/volume identity, restore
state, and storage floors. Do not run a full derivation scan every minute.
Write the last status atomically; a sample older than 90 seconds is `unknown`,
not healthy. Failed telemetry must not become zero backlog or reset a success
timestamp. This sampler must continue while a child is building or awaiting
backup ownership, and while operator hold suppresses jobs.

`pipeline_status` includes `live`, `ready`, `operating_state`, `observed_at`,
`last_sample_error`, hold reason, current child job/run, last job exit/result,
last committed-work time, last cycle success, next eligible slots, oldest
eligible work age, last remote checkpoint/commit, last restore drill, pending
publication age, and active condition codes. Use known durable state; expensive
authoritative backlog counts come from bounded periodic reporting or the last
verified report, with its own observation time. Queue hints are labeled hints.

Configure restart-on-process-exit (`unless-stopped`) for the containers, but
do not rely on Docker Compose to restart a running unhealthy container.
Health status and restart policies are separate controls. The supervisor's
existing shutdown/failure protocol remains responsible for safe process exit;
an unresponsive-process condition stays visible without an automatic restart
loop around an uncertain publication.
[Compose health and restart controls](https://docs.docker.com/reference/compose-file/services/).

These read-only commands belong in the Dokploy application's operating notes
and are run in the selected **worker** terminal after implementation:

```sh
swingset health --json
swingset health --check ready --json
swingset doctor --json --state /var/lib/swingset
```

Use doctor only for deeper diagnosis; health is the inexpensive frequent check.
Do not put `cycle --dry-run`, `summary`, restore, or publication in a health
probe: those commands can mutate state, advance cursors, or acquire evidence.

#### Conditions and notification ownership

The following thresholds are operational choices for this migration, not
built-in Dokploy pipeline alerts. Swingset evaluates them and exposes the
condition codes in its health/log output:

| Condition               | Trigger and handling                                                                                                                                                                                                                      |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cycle_overdue`         | No successful eligible scheduled cycle for 45 minutes while active. Begin the clock at activation/hold release if no successful cycle exists. Suppress while held; retain and display the actual last-success age.                        |
| `eligible_work_stalled` | Eligible authoritative work exists and no committed work advances for 60 active minutes. Exclude documented host-budget waits and disabled/paused scopes; show those reasons separately.                                                  |
| `checkpoint_overdue`    | A required backup slot is still unsatisfied two hours after its due time. Use the UTC calendar, not a fixed age that misclassifies weekday/weekend cadence. Suppress missed-slot alarms while held; always display absolute recovery age. |
| `publication_uncertain` | Any unresolved external publication outcome. Report immediately and retain the existing fence/reconciliation rules; never fix this by restarting until it disappears.                                                                     |
| `state_not_ready`       | DB unreachable or schema/identity mismatch on two consecutive samples. Identity mismatch is critical immediately. Operator hold never suppresses these conditions.                                                                        |
| `write_timeout`         | Each rolled-back 45-second write deadline is an error event; three occurrences within 15 minutes open a persistent condition.                                                                                                             |
| `storage_low`           | Free bytes below the run's safety reserve or filesystem free capacity/inodes below 20%. Report even when held; preserve existing admission/maintenance safety gates.                                                                      |
| `worker_memory_high`    | Worker usage above 12 GiB for five consecutive minute samples. Record whether the source is cgroup usage or process RSS; do not mix those measurements. A killed/OOM process is an immediate error.                                       |
| `postgres_memory_high`  | PostgreSQL usage above 6 GiB for five consecutive minute samples, collected through verified host/container monitoring. A worker cannot infer a sibling container's memory from its own cgroup.                                           |
| `telemetry_stale`       | Expected sample age exceeds 90 seconds. Show unknown coverage and the last error instead of clearing other conditions.                                                                                                                    |

The supervisor can evaluate pipeline state and its own cgroup/resource checks.
Host and sibling-container checks require the verified Dokploy collector or a
separate authorized monitoring process. Record an unavailable evaluator as
`not_monitored`; do not grant the worker a Docker socket to fill that gap.

Dokploy's native notification settings cover deployment/build events, its own
backup events, and Dokploy restarts, with providers such as email, ntfy, or a
webhook. Reuse the operator's selected provider for those native events.
Because the worker is prebuilt and transferred here, a local/CI image-build
failure also needs its own build receipt; it may never reach a Dokploy build
notification.
[Notification events/providers](https://docs.dokploy.com/docs/core/overview).

If Cloud-style monitoring is actually available, select worker and PostgreSQL,
start with 20-second collection and seven-day retention, and set initial host
CPU/memory thresholds to 85%/80%. Verify measured retention overhead. The
documented threshold notifications apply to the server, not individual
containers; they cannot replace Swingset's per-container or pipeline conditions.
[Monitoring configuration](https://docs.dokploy.com/docs/core/monitoring).

Notification webhooks documented by Dokploy send events **out** to a receiver.
Do not treat that feature, or its metrics callback, as a documented general
ingestion endpoint for `checkpoint_overdue` and other custom alerts. Custom
condition delivery requires an external evaluator/receiver integration.
[Webhook direction](https://docs.dokploy.com/docs/core/webhook).

For this migration, prepare the native notification configuration for the
already selected destination; if none exists, record `delivery: unconfigured`.
Do not guess recipients or send test messages as part of research or local
tests. A delivery drill uses a local fake receiver until the destination and
permission to notify it are supplied. Report notification delivery as untested
until a real authorized delivery is observed. Missing delivery does not block
the held transfer, but the handoff must not describe it as alert-covered.

#### PostgreSQL depth and host-failure coverage

Add `swingset health --database-details --json` as an on-demand diagnostic with
the reader role. Return connection count versus its limit, oldest active and
idle-in-transaction ages, blocked-session count/wait classes, deadlock count,
database bytes, and the observations needed for WAL/temp growth. Use cumulative
counters as rates only when two samples share the same stats-reset identity.
Set `application_name` by worker/control/backup role so diagnosis can attribute
sessions without exporting SQL text. Use documented statistics views/functions
and grant only required monitoring access on this dedicated cluster; inaccessible
fields return `unavailable` rather than widening the worker's privileges.
[PostgreSQL statistics](https://www.postgresql.org/docs/18/monitoring-stats.html).

Do not install Prometheus, Grafana, a PostgreSQL exporter, tracing, or centralized
log storage as a prerequisite for this held migration. The named log/health
contracts leave a clear later integration if historical query analysis or custom
alert routing is needed. Deploying such tools through Dokploy would use Dokploy
as their host; it would not make their dashboards or custom rules native features.

Dokploy and Swingset on the same server cannot independently report that the
whole host has lost power or connectivity. An existing monitor outside
sandile.dev should check Dokploy reachability and, when integrated, missing
Swingset status/checkpoint signals. A reachable login page proves only that
the control panel is reachable. Without an external monitor, explicitly record
`host_failure_detection: unavailable`. No public pipeline endpoint is needed
for the initial log/terminal workflow.

#### Work-package changes and acceptance

M0 records the capability matrix and existing notification destination without
changing it. M5 implements structured events, status sampling, probes, and
hold-aware conditions. M6 adds OBS tests. M7 verifies actual Dokploy visibility
using rehearsal resources; M8 records monitoring/notification coverage in the
handoff. Keep these additions in the existing work packages.

OBS passes when logs from both services can be retrieved, startup/deployment
identities correlate, healthy-held startup does not cause a false pipeline
alarm, a stale heartbeat fails liveness within two minutes after startup grace,
DB loss degrades readiness while liveness stays valid, and deliberate stale
cycle/checkpoint/telemetry samples produce the expected codes. Simulate high
memory/disk samples in tests; do not exhaust production resources. Verify
recovery clears the condition, elapsed holds do not accumulate false alarms,
restarts preserve success timestamps, and no probe causes a source request,
public write, input acceptance, or report-cursor mutation.

The rehearsal also proves log rotation, secret redaction, checkpoint event
meaning, and the installed UI/API fallback for container health. Historical
metrics and real notification delivery have separate capability receipts; an
unavailable feature is not a failed code test and is never recorded as enabled.
Link the relevant Dokploy application views and read-only diagnostic commands
from the final operating handoff so an operator can investigate without SSH
for routine application questions.
