# PostgreSQL and Dokploy migration execution plan

Status: implementation specification, 2026-09-14. Owner: Sandile Keswa.
Target: `https://sandile.dev`, an 8-core Xeon with 60 GB RAM and 800 GB disk.
Free capacity, installed Dokploy version, and production state require preflight.

This plan specifies the code, tests, deployment, transfer, and recovery work for
a coding agent. Execute packages M0–M9 in order. Fix failed implementation checks
and rerun their dependent checks without asking for routine design decisions.
Writing this plan does not execute the migration.

**The completion target is a verified PostgreSQL deployment on Dokploy that
preserves the source's operating holds.** The recorded source is held. Migrating
it must not resume acquisition, publish a new dataset, approve history years,
adjudicate identities, or activate H18. Those are separate actions under the
existing [operating handoff](../docs/v2-resume.md) and
[v2 release gates](implementation-plan-v2.md). A held migration can complete
without resolving those gates. Test active scheduling on isolated state.

The agent can implement and test everything locally without production access.
An unattended production transfer additionally requires usable credentials,
access to the source writer, and authorization to execute the migration. These
cannot be inferred from a public login page. Section 3 defines the required
inputs and a noninteractive failure result when they are unavailable.

## 1. Decisions and invariants

This is the executable successor to the
[PostgreSQL research](../research/postgres-migration-plan-2026-09-14.md) and
[hosting review](../research/dokploy-migration-2026-09-14.md). Their experiments
are background evidence; they do not count as this plan's acceptance tests.
This plan owns migration order and implementation choices. Existing domain
documents continue to own collection, identity, derivation, and publication rules.

| Subject                    | Decision                                                                                                                                                                                                                                    |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Database                   | PostgreSQL 18; resolve and pin the current stable 18 minor and native image digest once in M0, then use that pin throughout the run. The research tested 18.6 on ARM64; that digest is not an AMD64 deployment pin.                         |
| Driver                     | Synchronous Psycopg 3 with the C implementation and system libpq; resolve a stable version compatible with Python 3.12 and record its exact resolution in `uv.lock`. No ORM, async conversion, connection pool, or production dual writes.  |
| First production semantics | Schema 14. Also port schema 15 for checkout/test compatibility, but ordinary startup never upgrades a database. A schema-14 import remains schema 14.                                                                                       |
| Source selection           | A newly verified immutable checkpoint of the actual held production writer. Scratch replay state and historical checkpoints cannot substitute for it.                                                                                       |
| State                      | PostgreSQL owns relational state. The artifact volume retains blobs, extracts, captured inputs, candidates, baseline link, reports, and holds.                                                                                              |
| Hosting                    | One raw Docker Compose application managed by Dokploy, containing `postgres` and `worker`. A dedicated PostgreSQL instance belongs to this application; use no Dokploy control database.                                                    |
| Networking                 | PostgreSQL only joins a Compose network declared `internal: true`. Worker joins that network and a separate bridge for outbound HTTPS. Neither publishes a host port or receives a Traefik route.                                           |
| Ownership                  | One worker replica. PostgreSQL command locks and an artifact-volume process lock prevent overlapping supported writers, including during container replacement.                                                                             |
| Migration destination      | Fresh database and fresh artifact volume per attempt. Never overwrite an active database, restore over the source, or reuse a partly imported target as production.                                                                         |
| Activation                 | Remove `RESTORE_PENDING` only after complete verification. Preserve `operator-hold` and application pause records. No migration command clears an operator hold.                                                                            |
| Release behavior           | Keep accepted inputs and public baseline unchanged during import. Runtime acceptance and replay happen only on disposable rehearsal state in this plan.                                                                                     |
| Recovery                   | Complete database-plus-artifact checkpoints remain the recovery contract. A standalone Dokploy volume/database backup is supplementary.                                                                                                     |
| VCS                        | Use `jj`; preserve unrelated edits. Do not commit, move bookmarks, push, or publish images to a registry unless execution authorization includes that action. Transfer the image over SSH instead.                                          |
| Administration             | Use a pinned official `@dokploy/cli` as the default Dokploy interface. The driver supplies credentials, explicit IDs, JSON parsing, verification, and retries. Use narrowly tested HTTP fallbacks for CLI gaps and SSH for host operations. |

At every completed import: source rows are accounted for exactly once; hashes
and identifiers survive; source request budgets and next-allowed timestamps do
not reset; unresolved work remains unresolved; missing evidence remains a gap;
controls remain effective; and neither host starts collection implicitly.

## 2. Deliverables and module ownership

Create the following files unless an existing module already owns the stated
implementation. Retain existing domain modules; move database mechanics behind
the `Database` interface. Do not grow `cli.py` into the migration implementation.

| Files                                                               | Responsibility                                                                                                                           |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `src/swingset/state/db.py`                                          | PostgreSQL lifecycle, checked schema profile, command ownership, write transactions, read snapshots, and stable application errors.      |
| `src/swingset/state/connection.py`                                  | Typed connection/cursor/row interface and private Psycopg adaptation. No SQL dialect translator.                                         |
| `src/swingset/state/postgres_schema.py`, `postgres_migrations/`     | Explicit, checksum-verified DDL installation and profile capability manifest.                                                            |
| `src/swingset/state/legacy_sqlite.py`                               | Immutable checkpoint reads and isolated historical fixture construction. Preserve the old migration files as legacy inputs.              |
| `src/swingset/state/control_lock.py`, `write_deadline.py`           | PostgreSQL control-turn lock and bounded write cancellation.                                                                             |
| `src/swingset/build/closure_manifest.py`                            | Hash-verified, bounded dependency-manifest access.                                                                                       |
| `src/swingset/migration/{cli,inventory,importer,audit,receipts}.py` | Offline inventory, explicit data transfer, independent equivalence checks, and durable phase receipts.                                   |
| `src/swingset/backup/{checkpoint,postgres,legacy}.py`               | Format dispatch, PostgreSQL snapshots/restores, and legacy checkpoint support. Keep existing archive transport owners.                   |
| `src/swingset/runtime/{supervisor,schedule,health}.py`              | Process supervision, persistent job eligibility, signal forwarding, and local health output.                                             |
| `tools/migration/{run,dokploy,source,capacity}.py`                  | Resumable host orchestration. Process arguments are arrays; credentials never enter command strings.                                     |
| `tools/migration/dokploy_cli.py`                                    | Run the pinned CLI noninteractively; validate/redact results, enforce timeouts, and own the tested operation-to-command mapping.         |
| `tools/migration/admin/{package.json,package-lock.json}`            | Exact official CLI dependency and npm dependency lock. Administration tooling only; excluded from the worker image and pipeline recipes. |
| `deploy/dokploy/{compose.yml,images.lock.json,README.md}`           | The deployment template, resolved base image identities, and exact operating commands.                                                   |
| `Dockerfile`, `.dockerignore`                                       | Reproducible source-layout worker image.                                                                                                 |
| `tests/postgres/`, `tests/migration/`, `tests/runtime/`             | Real database contracts, transfer/fault fixtures, and fake-clock scheduler tests.                                                        |
| `tools/test_postgres.py`                                            | Disposable PostgreSQL test lifecycle, role provisioning, and subprocess test execution.                                                  |
| `docs/dokploy-migration-status.md`                                  | Package status and pointers to actual validation receipts. No claims based only on this plan.                                            |

Update `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, development Nix
dependencies, and existing tests as needed. When implementation changes a
contract, update its owner: `state.md`, `operations.md`, `technology.md`,
`architecture.md`, `docs/runbook.md`, and `docs/implementation-status.md`.
Keep the old Nix writer configuration available for recovery; do not point it
at the new database or activate it as a side effect of building the image.

## 3. Run inputs, authorization, and receipts

Implement this local orchestration interface in M0 and complete its commands in
later packages. These are **new commands to implement**, not existing commands:

```sh
uv run python tools/migration/run.py prepare --run-dir /absolute/private/run
uv run python tools/migration/run.py check --run-dir /absolute/private/run
uv run python tools/migration/run.py rehearse --run-dir /absolute/private/run
uv run python tools/migration/run.py cutover-held --run-dir /absolute/private/run
uv run python tools/migration/run.py verify --run-dir /absolute/private/run
uv run python tools/migration/run.py status --run-dir /absolute/private/run
uv run python tools/migration/run.py inspect --run-dir /absolute/private/run
uv run python tools/migration/run.py logs --run-dir /absolute/private/run --service worker
```

`prepare` is local and read-only toward both hosts. `check` runs local tests.
`rehearse` may create only resources labeled for that run and read an authorized
checkpoint. `cutover-held` performs the production transfer. `verify` is
read-only except for a separately named disposable restore drill. `status`
reads receipts only. `inspect` uses the CLI to read the recorded application,
deployment, and container state, returning a redacted summary; `logs` resolves
the recorded application/service and retrieves bounded logs (`worker` or
`postgres`, default tail 500, since 1h). These commands resolve IDs from receipts
and live labels rather than requiring the user to copy IDs from the dashboard.
No command reads stdin or prompts. An existing valid
receipt is reused only after rechecking its postconditions.

Use a mode-0700 run directory outside the repository. `prepare` writes
`run.json` atomically with format `swingset-migration-run-v1`, a UUID `run_id`,
UTC creation time, source-tree file hashes, `jj` revision identity, lockfile
hash, chosen image pins, desired semantic schema `14`, and phase status.
Do not snapshot `.git`, `.jj`, credentials, local databases, or venvs.
Source-tree hashes, rather than a parent commit ID, identify a dirty checkout.

Read external inputs from environment or mode-0600 secret files. Persist only
their names and non-secret resource identities in receipts:

| Input                    | Contract                                                                                                                                                                                                           |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `SWINGSET_SOURCE_SSH`    | Existing SSH destination for the OrbStack/NixOS writer; SSH key/agent access and noninteractive sudo for its named Swingset units.                                                                                 |
| `SWINGSET_TARGET_SSH`    | Existing SSH destination for sandile.dev, with noninteractive Docker administration. Host identity must match the Dokploy target.                                                                                  |
| `DOKPLOY_URL`            | Default `https://sandile.dev`; verify TLS normally.                                                                                                                                                                |
| `DOKPLOY_API_KEY_FILE`   | API key for the selected environment's required read/manage operations. Driver reads this file and supplies `DOKPLOY_API_KEY` only to CLI children; CLI sends `x-api-key`. HTTP fallback uses the same key/header. |
| `DOKPLOY_ENVIRONMENT_ID` | Exact authorized Dokploy environment. Never select the first environment returned by an API.                                                                                                                       |
| `DOKPLOY_SERVER_ID`      | Optional explicit remote-server ID; omit only when inspection proves the environment uses the local sandile.dev host.                                                                                              |
| `SWINGSET_HF_TOKEN_FILE` | Existing authorized HF access for private checkpoint download/upload and public-head reads. Rehearsal children receive no public-write credential.                                                                 |

Record the actual session instruction authorizing execution in the run receipt,
including its scope and `preserve_operator_hold: true`. An instruction to execute
this plan is sufficient authorization for its transfer operations; do not ask
again or require the user to create a separate approval file. This planning
request alone does not authorize starting the transfer now.

Reuse already available authorized access. Do not enumerate unrelated secret
files or create credentials in the user's accounts. Generate fresh database
role passwords locally with a cryptographic generator; install them only in
the run's private directory and target's private secret directory. Existing
SSH trust configuration is required; do not disable host-key verification.

Each phase writes an append-only JSON receipt with `format`, `run_id`, `phase`,
`started_at`, `finished_at`, `status`, `input_sha256`, `previous_receipt_sha256`,
named checks, produced resource IDs, output hashes, and sanitized failure detail.
Write to a temporary file, fsync, rename, and fsync the parent. Store output
files before the receipt that names them. A receipt never contains a token,
password, full DSN, or private row values. Retain sensitive audit details locally.
Publish only redacted evidence under `research/verification/dokploy-migration/`.

Exit codes: `0` passed; `1` implementation/test failure; `2` invalid invocation;
`3` missing access/authorization or unsupported source/server contract;
`4` unsafe or changed external state; `5` capacity gate failed. Write
`blocked.json` for codes 3–5, naming the exact missing condition, completed
work, intact holds, and restart command. Continue independent local work before
reporting a block. Never convert a missing credential or failed gate to a pass.

### Official CLI contract for independent agents

The official CLI supports environment authentication, command/group help, and
`--json` output. Its commands are generated from Dokploy's schema. Use it for
routine administration instead of maintaining a parallel general-purpose API
client. The migration's desired state, resource ownership, and success checks
remain in `tools/migration/dokploy.py`; `dokploy_cli.py` owns process mechanics.
[Official CLI](https://github.com/Dokploy/cli).

**Install once and pin.** M0 resolves the stable published `@dokploy/cli` release,
records its exact version in the admin `package.json`, and generates
`package-lock.json`. Use Node 24, resolve its exact patch and npm version, and
record both with npm package integrity and CLI distribution hashes in
`admin-toolchain.json`. Initial dependency resolution may access the registry;
subsequent installs use `npm ci --prefix tools/migration/admin`. Invoke the
absolute `tools/migration/admin/node_modules/.bin/dokploy` path, never whichever
global executable happens to be on PATH. Record both package metadata version
and `dokploy --version`; a disagreement fails tool identity acceptance.

No global npm install or unpinned `npx` belongs in the execution path. Add admin
`node_modules` to ignore rules and the generated npm lock to formatter exclusions
if needed. The lock remains version-controlled when commits are authorized.
Install this tooling on the agent/controller, not in Swingset or PostgreSQL.
A failed compatibility check does not automatically upgrade the server or change
the CLI pin midway through a run.

**Credentials and process invocation.** Read `DOKPLOY_API_KEY_FILE` in Python;
construct a minimal child environment containing the key as `DOKPLOY_API_KEY`,
the validated `DOKPLOY_URL`, required runtime paths, and `NO_COLOR=1`. Run from
a private empty directory under the run directory so the CLI cannot pick up a
project `.env`. Do not call `dokploy auth`, put the token in arguments, write
CLI auth configuration, inherit a second Dokploy token variable, or print the
child environment. Never repurpose HOME to achieve this isolation.

Use argument arrays, stdin closed, no TTY, and `--json` on data commands.
Supply all required options; do not invoke interactive selection flows. CLI
stdout/stderr are captured privately and parsed before anything is logged.
Read responses can contain secret environment values; receipts use an explicit
allowlist of identifiers, lifecycle state, image/config hashes, and safe error
codes. Do not dump a complete `compose one` or project response into the agent
transcript. Redact server-provided diagnostics and bound captured output.

Run local help/version checks with a 10-second timeout, remote reads with a
30-second timeout, and a mutation submission with a 60-second timeout. Killing
a timed-out CLI does not cancel an operation accepted by Dokploy. Mark that
outcome ambiguous and reconcile it before retrying. Poll deployment status at
least five seconds apart, for at most 15 minutes; an expired wait leaves the
deployment status unknown, not canceled or automatically redeployed. Keep
one request in flight per host and honor the repository's request floor.

**Validate the selected release against this server.** Capture help for the
groups/actions below and run one list read plus parameterized reads for the
exact authorized environment/resource. Exercise create, readback, update,
deploy, status, logs, and stop against a disposable run-owned rehearsal
application before production provisioning. Verify JSON types, error/exit
behavior, target environment/server, booleans, and remote postconditions.
Command exit zero or a deployment enqueue response is not deployment success.

Inspection of upstream source at
`e354bbacd7fde6d1c8d590b5231037cda6d3d099` found a tRPC client and generated
presence-only boolean flags. This is inspected source, not proof that the
selected published package or sandile.dev behaves identically. In particular,
test parameterized GET encoding and explicit false values. `--flag false` must
never be assumed to send boolean false. Do not supply `--freshVolumes`; verify
that omission preserves volumes. Do not use a CLI delete command if it cannot
express `deleteVolumes=false`.
[CLI transport source](https://github.com/Dokploy/cli/blob/e354bbacd7fde6d1c8d590b5231037cda6d3d099/src/client.ts),
[generated command source](https://github.com/Dokploy/cli/blob/e354bbacd7fde6d1c8d590b5231037cda6d3d099/src/generated/commands.ts).

Use this operation map. Command names shown were checked against that source;
the pinned package's help and rehearsal decide whether each mapping passes:

| Operation                        | Default CLI command                                                                                                                                                 | Required verification                                                                                                                                         |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Discover projects/environment    | `project all --json`, then `project one --projectId … --json`; use the supported environment read command from its help.                                            | Match the supplied environment ID and server; never pick the first result or reuse a similarly named application.                                             |
| Create held Compose application  | `compose create --name … --environmentId … --composeType docker-compose --sourceType raw --appName … --description … --json` with explicit server ID when required. | Run-owned identity/description, correct environment, fresh resource, no jobs/deployment implicitly started.                                                   |
| Read/update Compose              | `compose one --composeId … --json`; `compose update --composeId … --composeFile … --json`.                                                                          | Exact rendered Compose content/hash, raw mode, auto-deploy false, intended images, networks, volumes, and holds.                                              |
| Deploy                           | `compose deploy --composeId … --title … --description … --json` with no fresh-volume flag.                                                                          | New deployment belongs to this operation and reaches success; running images, external volumes, and holds match.                                              |
| Follow deployment                | `deployment all-by-compose --composeId … --json`.                                                                                                                   | Track the recorded deployment ID or newly created record relative to the pre-submit snapshot; do not treat an old successful deployment as this one's result. |
| Discover containers              | `docker get-containers-by-app-label --appName … --type standalone --json`, with server ID when needed.                                                              | Match run/application/service labels; resolve IDs again after replacement.                                                                                    |
| Read service logs                | `compose read-logs --composeId … --containerId … --tail 500 --since 1h --json`.                                                                                     | Correct service, bounded output, successful parameterized read, then redaction.                                                                               |
| Stop an owned deployment         | `compose stop --composeId … --json` after confirming its help/contract.                                                                                             | Containers stopped, no child still writing, volumes intact.                                                                                                   |
| Inspect monitoring/notifications | Supported read actions from `server --help` and `notification --help`, selected in the capability receipt.                                                          | Record capabilities without changing recipients or sending messages.                                                                                          |

The `--composeFile` argument is the literal multiline file content, not a path,
unless the selected release explicitly documents a file input. Python reads
the rendered file and passes it as one argument; shell interpolation is not
used. Compose content must contain secret-file references only. For oversized
payloads or fields that contain secrets and would be exposed in argv, use the
narrow HTTP fallback with a structured body. Do not assume stdin, `@file`, or
a `--body-file` option exists merely because another CLI supports it.

**Fallback without a browser handoff.** Record each operation as `cli` or
`http_fallback` in `dokploy-command-contract.json`, with CLI/server versions,
verified flags, response schema, omission/default semantics, and test receipt.
If a pinned command cannot safely express a required request, implement only
that request using the installed server's verified REST/OpenAPI or tRPC
contract. Use the same authorization and validate the same postconditions.
Examples are an explicit `autoDeploy=false` update, volume-preserving deletion,
or a parameterized read rejected because of CLI/server encoding mismatch.
Do not guess alternate routes/envelopes or patch installed CLI files in place.
Test the fallback on rehearsal resources and retain its exact contract.

An unsupported CLI command alone is not a reason to ask the user to operate
the dashboard. Use the verified fallback automatically within authorized scope.
If neither interface supports the required safe operation, return the existing
code 3 with the exact gap, intact holds, and completed independent work. Never
broaden credentials or upgrade the platform to bypass that block.

**Retry by reading state.** Before a mutation, record the target, a UUID
operation ID, desired-state hash, current deployment IDs, and intended
postcondition. Put the run/operation identity in resource descriptions and
deployment titles where supported. A timeout or lost response is resolved by
reading matching resources/deployments, not repeating the command. Resume the
existing operation when found; conflicting matches return code 4. Reuse this
rule for both CLI and HTTP paths. Destruction additionally requires exact
run-owned IDs/labels and volume-preserving semantics.

**Remaining SSH work is explicit.** Keep SSH for source Nix/systemd holds and
final checkpoint capture; target capacity/filesystem checks; image transfer and
Docker load; labeled external volume creation/inspection; installing private
secret files; and controlled migration/restore container execution. Invoke
`docker exec` over SSH for read-only health if the CLI lacks a verified exec
interface. Do not assume API command coverage includes an interactive terminal.
No browser interaction is required for the planned workflow once the existing
credentials, exact environment ID, and host access are available.

The agent's final handoff includes the toolchain and command-contract receipts
plus ready-to-run commands referencing the retained run directory. It must not
depend on shell history, a global CLI login, or a previous agent's memory.

## 4. M0 — Freeze contracts and inventory

1. Record `jj status` and byte hashes for existing modifications. Read
   `AGENTS.md`, the design contract index, operating handoff, and this plan.
   Create status rows M0–M9 initially `not_started`.
2. Resolve stable Python 3.12, PostgreSQL 18, uv, and Psycopg build inputs once.
   Pin image manifest/platform digests, dependency resolution, and client major.
   Record image architecture. Native `linux/amd64` is required for production;
   ARM64 may run developer tests, with separate receipts. No floating tags in
   the rendered deployment. Do not reuse the research ARM64 digest by assumption.
   Resolve/install the controller's pinned CLI/Node/npm tooling and record the
   local help, package integrity, and command map under the CLI contract above.
   With access available, validate list and parameterized reads; this does not
   grant M0 permission to create or deploy applications.
3. Build empty legacy SQLite schemas 14 and 15 using isolated fixture helpers
   extracted from the current migration lifecycle, including dynamically
   installed triggers. Do not open a production path with that helper.
4. Generate a schema manifest listing every table, ordered column, declared
   type, observed affinity, default, nullability, primary/unique key, index,
   foreign key/action/deferral, trigger SQL, and sequence. Record SQL hashes.
   The research found 71/74 tables, 82/87 explicit indexes, and 290/299 triggers
   for schemas 14/15. Differences must be explained by source changes before
   updating fixtures; counts alone are not a parity test.
5. Generate a Python/SQL call-site ledger by AST and text inspection. Every
   persistence caller, script, test fixture, SQLite exception handler, PRAGMA,
   implicit transaction, `rowid`, `lastrowid`, JSON expression, date expression,
   and dynamically composed identifier gets an owner and replacement category.
   Use the ledger to close the port; do not rely on a blind global replacement.
6. With access available, inspect the actual source package, active/persistent
   Nix profiles, state path, all six unit definitions/states, hold files, and
   public head. Record exact source/runtime/schema identity. Inspect sandile.dev
   architecture, Docker/Compose/Dokploy versions, existing resource allocations,
   volumes, free memory, disk, and inodes. These are observations, not mutations.

**Schema rule:** implement PostgreSQL storage migration `0001` as semantic
schema 14 and `0002` as semantic schema 15. Ordinary connections accept either
supported profile and never migrate. The first target uses `0001` only. A
fresh schema-15 source may use `0002` only when its already-deployed identity
and existing H16 checkpoint prerequisite are verified; never advance a
schema-14 source as part of relocation. Other source versions return code 3.

Schema capabilities are a checked, immutable mapping loaded once at connect:
schema 14 has derivations/controls and lacks `origin_backfill`; schema 15 adds
that capability. Replace table-existence probes with capabilities. Origin
dispatch already checks for table presence; preserve its inactive behavior
on schema 14. Other origin-only commands must fail before mutation with
`UnsupportedSchemaCapability`. Run common runtime tests on both profiles;
run origin tests on 15 and explicit disabled-origin tests on 14. This is two
PostgreSQL schema profiles, not two production database implementations.

The current source has undeployed work. Replaying its entire runtime is not
evidence of equivalence to the deployed source. Freeze both identities; use the
deployed source as the import/storage oracle and the checkout's frozen legacy
implementation as the engine-port differential oracle. Keep those results
separate. Production input acceptance remains held.

**M0 exit:** manifests, source ownership, port ledger, immutable local source
reference, schema profiles, and resolved image/dependency identities exist.
Missing external access does not block M1–M7.

## 5. M1 — PostgreSQL schema and database interface

### 5.1 Connection and row contract

Retain `open_database(state_dir, ...)` and the useful `Database` call surface,
adding a required database configuration supplied explicitly or from secret
files. A missing DSN is an error; never silently create SQLite state.
Reject `.sqlite`/`.db` runtime paths with a message pointing to legacy import.
`state_dir` continues to locate artifacts. Tests inject their own configuration.

Expose `transaction(immediate=True)` as a bounded write and
`transaction(immediate=False)` as a Repeatable Read, Read Only snapshot;
offer `read_snapshot()` as the clearer equivalent for converted callers.
Nested writes use savepoints and inherit the outer deadline. A read snapshot
cannot upgrade to writing; nested reads inside a write use that write's view
without pretending it is an independent snapshot. Invalidate snapshot caches
on exit, any write, and rollback to a savepoint. Normal autocommit reads must
not reuse snapshot caches.

Use Psycopg autocommit connections outside these explicit transactions, and
dedicated physical connections for session locks. Psycopg otherwise starts
transactions for ordinary reads; nested transaction contexts use savepoints.
[Psycopg transaction documentation](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).

The row type supports `row[int]`, `row[str]`, `len(row)`, value iteration,
`keys()`, and `dict(row)` with SQLite-compatible behavior. Reject duplicate
column labels at the interface during tests and fix ambiguous SELECTs. Cursor
supports `fetchone`, `fetchmany`, iteration, `rowcount`, and explicit close.
Large scans use a named cursor within a read snapshot or bounded keyset reads.
No `fetchall` for an unbounded relation. Ordinary result iteration alone does
not establish a memory bound. The connection wrapper checks write context;
callers cannot invoke raw commit/rollback or obtain the underlying connection.

Map integrity SQLSTATEs to a stable `StateIntegrityError`; cancellation to
`WriteDeadlineExceeded` only after rollback is confirmed; lock acquisition
failure to existing command/control timeout errors; connection/commit ambiguity
to `StateConnectionLost`. Preserve trigger-specific invariant codes in errors.
Do not catch all database errors as “no result.” Never transparently reconnect
and resume a command that owned state or attempted an external effect.

### 5.2 Locks and deadlines

Reserve the two-int advisory lock namespace `0x5357494E` with keys `1` for
data-command ownership (A), `2` for control/admission turn (B), and `3` for
atomic writes (C). PostgreSQL scopes these locks to a database. Use the exact
namespace/keys in all worker, maintenance, and control paths. Session locks
span commits; transaction locks end with the transaction.
[PostgreSQL locking documentation](https://www.postgresql.org/docs/18/explicit-locking.html).

Acquire A with `pg_try_advisory_lock(namespace, 1)` on the command's primary
connection. Also keep an exclusive `flock` on the artifact volume's `state.lock`
for that command's lifetime. Order is artifact lock, A, B when needed, C.
Never acquire B while holding C. Acquire C with
`pg_advisory_xact_lock(namespace, 3)` as the first statement after BEGIN in
each application write transaction; subsequent statements get a current
Read Committed view. B is a session lock on the connection doing admission or
controls. Do not reenter/recount session locks accidentally; release once.

All data commands, backup, GC, and maintenance take artifact lock plus A.
Controls take B then C without A or artifact lock. New admissions take B then
C. Settling an admitted action takes C only. A waiting pause owns B while
waiting for an existing write; that writer can settle, and the next admission
cannot overtake the pause. Controls may commit while a backup snapshot is open.

Defaults: timer cycle waits zero for command ownership and logs a successful
overlap skip; ordinary commands wait at most 60 seconds; backup waits until
ownership or shutdown. Lock polling uses monotonic time, at most 100 ms sleeps,
and one shared caller deadline. Control servicing, including both locks and
commit, uses its remaining deadline. Read-only reports take neither A nor C;
filesystem report-cursor updates take a separate short report-file lock.

Preserve the 45-second whole-write limit. Start its monotonic deadline before
BEGIN, inherit it through savepoints, and include commit. Before SQL, set local
statement/lock timeouts to the remaining budget. Retain the main-thread Python
alarm for Python work; add a watchdog that cancels a blocked libpq operation at
the deadline. At 45 seconds cancel and roll back; if cancellation/rollback
cannot be confirmed within 5 more seconds, exit the process and classify the
outcome as uncertain. A supervisor keeps the artifact lock until the child is
dead. Do not configure a shorter server `transaction_timeout` that silently
releases ownership while Python continues. Test server-terminated sessions as
a distinct failure. Maintenance/import transactions have a separate explicit
deadline and never run under worker credentials.

Before every external source request or publication step, verify the command
connection is healthy and the action remains admitted. Connection loss stops
new actions; an already-issued remote request can have an uncertain outcome.
The volume lock prevents a replacement local worker until the old process is
dead. An advisory lock alone cannot fence an HTTP effect. Keep existing
publication reconciliation and durable uncertain admission records.

### 5.3 DDL, trigger, and role contract

Use UTF-8 with `LC_COLLATE=C` and `LC_CTYPE=C` for the database. Keep SQLite
integer contracts as `bigint`, floats as `double precision`, and serialized
JSON/timestamps/keys as text unless the explicit manifest-byte mapping below
applies. Preserve defaults and nullability. Add 0/1 checks only where the
existing contract requires them and source preflight proves compliance.
Review unique-NULL and nullable-primary-key behavior instead of letting a type
conversion silently strengthen or weaken the domain rule.

Add an `insertion_order bigint` identity to `derivation_generations`, imported
from its source rowid, and an `attempt_cursor bigint` identity to
`requirement_attempts`, also imported from rowid. Update every tie-break/report
query accordingly. Preserve all existing numeric IDs and sequences, including
`registry_verifications.verification_id`, `work_attempts.attempt_id`,
`control_events.event_id`, `requirement_transitions.transition_id`, and
`admission_decisions.decision_id`.

Create infrastructure tables in a separate `swingset_internal` schema:
`schema_migrations(version, sha256, applied_at)`, one-row
`state_identity(dataset_id, restore_generation, semantic_schema, import_run_id)`,
and import phase/table receipts. Normal application roles can read identity,
but cannot change it. Keep domain tables in `public`; revoke PUBLIC CREATE.
Migration checksums and semantic schema must agree at every ordinary open.

Maintain an explicit trigger mapping from every legacy trigger to a PostgreSQL
function/trigger and an invariant test. Consolidation is allowed; losing
coverage is not. Preserve derivation revisions, immutable generations and
identity journals, deleted-link retention, requirement transitions, queue
invalidation, and publication fencing. Determine deterministic ordering where
multiple triggers affect one mutation. Test inserts, updates, deletes, upserts,
multi-row statements, cascades, rejected writes, and rolled-back savepoints.
Canonical compound keys must match the existing SQLite JSON encoder for their
actual types. Implement explicit compact string/null array encoding with golden
Unicode, quote, slash, control-character, and empty-string cases; do not strip
whitespace from arbitrary serialized JSON. Fail the port ledger on an unmapped
numeric JSON key rather than applying a string encoder to it.

Create a NOLOGIN schema owner and separate login roles: migration, worker,
control, and reader/backup. Worker has domain DML/sequence usage but no ownership,
DDL, TRUNCATE, trigger-disable, or role-switch privilege. Control has SELECT
needed for selectors/status and DML only on `operator_pauses`, `control_events`,
`control_state`, plus required sequence usage. Reader/backup has SELECT only.
Migration credentials never enter the normal worker container. Prefer invoker
trigger functions; any needed definer function has a fixed safe search path,
minimal privileges, and no PUBLIC execute. Inventory grants as test data.

Advisory locks are cooperative. The supported application interface requires C
for all writes; grants do not make arbitrary worker SQL obey that lock. Test
the supported paths and restrict credential distribution. Preserve publication
guard triggers on every domain table except the existing five-table control/
admission exception in `publication_fence.py`; infrastructure tables are owned
separately. No ordinary startup installs or refreshes triggers.

**M1 exit:** fresh schemas 14 and 15 install; migration rerun is a checksum-
checked no-op; altered migration checksum is rejected; privilege, transaction,
row, cache, three-lock, timeout, and connection-loss tests pass on real PG.

## 6. M2 — Port the pipeline and prove semantics

Port by module group in this order, running that group's existing tests on
PostgreSQL after each change:

1. `state/` core, controls/admission, work/attempts, findings, requirements,
   identity journal, derivations, and report cursors.
2. `fetch/`, `admission/`, and `schedule/`: budgets, cache validators, issued
   requests, outcomes, clocks, host gates, fair turns, and crash recovery.
3. `history/`, `parse/`, `project/`, and `link/`: evidence and scope ownership,
   identifiers, policy gates, retained generations, and invalidation.
4. `build/`, `publish/`, then CLI, backup/GC entry points, and runtime reports.
   Check `doctor` connectivity through the database interface, not existence of
   `state.sqlite`. Remove equivalent filesystem assumptions everywhere.

For every SQL statement, bind values with `%s`, compose identifiers safely,
give conflict clauses explicit intended behavior, use `RETURNING` for inserted
IDs, and translate null-safe comparisons. Fix SQLite permissive GROUP BY,
scalar min/max, truthy integer predicates, aggregate return types, division,
date arithmetic, LIKE/case sensitivity, JSON extraction, and transaction-abort
behavior where the ledger finds them. Do not assume only placeholders change.
Use explicit ordering for deterministic results, including NULL placement and
tie-breakers. Preserve time precision/UTC interpretation. Use per-statement
savepoints only when the original caller intentionally recovered from an
integrity failure and continued its transaction.

Keep JSON strings exact. For `derivation_dependency_sets.manifest_json`, store
exact UTF-8 in a `bytea` column with `STORAGE EXTERNAL`; update all access through
`closure_manifest`. Hash in 65,536-byte slices before yielding any member, then
decode in a second bounded pass within the same read snapshot. The incremental
decoder can retain one member; reject an individual member over 8 MiB with a
named error, after proving all retained source members fit. Do not materialize
a complete registry manifest. Audit `derivation_readiness.py`, `derivations.py`,
and `build/closure.py` for alternate full-payload reads.

Retain a frozen legacy implementation for differential tests in an isolated
source snapshot made at M0, outside installed production packages. Run both
engines over identical deterministic fixture actions, fake clock values,
accepted policy, source responses, and generated IDs. Compare normalized
logical rows after each committed action and compare failure outcomes. Allow
only documented storage additions and genuine runtime identity differences;
do not mask timestamps, counters, findings, or public content wholesale.

Required scenarios include request budget reserve/issue/failure, retry-after,
conditional 304, pause/drain/resume, interrupted units, duplicate observations,
identity correction/deletion, trigger revision changes, same-timestamp release
selection, partial build, publication reservation, remote success with lost
acknowledgment, and recovery from pending candidate/baseline changes. External
effects use local fakes; no fixture test contacts a source or production Hub.

**M2 exit:** no runtime SQLite imports outside explicit legacy modules; every
call-site ledger row is closed; existing behavior tests pass with PostgreSQL;
schema-14 origin paths stay inactive; schema-15 origin tests still pass.
No test may be deleted or weakened merely because the engine changed.

## 7. M3 — Immutable import and independent audit

Expose new `swingset-migrate inventory`, `import-sqlite`, and `audit` commands
through `migration/cli.py`. Each accepts `--run-dir`; inventory also takes
`--checkpoint`. Target secrets come from files, never command arguments.
Import requires an unused target identity and creates `RESTORE_PENDING` before
any database write. The supervisor always treats an absent identity or this
marker as inactive. Import never calls normal input acceptance or recovery.

### Source validation

Verify the legacy checkpoint manifest and remote commit, database file hash,
artifact closure, relative paths, sizes, and baseline symlink. Reject path
traversal and symlinks escaping the checkpoint. Require a completed standalone
SQLite backup with no unaccounted WAL; then open `mode=ro&immutable=1`.
Run integrity/FK checks. Verify both schema markers and the exact installed
schema/trigger fingerprint against the selected source profile. Do not trust
`user_version` alone or run migrations to make a mismatch disappear.

Inventory every column's actual storage classes and every maximum field size.
Reject NULL primary keys unsupported by the mapped target, embedded NUL/invalid
UTF-8 text, unsupported storage classes, out-of-range integers, non-finite
floats, and invalid JSON in fields whose contract requires JSON. Record a
bounded diagnostic locally; do not silently coerce, trim, replace, or skip.
Source type exceptions require a separate explicit contract change and recheck.
These are data incompatibilities, not automatic invitations to edit production.

### Loading and resumption

Create the destination database with no worker CONNECT grant. Install tables
and primary/unique keys from the selected baseline, excluding domain triggers
until after load. Defer attachment/validation of cyclic foreign keys until all
tables exist and rows are loaded. Preserve their final deferral/action rules.
Never set a global trigger-bypass mode on an activated database.

For each table, use an explicit column mapping and bounded reads. Ordinary
rows are copied with Psycopg COPY adaptation. Read large manifest payloads
with SQLite `blobopen`, emit bytea as hex through streamed COPY text blocks,
and escape other text according to COPY rules (`\\`, tabs, CR, LF, NULL).
Do not build the whole hex field in Python; bound output blocks to 256 KiB.
Test a literal `\\N`, empty text, NULL, mixed Unicode, and binary bytes. Fields
over 1 MiB use a bounded path; other rows fit a 4 MiB batch cap. Reject any
single value too large for the selected PostgreSQL representation before load.

Commit one table with its input fingerprint and import receipt in the same
transaction. A killed COPY rolls back that table; a rerun verifies completed
tables and resumes the incomplete table from the beginning. Never append
blindly after a lost commit response. If receipts/data disagree, keep the
attempt inactive and create a new target, retaining the failed one for diagnosis.

For each existing identity sequence set the next value above both the maximum
surviving ID and retained `sqlite_sequence` high-water mark. For empty tables
with no high-water mark use `setval(sequence, 1, false)`; otherwise use the
greatest mark with `is_called=true`. Apply the same rule to new rowid-based
ordering sequences. Check signed 64-bit exhaustion. PostgreSQL sequence gaps
after rollback are allowed; committed ordering/identity values must remain
monotonic and never collide.

After loading: create and verify triggers, remaining indexes, and foreign
keys; validate all constraints; provision grants; run ANALYZE; verify catalogs
against the DDL manifest. A numeric trigger count is insufficient. Only the
maintenance process may connect during this stage.

Copy all required artifacts to the new volume using the existing closure
contract. Preserve report cursors, pending publication files, input holds,
and `operator-hold`. Exclude venvs, dependency caches, old lock files, and
transient checkpoints already represented by retained archive references.
Do not remove the original immutable checkpoint. Track every included or
excluded path and its rule; unknown state files fail the inventory gate.

### Audit encoding and identity

The independent auditor reads source and target; it does not trust importer
counts or run importer conversion functions to decide equivalence. Use the
shared documented _format_, with separate source and target readers:

- Sort rows by declared primary-key columns with text byte ordering and
  explicit NULL placement. Tables without a unique key use a preserved source
  ordering field or an externally sorted full-row encoding; list the choice in
  the table mapping. Stream the sort to disk when necessary.
- Encode each field as a one-byte type tag, unsigned 8-byte big-endian length,
  and payload. NULL has a distinct tag and zero length. Integers use signed
  decimal ASCII; doubles use big-endian IEEE-754 binary64; text uses exact
  UTF-8; bytes are exact. Normalize negative zero to positive zero only if
  the source's observed storage/read contract requires it, with a golden test.
- Prefix each row with its column count and frame each field. Hash the ordered
  row stream with SHA-256. Record count and digest per table. New ordering
  columns are compared separately to source rowids. Infrastructure rows are
  excluded from domain hashes and validated against their own manifest.
- The source manifest text and target bytea are compared as exact UTF-8 bytes
  under the same declared payload tag. A second pass verifies dependency IDs
  against their content hash. Bounded reads apply in the auditor too.

Require zero unexplained table/count/hash differences and zero invalid
constraints. Independently inspect active admissions, control revisions,
budgets, next-allowed times, source/parse cursors, identity journal positions,
selected generations, report cursors, baseline, pending intent, and historical
unfinished runs. Do not “repair” these before comparing them.

Generate a persistent UUID `dataset_id` for legacy state during import and a
fresh UUID `restore_generation` for each installation. Write both to the DB
identity row and `state-identity.json` in the artifact root. Record their
binding to the source checkpoint. Every ordinary DB open compares both values;
an absent/mismatched pair fails before mutation. Across future restores preserve
dataset identity and regenerate the installation generation under hold.

**M3 exit:** fixture schemas 14/15 import exactly; large fields stay bounded;
interruptions resume safely; deliberately mismatched DB/volume, corrupt bytes,
invalid rows, stale receipts, and missing files prevent activation.

## 8. M4 — Complete PostgreSQL checkpoints and restores

Introduce manifest format `swingset-checkpoint-v2` with `engine: postgresql`,
semantic schema, storage migration checksums, dataset identity, source restore
generation, runtime/input identities, dump file/path/hash/size, tool versions,
artifact entries, baseline/pending intent, and creation time. Retain legacy
format readers. Do not include role passwords or connection strings. Unknown
manifest formats fail closed. Keep HF repository defaults as today; make
repository injection available to tests and checkpoint rehearsal.

Checkpoint algorithm:

1. Take artifact lock and A. Start a dedicated reader/backup connection in
   Repeatable Read, Read Only; export its snapshot. Enumerate database-referenced
   artifacts in that same transaction. Capture marker files under the relevant
   short filesystem locks; controls may continue in the database.
2. Run PostgreSQL-18 `pg_dump --format=custom --compress=gzip:6 --no-acl`
   with the exported snapshot into a temporary checkpoint directory. Keep its
   exporting transaction open until dump completion. Use a private libpq
   service/password file; never log secrets in arguments or subprocess errors.
3. Copy/hash the immutable artifact closure while A protects it against GC and
   baseline mutation. Include holds and report state. Validate the manifest,
   fsync, and atomically finalize. Close the snapshot promptly after dump and
   database closure enumeration complete; retain A through the existing upload
   lifecycle. The existing HF transport packs an uncompressed tar; compression
   belongs to the dump. Preserve its 20 GiB archive limit and require the
   measured packed checkpoint to stay below 18 GiB at rehearsal and cutover.
   Exceeding that threshold fails capacity acceptance; do not lift the bound or
   omit artifacts. Supporting larger checkpoints requires a separately tested
   versioned transport extension before cutover can proceed.
4. Upload to the private archive and verify the acknowledged commit and manifest
   hash through the existing transport. Only then record remote success. Retry
   the same completed local checkpoint after an ambiguous upload, reconciling
   its manifest first; never declare success from a local file alone.

Custom-format ownership is handled at restore, not by assuming dump-time
`--no-owner` removed it. Exported snapshots give dump/read consistency, but
sequence allocation is not snapshot-isolated: a concurrently advanced sequence
may restore above the snapshot's largest row ID. That is an allowed gap, never
a reason to lower it. Validate restored sequences against all retained IDs.
[PostgreSQL dump options](https://www.postgresql.org/docs/18/app-pgdump.html).

Restore algorithm:

1. Verify the remote/local manifest and every byte before installation. Provision
   a fresh database and artifact volume. Put an external activation hold in the
   supervisor configuration and `RESTORE_PENDING` in the volume.
2. Restore the dump with `pg_restore --exit-on-error --single-transaction
--no-owner --no-acl --role=swingset_owner` under maintenance credentials into
   that empty database. Provision `swingset_owner` as the NOLOGIN owner before
   restore, permit only the maintenance role to SET ROLE to it, and apply the
   documented grants afterward. Ownership/ACLs must come from this provisioning.
   Restore failures leave the target inactive and restart in a fresh database.
3. Install artifact files, run catalog and logical checks, and bind the new
   restore generation on both resources. Keep a phase journal outside the
   replaced resources so a crash between them is detected.
4. Use existing publication reconciliation against the actual public head.
   This phase may recognize an already-completed publication and repair local
   pointers under maintenance ownership; it must never issue a new Hub write.
   An unexpected remote head blocks installation rather than overwriting it.
5. Clear `RESTORE_PENDING` atomically only after the database/file identity and
   public reconciliation pass. Clear the external restore hold only after that
   postcondition is verified. Preserve the separate `operator-hold` throughout.

Port local checkpoint artifact recovery, checkpoint enumeration, and GC to
format dispatch. GC takes A, never deletes referenced generations/artifacts,
never removes the last verified complete checkpoint, and cannot delete an
in-progress checkpoint closure. A backup while controls change must restore
the snapshot-consistent earlier controls. Summary cursor writes must be locked
while captured; restored cursors must not skip a committed report interval.

**M4 exit:** new-format round trip, legacy import/recovery, interrupted restore
at each step, changed controls during snapshot, pending publication, missing
artifact, corrupt dump, failed remote upload, connection loss, and GC exclusion
all pass. Record measured dump/archive sizes; no deduplication claim is assumed.

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

## 10. M6 — Test integration and measurable acceptance

`tools/test_postgres.py` starts a uniquely labeled PostgreSQL container with
test-only credentials and no production mounts, provisions per-test databases
and role sets, runs its child command, and always cleans up only its resources.
Use a random loopback port for host tests. CI may use an isolated Docker
network instead. Parallel tests get separate databases because advisory lock
keys are database-scoped. Concurrency/crash tests must use actual commits and
independent processes; do not wrap the entire test in a rollback transaction.

Keep parser/model fixtures offline. Convert existing persistence fixtures and
subprocess helpers to PostgreSQL configuration; no silent SQLite fallback.
Retain SQLite only for legacy migration and differential oracle tests. Add
typed test helpers rather than duplicating setup SQL in every test. Run tests
with source networking blocked/mocked and no HF token. Assert zero unexpected
HTTP requests in the integration suite.

These commands must work after M6; the test launcher sets the temporary DB
configuration and destroys it after its child exits:

```sh
mise run fmt
nix develop --command uv sync --frozen
nix develop --command uv run ruff check .
nix develop --command uv run mypy
nix develop --command uv run python tools/test_postgres.py -- uv run pytest -q
docker build --platform linux/amd64 -t swingset-migration:local .
```

CI retains Ruff/mypy/full-suite checks and adds final-image tests on Linux
AMD64. Add libpq/compiler/PostgreSQL client dependencies to Nix where the C
driver needs them. Format only through `mise run fmt`. Verify unrelated
pre-existing edits remain byte-identical except for specifically intended
additive index changes. Do not add snapshot tests that merely mirror DDL;
tests must demonstrate invariants or realistic failure recovery.

Required acceptance groups and their pass conditions:

| ID      | Evidence                                                                                                                                                                                                           |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| DB1     | Commit, rollback, nested failure recovery, RC writes, coherent RR reads, and row access parity pass on both semantic profiles.                                                                                     |
| DB2     | Separate processes prove A across commits, B/C pause ordering, timer skip, control timeout without mutation, and lock release after process death.                                                                 |
| DB3     | Sleeping SQL, lock wait, CPU-bound Python, failed cancellation, and lost commit acknowledgment produce the specified bounded/uncertain result without a second external effect.                                    |
| DB4     | Every trigger mapping has an invariant test; control cannot mutate evidence; worker cannot alter schema or disable guards.                                                                                         |
| PORT    | Differential fixture action sequences match logical state and expected failures; all existing behavior tests pass.                                                                                                 |
| IMPORT  | Exact per-table and artifact audit, rowid/sequence preservation, no source mutation, inactive interrupted targets, and no accepted new input.                                                                      |
| BACKUP  | Complete round trip including concurrent controls, pending publication, legacy artifact recovery, and GC protection.                                                                                               |
| RUNTIME | Persistent scheduling, all holds, duplicate worker exclusion, secret-free provenance/logs, SIGTERM, and database/container restarts pass.                                                                          |
| OBS     | Structured logs, cheap probes, hold-aware status/conditions, redaction, restart persistence, and documented Dokploy capability/notification coverage.                                                              |
| ADMIN   | Pinned CLI identity; noninteractive invocation; authenticated list and parameterized reads; verified booleans; safe JSON/error handling; reconciled timeouts; CLI/HTTP fallback parity; and restart from receipts. |
| SCALE   | Full-checkpoint import/audit/restore/build and manifest bounds meet section 11; record actual timings and peaks.                                                                                                   |
| HOST    | Actual Dokploy-generated mounts, networks, limits, image identity, held supervisor, and recreated-volume persistence match the specification.                                                                      |

**M6 exit:** local groups through RUNTIME and local OBS tests pass with retained
receipts, including ADMIN tests against a local fake API and the actual pinned
CLI binary. Capture requests to prove `freshVolumes` is never true, false-valued
updates use a working route, and cleanup cannot delete volumes. Test timeout
after remote acceptance, old successful deployment records, malformed/error
JSON, nonzero exit, missing credentials, stale IDs, and secret-bearing responses.
Tests must not contact sandile.dev or send notifications. SCALE, HOST, actual
ADMIN compatibility, and actual Dokploy visibility require the authorized
checkpoint/target; mark them pending if absent.

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
