# Stage 2 deployment assets

These assets prepare the isolated server rehearsal. They do not switch
production. The acceptance steps and required receipts live in
[stage 2](../docs/plans/postgres-migration/7-stage-2.md).

## Prepare the configuration

Copy the three example JSON files into a private, owned operation directory.
Replace every placeholder. `network.json` identifies the exact runtime image,
private bridge, service addresses and deployment label. `rehearsal.json` lists
the new databases' immutable UUIDs, the separate rehearsal dataset and explicit
branch, the scoped
backup endpoint and bucket, and owned mock origins. Its empty example UUID
list deliberately refuses real I/O.

Set both image variables in the host deployment environment; the empty values
in `runtime.example.env` deliberately refuse startup. `SWINGSET_IMAGE` is the
daemon's runnable image reference or CONFIG ID. `SWINGSET_RUNTIME_IMAGE` is the
verified qualified `repository@sha256:OCI_MANIFEST_DIGEST` for that same archive.
Workers record this artifact identity in backups. The namespace policy's
`image_id` pins the actual daemon `container.Image` CONFIG identity. Keep the
archive's manifest-to-CONFIG mapping with the operation receipt; Docker image
IDs can differ between containerd and overlay2 stores. Never derive an OCI
identity by attaching a repository name to a CONFIG digest. Both ordinary and
utility Compose services require the explicit artifact variable, and their
entrypoints reject missing, tag-only or malformed artifact identities before
reading credentials or opening a database.

Generate gateway files with:

```sh
python -m swingset.deploy.configuration gateways.json generated-gateways
```

The proxy permits exact host and port pairs. It refuses literal IP destinations
and does not log URLs or query strings. Add only the exact Hub redirect,
bucket, alert and dead-man origins the rehearsal needs. The resolver serves
only its listed internal names and never forwards unknown questions. Neither
configuration permits collection websites.

The pinned CoreDNS executable requires `NET_BIND_SERVICE`. Only the resolver
receives that capability; it keeps its nonroot user, read-only root, no new
privileges and default Docker security profiles. Application workers receive
no added capabilities ([D-0280](../journal/decisions/0280-allow-only-the-resolvers-bind-capability.md)).

Create the named external volumes explicitly with the deployment's owned
label. Prepare their empty directories for UID 10001, including spill `tmp/`
and `cache/`; the Postgres image initializes its own data volume. Preserve all
existing volumes on recreation. Never run `compose down -v` or prune globally.
The `compose.yaml` file gives each application service its own memory limit,
read-only root, dropped capabilities, no new privileges and a private network.

The shared Postgres relay publishes only `127.0.0.1:15435` by default. It has no
credential. Every SQL client still authenticates with SCRAM. Confirm denial
from a non-loopback address as well as a successful SSH tunnel, then repeat
both checks after recreation.

## Keep the administrator outside the application

Build `Dockerfile.operator` from the repository root. Its pinned tools install
and inspect the firewall from a separate, trusted platform operator. This
container has the Docker socket and host PID namespace; application services
have neither. Give it only the capabilities its namespace operations need:
`SYS_ADMIN`, `NET_ADMIN` and `SYS_PTRACE`. Keep its filesystem read-only and
network disabled. Mount the exact reviewed `security.py` and `network.json`
read-only, and only the owned authorization directory writable. Do not mount
the whole host filesystem or use `--privileged`.

The authorization directory must be owned by UID 0 with mode 0755. Its policy
and script contain no credentials and are readable mode 0644. Workers receive
the directory read-only. Do not make it writable by UID 10001 or other users.
The operator runs:

```sh
python /operator/security.py /operator/network.json /authorization --watch
```

It checks the exact container image, labels, private address, attachments and
hardening. It installs IPv4 and IPv6 OUTPUT chains in that container's network
namespace. Only its database relay, proxy and designated DNS service are
reachable; the policy covers every interface. It writes authorization only
after verifying the rules. Authorization names the full container id, boot,
process start, namespace inode and policy hash. A restarted or recreated worker
must receive a fresh authorization before opening Postgres. Application code
cannot edit the kernel policy. The operator also exports peer container state
and memory for the watchdog; workers never receive the Docker socket.

Initial shared-database table creation uses
`postgres_bootstrap.prepare(admin_connection)` in a trusted host job. It executes
only the exact frozen SQLite-37 table phase, assigns the application owner and
records the still-empty catalog fingerprint. The ordinary migration or importer
adopts only that exact prepared phase. Cluster administration credentials are
not copied into application or migration services. The backup checker receives
administration rights only on its separate private scratch Postgres server.

The maintained `swingset platform-bootstrap` command accepts `--mode roles`
against `postgres`, then `--mode tables` against the newly onboarded Swingset
database. Pass a private UID-matched mode-0600 file with `--admin-dsn-file`;
the DSN must explicitly select that database at `127.0.0.1:5432`. Run it in a
separate trusted job sharing only the exact Postgres container's network
namespace, with its own PID namespace and resource limits. It checks the
actual administrator and target before changing roles or preparing tables.
Stage those files on the trusted server with
`python3 -m swingset.deploy.bootstrap_credentials`. It requires the exact
Postgres container and image IDs and a fresh directory under a private owned
parent. Credentials remain on the server; its result contains only file and
container identities. Keep partial files on failure and review their disposal
separately.

Large checkpoint validation uses `swingset unpack-job --policy PATH` on the
server. The frozen-code reference uses `swingset reference-build --policy
PATH --review PATH` on the Mac. Both require reviewed identity and resource
bounds; their authoritative final result, rather than progress output,
establishes completion. Keep the reference filesystem and full-suite scratch
allocations serial.

Before import, prove that UID 10001 can open and hash the exact read-only
source mount. A checkpoint unpacked by another user can pass every content
check and still be unreadable to the utility. Prepare a separate source copy
with that ownership; preserve the original checkpoint and its permissions.

Supervise one already-created, reviewed import with:

```sh
swingset import-job --plan import-plan.json --plan-sha256 SHA256 \
  --container FULL_CONTAINER_ID --platform FULL_NAMESPACE_OPERATOR_ID
```

The plan binds the pinned utility, its create arguments, private operation
directory and seven existing applications. It keeps the 200 GB disk floor.
The deadline is exactly 7,200 or 21,600 seconds; the longer full-size budget
requires a separate review after the measured 6,877-second source validation.
A failure retains the partial database, files and
hold. Never repeat a reserved operation automatically. Exit zero still needs
the complete native import/audit receipt and stored identity postchecks.
The importer validates the SQLite source before opening its destination
session, so validation cannot expire an idle relay connection. Its separate
`import-preflight.json` reports phase and table, never load completion or a
cached authorization to skip checks.

### Inspect a loaded target

Inspect a finalized target with all source tables loaded before planning a resume:

```sh
swingset inspect-import /native-import/import.json \
  --receipt-sha256 RECEIPT_SHA256 --source-sha256 SQLITE_SHA256 \
  --database swingset_dryrun --database-oid EXPECTED_OID --environment dryrun
```

It uses the explicit owner credential in `SWINGSET_OWNER_DATABASE_URL` with
read-only transactions. It checks the bound receipt, database name and OID,
C/UTF8 locale, stored UUID, finalized schema, every row count and absence of
other clients. Add `--database-id UUID` when the UUID is already known. The
command changes no application data or receipt and does not resume an import.
Its success does not replace the independent typed audit. The guarded operator
utility permits this command; no third-party credentials are needed.

## Credentials, holds and scheduling

Bootstrap the private history mirror in a separate trusted Linux host job:

```sh
swingset mirror-bootstrap --policy mirror.json \
  --credentials-file /private/credentials.env --result /private/result/result.json
```

The policy format is `swingset-rehearsal-mirror-v1`. It names `source`
(`skeswa/swingset`), a fresh `target` under `skeswa/swingset-rehearsal`, the
exact `source_head`, checkpoint `baseline`, `source_commits` count and
`maximum_source_bytes` (at most 10 GiB). Bind these to a read-only preflight.
Policy and credential files must be private mode 0600 and owned by the job's
user; the result's parent must be mode 0700. An existing result is refused.

The command reads only `HF_REHEARSAL_TOKEN` from the server template. Its
owner-scoped bootstrap credential stays outside application services. The
sole mutation creates a fresh private duplicate; it never resets or deletes
a repository. It verifies full main history, manual branches/tags, the
checkpoint baseline and the unchanged source head. Then replace or restrict
the token to the new repository before distributing application credentials.
Baseline-file downloads remain a separate proof.

Require both a successful process exit and a complete receipt. A missing or
failed receipt leaves the mutation outcome unknown; inspect the destination
read-only and never automatically repeat the duplicate request.

Supply credential files with mode 0400 owned by UID 10001. The Compose file
mounts only the required files in each service. Ordinary workers receive no
owner or scratch-administrator credential. Only release gets the
repository-scoped Hugging Face token; backup jobs get bucket-scoped S3 keys.
Alert and dead-man endpoint files are private. The policy and kernel rules are
required even when credentials are scoped correctly.

Run the graph re-key through the `operator` utility after reviewing its verify
receipt. The command opens the ordinary worker connection; the private
`verifier` utility cannot re-key the shared import.

Verification scratch uses `TMPDIR`, with `/tmp` as the default. In the
read-only container, create its configured spill directory for UID 10001
before launch. Verification removes its temporary directories on exit.

Place an owned import hold in the external hold directory before starting
services. It names its owner, reason and review time. Every scheduler checks
this directory before a database connection; removing one operation's hold
never removes another. A skipped slot is consumed. Cursors survive recreation,
run a missed interval once and never repeat after a backward clock jump.
Ticks run in separate process groups with bounded SIGTERM and kill grace.
The background release task uses `--dry-run`; only the explicit, reviewed
operator publication pushes B. This keeps the rehearsal head fixed while the
long recovery drill runs.

The default backup calendar is Monday and Thursday at 04:00 UTC; summary is
08:00 UTC and maintain is 03:30 UTC. Acquire and derive run every five minutes,
plan every thirty minutes, and release hourly. The health service runs
independently of long backups. Its watchdog preserves the existing failure,
pause, hold-age and stalled-progress signals. Check real page requests after
resuming; completed empty ticks do not establish progress.

Operator writes on stored `dryrun` or `production` require `--prod`. This flag
never changes the stored environment or permits a development database to make
real requests. The CLI prints only the safe destination name and UUID to
stderr; it never prints a DSN or credential.

Add `compose.utilities.yaml` only for reviewed one-shot work. Its `operator`
profile receives the shared migration, control and worker logins and scoped
publication credentials, never the private cluster administrator. Its
`verifier` profile receives only private scratch-server logins and administrator,
with no shared owner, Hugging Face token or R2 keys. Both wait for the same
incarnation-bound firewall authorization before reading credentials. Pass a
supported CLI command to `compose run operator` or `compose run verifier`.
Do not override their entrypoints. Mark these two roles `required:false` in
the network policy so their absence does not make regular inventory incomplete;
failed or OOM exits still alert.

Select a fresh owned utility-state volume for each import or restore target.
Before full graph verification, copy the shared source read-only to the private
Postgres server, retaining its exact 141 source fields and recording the fresh
dev copy UUID. The verifier accepts only that dev identity and private endpoint;
its entrypoint explicitly changes its process mode to dev after authorization.
The final shared recovery database keeps its required dryrun identity. Initialize
`runtime/verifier/copies` as UID 10001, mode 0700. This durable copy-owner ledger
survives container replacement; inspect older namespace owners before cleanup.

## Prove the boundaries

Use the owned mock server (`python -m swingset.deploy.mock`) for normal replies,
redirects, conditional 304s, Retry-After and timeouts. Run the following inside
every actual worker service and repeat after recreation:

```sh
python -m swingset.deploy.probe \
  --allowed http://owned-mock.swingset.invalid:8080/ok \
  --denied-name forbidden.swingset.invalid \
  --denied-address OWNED_IPV4 --denied-address OWNED_IPV6 \
  --proxy http://PROXY_PRIVATE_IP:3128
```

The negative endpoints must first be shown live from an allowed control path.
A dead endpoint or failed connection alone does not prove enforcement. Retain
raw packet and request observations in scratch and bind the compact operation
receipt to them. Also inspect both kernel rule sets, test forbidden redirects,
verify the relay tunnel and non-loopback denial, and watch other apps' health.
Do not contact a collection site during the rehearsal.

On the Mac, `mise run tunnel` opens the loopback SSH forward. `mise run psql --
--database NAME --user ROLE --keychain-service ITEM` uses that one keychain
credential and starts `psql` with transactions read-only by default. Install the
`psql` client before using it; no database password belongs in a dev dotenv file.
