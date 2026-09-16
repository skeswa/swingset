# Migration contracts and run inputs

Part of the [PostgreSQL migration plan](README.md). These are target
requirements, not evidence that migration has happened. Keep the numbered
steps in order and preserve the recorded operating holds.

## Original scope and execution boundary

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
existing [operating handoff](../../../journal/investigations/2026/2026-09-15-release-handoff.md) and
[v2 release gates](../history-and-recovery.md). A held migration can complete
without resolving those gates. Test active scheduling on isolated state.

The agent can implement and test everything locally without production access.
An unattended production transfer additionally requires usable credentials,
access to the source writer, and authorization to execute the migration. These
cannot be inferred from a public login page. Section 3 defines the required
inputs and a noninteractive failure result when they are unavailable.

## 1. Decisions and invariants

This is the executable successor to the
[PostgreSQL research](../../../journal/investigations/2026/postgres-migration-plan-2026-09-14.md) and
[hosting review](../../../journal/investigations/2026/dokploy-migration-2026-09-14.md). Their experiments
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
`architecture.md`, `docs/guides/operation.md`, and `journal/archive/v1-implementation-evidence.md`.
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
Publish only redacted evidence under `journal/evidence/hosting/dokploy-migration/`.

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
