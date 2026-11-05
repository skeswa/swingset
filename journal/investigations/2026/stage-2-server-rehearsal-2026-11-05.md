# Stage 2: server rehearsal

Date: 2026-11-05 UTC  
Type: Work in progress  
Related plan: [stage 2](../../../docs/plans/postgres-migration/7-stage-2.md)

## State

Stages 0 and 1 are recorded in `5f5e52447963`. Stage 2 has its own revision.
Gate 2 remains open. Production remains on SQLite; no rehearsal database,
publication or cutover has happened yet. All 45 immutable transport files are
verified on the server; no new SQLite copy was made on the Mac or source VM.

## Prerequisites

The initial audit found missing persistent full-dump exclusions, dedicated
role onboarding, a stored `dryrun` identity, rehearsal dataset routing, Compose
services, enforced egress and the database tunnel. These are being implemented.
Rhuidean's configured R2 account initially differed from both Wrangler OAuth
accounts. The owner authorized a fresh login; configured-account access now
works. The reviewed operation created only `swingset-backups` and `swingset-dev`
(enam, Standard). Both are private with no custom domains; only dev has a
30-day object expiry. Existing Rhuidean bucket policies remained unchanged and
no objects or keys were written
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/r2-provision-current.json)).

OAuth cannot mint bucket keys: the account-token endpoint returned 403.
Scoped S3 keys, a rehearsal-only Hugging Face token and owner alert/dead-man
endpoints remain pending. A mode-0600 server-only credentials template is at
`/home/customer/.config/swingset-stage2/credentials.env`; it must never be
committed, copied into the state volume or stored on the Mac.

## Source and transport

The initial source inspection found recovered acquire, plan and release runs
after the 19:18 stall alert. A later acquire failed with `database locked` at
19:55, before transfer began. The next run recovered at 19:55:40 with four
actual page requests and 13,510,991 downloaded bytes.

The read-only watcher fired at 20:57 after an hour without an actual fetch.
Scheduling inspection found zero eligible pages: the registry's daily budget
was 1,500/1,500, and other work was exhausted, awaiting parents or cooling
down. The next inspected registry work was due at 00:11 UTC on November 6.
This explains the idle cycles; it does not establish an outage. No production
budget, state or code was changed. The [receipt](../../evidence/runtime/postgres-stage2-2026-11-05/source-watch.json)
binds the two observations and distinguishes inspected scheduling slices from
global eligibility.

The 22:11 follow-up again found zero eligible pages. Registry priority-5 work
was due, but its host had spent the daily budget; the named 00:11 future slice
was not the globally earliest due work. At 22:37 the loops still had no new
failure, with no actual fetch since 19:55. The existing archive-host pause and
two-hour fetch-idle alerts were observed, with no production changes
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/source-watch-current.json)).

The 04:00 checkpoint has baseline `84939a5d3e53fd12da200adf9be8e310b22beb02`;
the live worker has since advanced to `790a604a32e935072079c7ff62f0fcef29f3dfd6`.
The rehearsal must use the checkpoint's baseline explicitly. Its immutable
transport consists of 45 files and 16,082,378,008 stored bytes. Direct transfer
to the server and unpacking there avoids another full SQLite copy on the Mac.
Changes since the checkpoint baseline still need checking on the server copy.

The initial stream ended early in the compressed index; the sender exited 0
and receiver 2. A direct SSH retry also stopped with a short tail before any
replacement. Standard SFTP delivered the exact index. Reviewed atomic activation
preserved both rejected files outside the transport, and all 45 SHA-256 checks
then passed. Fourteen health snapshots found the seven original services running
with configured checks passing; all owned transfer processes exited
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/transfer-verified.json)).
The corrected [initial failure receipt](../../evidence/runtime/postgres-stage2-2026-11-05/transfer-first-current.json)
separates the supervisor's exit 1 from the receiver's exit 2.

## Schema versions

Native migration 38 introduces `dryrun` without editing native migration
37 or the exact imported SQLite version row. The import contract remains 37;
checked native metadata carries the newer version
([D-0276](../../decisions/0276-version-native-schema-without-changing-imported-history.md)).

## Pending proof

Provision the isolated services after their prerequisites pass. Import and
audit the whole checkpoint; verify and re-key the untouched import; publish
only to the rehearsal dataset; rebuild from the actual prepublication backup;
compare releases and history, then run the lifecycle and network failure tests.
Timing, peak resources and other apps' health must be recorded. No Gate 2
result is claimed by this initial audit.

## Recovery and shared privileges

Real separate-login tests exposed the initial administrator-only text function
and the lost search path when an owner adopted an administrator's table phase.
The checked bootstrap now verifies the empty catalog across plain and native
readers, explicitly restores the schema path, and lets the limited owner finish
native version 38. Worker/control identity writes and cross-database connections
were refused in actual private PostgreSQL probes. This is local evidence; no
shared-server database has been provisioned yet.

Recovery now has database-bound bootstrap and verified-source cursors. Bounded
turns preserve the restore marker and completed work. The targeted source and
unpack groups passed 56 tests; activation and checkpoint checks passed 38. Final
whole-suite validation of the full deployment candidate remains pending.

The first full core run executed all 2,724 cases: 2,716 passed and eight
failed. Native version fixtures and recipe-scope attribution need updating.
Its 221.79-second wall time exceeded the unchanged 180-second budget; Ruff
and mypy's 354 source files passed
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/developer-first.json)).
Review also reproduced a scheduler returning while a helper kept writing.
The correction must terminate and reap the owned group, including adopted
children under Linux PID 1, before another task can start.

After the functional corrections, all 2,729 core cases passed, but the command
took 200.48 seconds and therefore failed the unchanged 180-second gate. Ruff
and mypy passed. A one-second image export overlapped that run, so it is not
claimed as a quiet benchmark
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/developer-wal.json)).
The full suite was interrupted after 540 passes to remove unnecessary WAL
grant authority; the corrected bootstrap passed 47 native cases. A complete
full-suite result remains pending. The interrupted scratch tree was removed
after an independent ownership and open-file check; reports remain in scratch
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/interrupted-full-cleanup.json)).

## Rehearsal branch and credentials

The configured private branch must start at checkpoint A without resetting a
newer live head. Hugging Face's [SDK guide](https://huggingface.co/docs/huggingface_hub/guides/repository)
says duplication preserves history, while its [Hub guide](https://huggingface.co/docs/hub/repositories-next-steps)
says dataset history is squashed. The proof therefore checks actual complete
ancestry, A's manifest and baseline-file downloads before using a duplicate.
No existing unrelated repository is silently adopted. No Hugging Face token
has been placed on the Mac and no rehearsal repository has been written.

The first compact R2 receipt omitted Wrangler commands because its producer
selected HTTP verbs only. The current generated receipt includes all three
mutation commands and retains the first file unchanged.

## Developer timing

The later quiet run passed all 2,745 cases in 202.26 seconds, still above the
180-second limit. PostgreSQL's completed catalog vacuum reduced the measured
dependency lookup from 15.78 to 0.69 ms, but did not resolve the core regression
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/developer-operation.json)).

A bounded filesystem probe compared identical source bytes and native runtime
fingerprints in the same pinned image. Moving the read-only source from the
Mac bind to a Linux volume reduced steady runtime-identity capture from
21.94 to 11.35 ms, and full capture from 26.68 to 15.09 ms. All 20 fingerprints
matched. These per-call savings do not yet establish the whole-suite saving
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/runtime-filesystem.json)).
The harness change must preserve exact inputs, test selection and resource
limits, and include staging in the existing wall-time budget. The small native
fingerprint cache also preserves the old hash and reads current bytes on every
call; neither change has passed the final core or full-suite gate yet.

The complete Linux-source experiment then took 200.66 seconds and failed an
existing migration fixture's write check because copied permissions became
read-only. That overlay implementation was reverted. Under the existing test
selection rule, the five-file nested read-set replay moved intact to extended,
and core gained one real armed project/link smoke case. The next whole core
selected 2,843 cases in 184.02 seconds. Its one failure was a forbidden dynamic
import in the new reference operator; that import and the lint failures were
fixed without changing the static import check. The 180-second gate and the
complete full suite remain open.

The next complete core passed all 2,928 selected cases in 194.37 seconds;
lint and types passed. Both fixture failures were corrected: a foreign-owner
metadata mock now affects only its own file, and crash recovery waits for the
exact dead PostgreSQL backend within the original deadline before taking the
ordinary nonblocking lock. The timing gate remains open
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/developer-late.json)).
The native full suite was interrupted after 959 passes because retained passed
temporary files had already used about 13 GiB. Its source binding stayed
unchanged. After independent ownership, process, ledger and open-file checks,
only its exact temporary directory was removed. The restart uses core's
existing `tmp_path_retention_policy=failed`; cases, assertions and resource
limits are unchanged. A complete result remains pending
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/full-retention-cleanup.json)).

## Frozen reference build

The read-only runtime preflight reproduced all 525 frozen input files with
source `b6cc9c28` and bundle `e80f675d`. The first bounded reference operation
then stopped at mount-option readback, before quota validation, restore or
build. Its private image retained 4,345,856 allocated bytes. The namespace and
all cgroup processes exited; no loop device remained, no OOM occurred, and
the shared source mount was unchanged
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/reference-first.json)).

The guard expected `discard=sync`. Linux's
[Btrfs implementation](https://raw.githubusercontent.com/torvalds/linux/master/fs/btrfs/super.c)
prints synchronous discard as `discard`; the failed guard did not retain its
actual option string. The correction must capture that string, accept the
equivalent spelling and continue refusing asynchronous or disabled discard.
Reusable orchestration moves into a maintained command with tests before
another attempt; the failed image and receipts remain intact.

The first maintained attempt accepted the actual `discard` spelling but
stopped when enabling Btrfs quotas failed, before the full restore. Its image
and failed receipt remain intact
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/reference-quota.json)).
The reviewed replacement bounds the loop device itself at 16,178,532,352
bytes and checks its actual capacity, physical allocation and both free-space
guards. It passed the compression and frozen-input preflights, restored and
verified the checkpoint in 405 seconds, and entered the normal frozen-code
build after its identity checks. It passed at 23:55 UTC in 1,051.38 seconds,
producing the fresh changed candidate `cand_459f6af282274dc4` against baseline
A. Its 53 export files total 381,573,994 bytes. Authoritative SFTP output and
all source pins match; the owned cgroup is empty, the namespace owner exited
and no owned loop remains. B/C comparisons are still pending
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/reference-built.json)).
All candidate/control files and provenance receipts then reached a fresh
private server directory through standard SFTP: 59 files, 381,686,344 bytes,
with complete source/server hashes and inventory checks
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/reference-export.json)).
After an independent exact-identity review and another complete server hash
check, only the owned reference image was removed. Its loop and mount are
gone, shared production mounts are unchanged, and delayed disk reclamation
was measured before starting full tests
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/reference-cleanup.json)).

## Early server operations

The early image archive and all 19 receiver checks passed. Docker 28 loaded
the expected AMD64 CONFIG identities, and the pinned gateway checks passed.
All 15 original Docker objects and seven running services remained unchanged
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/image-cache-early.json)).
This proves the early image's transport and provenance, not the final source.

The first offline unpack failed before the application started: Docker's
`local` logging driver refuses compression with `max-file=1`. The owned
container stayed created with PID 0, exit 128 and no OOM; checkpoint inputs
were untouched. The corrected bounded logging configuration belongs in a
maintained job command before a fresh attempt
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/unpack-first.json)).

The early network setup authorized all six worker namespaces under the
reviewed capabilities and default AppArmor/seccomp. The resolver then exited,
so baseline testing did not start. The setup command must also require every
support service running before it reports success; network denial and relay
proofs remain pending
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/network-setup-first.json)).
Read-only inspection of the stopped resolver proved its executable carries
only the effective `NET_BIND_SERVICE` file capability. The resolver-only
exception is recorded in
[D-0280](../../decisions/0280-allow-only-the-resolvers-bind-capability.md).

The resolver repair and six-role baseline passed. Both owned target families
were live from the control container, but the workers' baseline rejection
counters stayed zero: this establishes the route boundary, not an exercised
TCP firewall rule
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/network-baseline-early.json)).
The adversarial attachment then failed on Docker's unsolicited IPv6 neighbor
advertisement. Its 12 rejected ICMPv6 packets are not TCP proof. The reviewed
repair replaces only the owned target bridge and four fixture services with
Docker's advertisement count set to zero; worker IDs and complete firewall
rules must remain unchanged. Each later TCP probe must show the correct route
and source, the configured administrative rejection and an immediate counter
increase, with live controls before and after.

The maintained offline unpack started at 23:06 UTC under its fixed two-hour,
2 GiB limit. At 23:54 it was still validating the full SQLite and file closure,
with no OOM or original-service health change. A 30-second sample measured
27.7 CPU seconds and 1.4 seconds of I/O stall, so no memory increase was made.
Its final result follows below.

The unpack finished after 3,587.22 seconds with exit 1 and no OOM: 153 legacy
code scopes lacked verified retained bundles. Frozen04 contains only current
bundle `e80f675d` and its 525 members. Production still retains historical v1
scoped archives, so the next diagnostic must collect exact required pairs and
verify those separate witnesses. No guard was waived and no import ran. The
library automatically removed its own failed temporary copy; the new opt-in
`--keep-failed` leaves future failed workspaces fenced for diagnosis. A retry
must use an image that actually implements that option. The corrected receipt
removes a producer's placeholder review digest while preserving the first
receipt's bytes
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/unpack-code-closure-current.json)).

The complete early network rehearsal passed for all six roles. Each routed
IPv4 TCP probe returned administrative rejection with a positive rule-counter
delta; IPv6 did likewise with a ready source address. Owned endpoints were
live from control before and after. Old authorization was refused after
recreation and restart, and each new incarnation received fresh authorization.
The loopback relay and SSH forward worked before and after relay replacement;
both declared and actual mappings named only `127.0.0.1:15435`.
Docker's internal-only fixture bridges initially prevented actual port
publication. A separate owned ingress bridge attached only the credential-free
relay corrected the fixture; production Compose already uses the external
Dokploy bridge. After independent review, all 12 proof containers and three
bridges were removed. Original 15 Docker objects and seven applications stayed
unchanged. These are early-image proofs, not final-source acceptance
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/network-lifecycle-early.json)).

The dedicated trusted platform job installed four capability roles and exited
without OOM. Only its private server-side DSN file carried the administrator
credential; no application or migration container received it
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/platform-roles.json)).

After the UTC day reset, actual registry acquisition resumed. The 00:19 run
fetched 54 pages. The new alert repeated web.archive.org's known ServerError
pause; no hold or budget was changed
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/source-watch-resumed.json)).

## Full test and import prerequisites, November 6

The full run with passed-test scratch removal completed in 1,437.83 seconds:
4,506 passed, two failed and one teardown errored. Its source binding stayed
unchanged. Both failures came from fixture inputs left outside the owning
pytest tree; basename reuse then collided. The teardown error came from a
process-wide scandir mock receiving a file descriptor during cleanup. Inputs
now stay under the test's ignored tmp directory, and the mock restores before
cleanup. All five affected cases passed. Separately, a recovery review found
that marker-unlink failure could repeat an already completed rebuild. The
completion ledger now clears that pending work and honors old complete
ledgers without replay. All 47 activation, pressure and rebuild checks passed.
A clean complete full run is still required
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/full-current.json)).

The latest core passes all 2,945 selected cases, lint and types, but takes
202.56 seconds. The fixed 180-second gate remains open. A measurement found
empty-table bootstrap probes account for only 0.7% of a real backup roundtrip;
that experiment was removed without changing the bootstrap queries
([core receipt](../../evidence/runtime/postgres-stage2-2026-11-05/developer-current.json)).

The trusted platform jobs created the Swingset project and the C-locale
swingset_dryrun database, with three scoped logins and 142 empty tables.
The environment table has no row yet: no UUID, migration 38 or import was
adopted. Actual role probes passed, backup rediscovery left no enabled
Swingset dump, and the seven existing services stayed healthy. Rhuidean's
inventory and dedicated R2 backup exception are recorded there
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/dryrun-provision.json)).

A database-only offline diagnostic restored and SHA-checked the exact frozen
38,890,938,368 bytes. The unchanged streaming reader examined every retained
recipe in four tables and collected all 192 required scope/digest pairs.
It finished in 1,827.79 seconds under the same 2 GiB combined memory/swap
limit without OOM. The fenced raw database and stopped container remain on
the server. This proves the references, not the checkpoint's full archive
closure; no import or activation ran
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/code-references.json)).

A bounded metadata search found descriptor matches for all 192 pairs in
13 retained production bundles: the current bundle covers 39 and twelve
historical bundles cover the remaining 153. Full source inventory and SHA
checks passed for 6,232 files totaling 256,270,223 bytes. Full native scope
verification on the server subsequently passed for every required pair. The new optional supplemental
input path copies only complete immutable bundles, refuses undeclared files
and links, preserves existing checkpoint bytes, rechecks copied identities,
and then runs the unchanged strict closure check against the original rows.
Frozen manifest and index counts remain separate from supplement provenance.
All 64 legacy-unpack and CLI cases pass. The new runtime image passed its
installed-module and receiver checks before preparing the actual retry.

After independent review, the two completed administrator bootstrap containers
and their two temporary DSN staging names were removed. Exact inode guards
proved both names absent and the guarded files unlinked. No credential bytes
were read or hashed; this is not a global open-file or secure-erasure claim.
The application credentials, database, project, 142-table catalog and all
other services remained unchanged
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/temporary-admin-cleanup.json)).

The measured restore fixture reduction keeps all eleven tests and every
assertion: one real immutable backup source replaces repeated source creation,
while every actual restore still creates a fresh destination. Focused wall time
fell from 50.68 to 22.74 seconds. The analogous artifact-recovery file passes
all 18 cases in 6.26 seconds, with private archive bytes and fresh qualification
caches. No live source database or DSN is reused. Transport core passes 16
cases in 21.53 seconds, including a real native standalone restore per backend;
its five original multi-day integrations remain unchanged in full. Two extra
link-fuzz seeds move to full while the representative sequence and all targeted
identity/dependency checks remain core. These focused timings do not establish
a whole-core saving; the next complete run must measure that
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/fixture-setup.json)).

## Verified witnesses and final runtime

The standard SFTP relay copied all thirteen selected archives in 990.01 seconds.
The receiver proved the exact 6,232-file inventory and every SHA, totaling
256,270,223 bytes; all transfer process groups were quiet afterward
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/code-archive-transfer.json)).
The independently reviewed archive-only sandbox then used the actual installed
legacy resolver. It verified all 192 scope/digest pairs across all thirteen
complete bundles in 9.75 seconds, resolving the 153 historical gaps. No database
was mounted or opened, and no captured Python ran. The quarantine is separate
from the unchanged frozen checkpoint; the next strict unpack must still prove
the combined closure
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/code-archives.json)).

The final AMD64 runtime built in 7.32 seconds with all 557 runtime inputs
unchanged. Its portable CONFIG identity is `758d93a5`; its OCI artifact identity
is `b593476e`. Both new unpack flags and all six installed reader/operator modules
match source. The server checked all nineteen transferred assets and its actual
Docker28 image identity; existing objects and seven apps stayed unchanged.
No application was deployed
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/image-cache-final.json)).

The next core passes all 2,978 cases but takes 214.56 seconds. The three reduced
fixture files now use 31.15, 32.64 and 7.97 aggregate phase seconds. Other cases
became slower, and old scheduling weights left one client thirty seconds early.
A post-run host sample found BTLEServer and fseventsd using nearly two CPU cores;
that sample does not prove the cause of the preceding slowdown. No unrelated
process or test limit was changed. The fixed 180-second gate remains open, and a
new complete full run started against the final frozen runtime
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/developer-fixtures.json)).

## Restart recovery and completed checks

The agent daemon restart closed two reporting pipes. The first full test run
had completed 4,540 named cases without a test failure when pytest's reporter
raised `BrokenPipeError`; it is an interrupted run, not a passing full gate.
The corrected receipt excludes two unnamed incomplete XML elements
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/full-daemon-interruption-current.json)).
The strict unpack supervisor also saw a broken pipe and stopped its owned
container with exit 143 and no OOM. Its fenced partial workspace is retained;
nothing was imported or adopted
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/unpack-daemon-interruption.json)).

Both jobs restarted with private durable log files. The new full run uses
the bookmark's `mise run test` entry point and passes all 4,550 cases in
1,328.73 seconds. All 929 source and test inputs match before and after, and
the owner process is gone. The separate 180-second core timing gate remains
failed at 214.56 seconds
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/full-durable.json)).
The unpack retry changes only its nonce and output directory, keeping the
reviewed image, policy limits, read-only inputs and supplemental witnesses.
Its supervisor and logs survive an SSH or agent disconnect. It remains under
the two-hour limit, 2 GiB memory cap and 200 GB free-disk guard; completion
must come from its authoritative result, not the existence of a raw SQLite file.

The final runtime's isolated network proof completed every phase: baseline,
routed adversarial IPv4/IPv6 TCP, worker recreation, restart, relay recreation,
and local tunnels before and after. Six actual worker namespaces each hit
their dedicated reject counters while owned controls remained reachable.
DNS, proxy and redirect allow/deny checks pass. The recreated relay binds
only `127.0.0.1:15435`; its nonce passes through SSH and access through the
non-loopback address is denied. This proves an HTTP relay to owned endpoints;
SQL authentication and the final credentialed application topology remain open
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/network-final.json)).
After a second agent's written ACCEPT verdict, the maintained cleanup command
removed exactly twelve stopped proof containers and three empty bridges.
Twenty-three other containers, seven other networks, all images and volumes,
the seven existing apps and the running unpack were preserved. Two outer
read-only formatting errors preceded the sole cleanup invocation
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/network-final-cleanup.json)).

Rhuidean's maintained server-only credential delivery command passes all 36
tests. It sends the management API key through SSH stdin, reads project
credentials only on the server, validates the exact project/database/roles and
private destination, and writes fresh private files without echoing values.
The code review accepted that path; it has not run because its actual
application endpoint is not selected. At 02:49 UTC the separate owner-managed
0600 template still has seven empty fields. Bucket keys, the rehearsal-only
Hub token and alert/dead-man endpoints remain prerequisites; no secret was
downloaded or hashed.

The production observer was reconnected with durable reporting after checking
the old observer's process identity. The 02:35 host-failure alert repeats the
same expired Wayback `ServerError` pause. The 02:48 read-only check found no
active pause, hold, admission or loop error. Actual fetching last occurred at
02:07 UTC: 38 registry pages. Registry has reached 1,500 requests and Wayback
ten for the UTC day, and the current plan has no eligible page rows. Thus
recent successful cycles issue zero requests. No production setting was changed
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/source-watch-0248.json)).

Collection then fetched one eepro.com page at 02:51 UTC without intervention.
The next observer pulse has no new failure, hold or active admission
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/source-watch-0254.json)).

A focused cProfile run of publication safety passes all eighteen cases.
Fourteen real publication backups and thirteen actual restore checks dominate
its 75.20-second profiled wall time. The complete Linux core contains 2,989.72
aggregate worker-seconds; even perfect balancing across sixteen workers would
need 186.86 seconds before collection and cleanup. Refreshed weights improve
the projected phase span from about 196 to 187 seconds, so no blind whole-core
repeat followed. The profile does not justify skipping safety checks, caching
audits or changing caps. Native profiled timings are not a causal comparison
with the Linux run
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/developer-profile.json)).

The supported `mise run check` was then measured once with the executor's
refreshed file weights. It passes all 2,978 selected cases in 171.62 seconds,
Ruff, and mypy across 362 source files. All source and test inputs are still
the full run's exact 929-input binding. Each client stayed below 1.56 GB
sampled working memory, with zero swap or OOM kills, and all cleanup checks
passed. Aggregate phase time fell to 2,480.71 seconds as well as becoming
more balanced; this run does not isolate the cause of that reduction. The
unchanged 180-second developer gate is now passed
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/developer-passed.json)).

## Strict unpack passed; credentials pending

The authoritative strict unpack result passes in 3,675.37 seconds, exit zero,
without OOM. The exact frozen 38,890,938,368-byte SQLite file matches its SHA;
all 195,869 indexed files and 41 segments match the unchanged checkpoint.
Full SQLite integrity, foreign keys and the original database-bound closure
pass. That closure verifies 173,592 required files, 81,832 objects and all
192 legacy code scopes across thirteen complete bundles, with no missing
finding declarations. The twelve historical additions account for 5,707 files;
their pins match the earlier independently checked archive proof exactly.
Both authoritative JSON results were transferred by SFTP and their hashes
matched before and after. The successful stopped container, validated directory
and earlier fenced failed attempts are retained. The seven existing apps
remain running, with about 755 GB free on the server
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/unpack-passed.json)).

The validated state is at
`rhuidean:/var/tmp/swingset-stage2-20261105-3bd7b122c532/unpack-0f85536fd5c7/work/sqlite37`.
No Postgres import or environment UUID has been created. The latest presence-only
check of `/home/customer/.config/swingset-stage2/credentials.env` still finds
all seven fields empty, with mode 0600. No credential value was emitted or
hashed. Local checks, frozen reference R and the isolated network proof pass;
Gate 2 remains open for the credentialed topology, import, whole-graph verify,
re-key, publication B, recovery C, comparisons and operational alerts.

The final production pulse at 03:24 UTC has no new failure, hold or active
admission; its last actual fetch was two scoring.dance pages at 02:56 UTC.
The temporary read-only observer and its SSH transport ended after checking
their exact process identities. The task-owned caffeinate also ended; production
timers and services were unchanged. The unpack supervisor has exited and its
successful container is stopped
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/source-watch-final.json)).

## Credentials and private history, 2026-11-06

The owner populated all five storage and Hub credential fields in the private
server template. Values stayed on the server and were neither emitted nor
hashed. The owner declined Slack and Healthchecks.io; both notification fields
stay blank while their delivery method is unresolved. Those proofs remain open.

The token was newly created for this rehearsal. Its original user-wide write
grant was confined to trusted bootstrap jobs. After creating the repository,
the owner restricted it. A read-only API check verifies one active resource
grant, with read/write access to the exact private repository ID. An empty user
entry has no permissions and adds no grant
([scope](../../evidence/runtime/postgres-stage2-2026-11-05/credential-scope.json)).

The owner deleted the original empty rehearsal repository. One container never
started because of a Docker logging-option conflict; its replacement refused
the now-missing empty-repository precondition before mutation. No duplicate
request had been sent. Because another attempt required reusing the helper,
the bootstrap became the maintained `swingset mirror-bootstrap` command, with
23 tests. Independent review caught a receipt-write failure path that could
have reported no mutation after a possible request; it now reports an unknown
outcome and forbids automatic retry.

The sole duplicate POST then returned HTTP 200 and created a private snapshot
on default main. Its one-commit main history failed the command's strict guard;
the failed command was not repeated. Read-only checks found the original commit
objects still accessible. A separately reviewed operation created `mirror-live`
at the unchanged source head and `rehearsal` at checkpoint A. Full ancestry
matches all 77 source commits and A's original history. Both default heads are
unchanged. Baseline-file downloads still need proof
([duplicate](../../evidence/runtime/postgres-stage2-2026-11-05/mirror-duplicate.json),
[anchors](../../evidence/runtime/postgres-stage2-2026-11-05/mirror-anchors.json)).

Both R2 keys pass their own conditional 36-byte put, exact body/metadata/ETag
read-back, ownership recheck, exact-key deletion and final absence check. Each
receives HTTP 403 for HeadBucket on the other Swingset bucket and both Rhuidean
buckets. This proves those specific denials, not every possible object action.
The independently reviewed probe exits zero in 40.26 seconds, leaves both
objects absent and preserves all seven existing apps
([storage](../../evidence/runtime/postgres-stage2-2026-11-05/r2-capabilities.json)).

The new command's full suite passes 4,573 cases in 1,760.54 seconds on an
unchanged source binding. Core passes all 3,001 cases, Ruff and mypy across
363 source files, but takes 285.25 seconds. An earlier mirror-helper core also
passed functionally in 261.53 seconds. Thus the 180-second timing gate is open
again. Post-run host swap pressure does not establish its cause. No assertion,
deadline or memory cap was weakened
([full](../../evidence/runtime/postgres-stage2-2026-11-05/full-mirror.json),
[core](../../evidence/runtime/postgres-stage2-2026-11-05/developer-mirror.json)).

The refreshed AMD64 runtime builds in 8.09 seconds. The bounded local builder
returns to its original stopped state, all source inputs remain unchanged,
and the server verifies the archive, CONFIG identity, source label and seven
installed modules. Existing containers are preserved
([image](../../evidence/runtime/postgres-stage2-2026-11-05/image-mirror.json)).

The maintained Rhuidean credential tool then successfully delivers three scoped
database URLs directly from Dokploy to fresh mode-0600 server staging files.
Their selected endpoint is the planned private relay, `172.30.240.3:5432`.
This is credential delivery, not connectivity or SQL authentication. Ordinary
applications have not received them; no cluster administrator credential was
involved
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/database-credentials.json)).

The first fresh baseline download check fetched ten manifest files, then failed
on `data/callback_marks/year=2026.parquet`: the private repository resolves its
original LFS pointer but its CDN returns HTTP 403. A read-only diagnosis finds
the same 1,044,272-byte content hash in both repositories at A, and the frozen
checkpoint file matches it. The live repository serves those bytes with HTTP
200; its current private snapshot uses a different 2026 object. The signed
URL has not expired. Thus branch ancestry does not establish historical object
availability. Inventory and repair of historical content must precede another
complete download proof; no failed duplicate request was repeated.

The read-only inventory then enumerated all 77 frozen source trees: 967 unique
large-file objects, totaling 8,784,215,213 bytes. After independent review, ten
single-attempt server-side content-copy batches associated those exact objects
with the private target. All 77 target trees match. A fresh cache downloads
all 39 baseline files through the maintained application adapter, and every
hash matches the frozen manifest. The job exits zero in 366.43 seconds without
OOM; native Xet transfers were disabled to keep HTTP requests serial and paced.
No commit or ref changed. The live head, which production had independently
advanced to `7a57fa0`, stays unchanged through this operation, as do the seven
existing apps. Non-baseline historical bodies were not individually downloaded
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/mirror-content.json)).

Dokploy now manages Compose service `XeKMlhSKH9FokoDJ8B4eL`, app name
`swingset-stage2-obeodd`, in Swingset's existing project. It runs four support
services only: database relay, proxy, resolver and owned mock. Their actual
networks and immutable images match the prepared configuration. Only relay and
proxy join Dokploy's overlay; resolver and mock remain on the private bridge.
The proxy needed an explicit private network alias for the owned mock's full
name. One initial same-definition update changed nothing; the corrected
definition was then deployed and its allow/deny checks pass.

Three scoped logins authenticate through `172.30.240.3:5432` in read-only
transactions. None has cluster administration privileges; only migrate can
assume `swingset_owner`. The public host refuses port 15435, whose Docker
binding is exactly `127.0.0.1:15435`. An ephemeral SSH tunnel receives the
database's SCRAM-SHA-256 challenge and is reaped; no password is sent to the Mac.
That tunnel test does not authenticate a login. The seven original apps are
unchanged. Pipeline workers, their credentials and the database import remain
unstarted
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/gateway-sql.json)).

Preparing the remaining commands found the guarded operator allowlist omitted
`rekey`. It now admits that command, which opens the ordinary worker connection;
the verifier still refuses it before authorization or credential loading.
All 35 utility and CLI cases pass in 13.46 seconds. The server still runs the
earlier proved image; this local entrypoint change needs the next image and
final full-suite validation.

## Held import and readable source

The independently reviewed filesystem job passes in 534.24 seconds, exit zero
without OOM. It copies and checks every one of 201,576 files, totaling
5,871,217,514 bytes, preserves the baseline symlink and assigns the state to
UID 10001. Three scoped database files are mode 0400 for that user. No Hub,
storage or cluster administrator key is supplied. The new external hold names
its owner, reason and review time
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/import-filesystem.json)).

The first guarded utility receives its fresh kernel authorization, then exits
one in eight seconds. Its UID cannot traverse the original source directory,
which belongs to UID 1000 with mode 0700. The failure occurs at the source stat
inside preflight, before database initialization or TRUNCATE. The monitor
retains the failed container, result and hold and does not retry it. A fresh
read-only call to the maintained prepared-adoption guard confirms the exact
catalog, all 142 empty tables and absence of a native import receipt
([refusal](../../evidence/runtime/postgres-stage2-2026-11-05/import-permission-refusal.json)).

GNU `cp` and `chown`, each in a bounded network-disabled job, prepare a separate
source volume. Only that new volume's ownership changes. In the actual runtime,
UID 10001 with no capabilities reads and hashes the entire 38,890,938,368-byte
SQLite file, then reads schema 37 and baseline A's sealed manifest. Its inode
differs from the original source's. All checks pass; the hash probe takes
273.02 seconds. The original checkpoint and its permissions are retained
([source proof](../../evidence/runtime/postgres-stage2-2026-11-05/import-readable-source.json)).

Before another invocation, the import monitor becomes the maintained
`swingset import-job` command. All 52 focused utility, CLI and supervisor cases
pass in 11.80 seconds; Ruff and mypy pass for the new supervisor and utility.
Its tests cover one start, identity/command/network/mount refusal, peer and disk
failure, deadline, lost start acknowledgment, native failure, OOM, a reserved
operation, receipt-write failure and a visible sixty-minute progress stall.
Exit zero still requires a separate native audit and stored-identity check.

An independent review approves one fresh attempt, with exact source-volume and
mount checks, the unchanged prepared database, existing verified state volumes
and no reset. It starts at 21:25 UTC in the previously proved runtime; the
new host supervisor contains no credential reader. Namespace authorization
arrives before credentials or a database connection. The first observation
shows source validation running, all seven original apps unchanged and about
702 GB free. The monitor keeps the 7,200-second deadline, 200 GB floor and
out-of-band hold. Owner alert and outside liveness delivery remain unresolved;
this approval covers attended import only, with no pipeline schedulers,
third-party credentials, external I/O or Gate 2 acceptance.

The current core check passes all 3,018 cases in 171.71 seconds, Ruff and
mypy across 364 source files. The complete source binding stayed unchanged
during the run. The three-minute gate now passes; the earlier slower
measurements remain retained. A final full run must include the supervisor
and utility changes ([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/developer-import-job.json)).
The fresh import binds all eleven exact mount sources and the independent
review, with no external credential mounted
([preflight](../../evidence/runtime/postgres-stage2-2026-11-05/import-second-preflight.json)).

## Source validation outlived the idle destination session

On resuming 2026-11-09, the second import had exited one at 2026-11-06
23:19 UTC. Source preflight returned after about 115 minutes, then BEGIN on
the destination found its connection closed. The relay times out idle sockets
after 4,200 seconds; this explains the idle window, without a retained relay
timeout event. A new read-only prepared-adoption check finds all 142 tables
empty, the exact catalog unchanged and no native receipt. All seven existing
apps still match their original identities and state
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/import-idle-refusal.json)).

The CLI now validates once before entering the owner connection context. It
keeps that one session through COPY and independent audit; it never reconnects
or adopts cached source checks. A regression test closes any session opened
during validation and exercises a complete native 141-table import and audit.
Preflight progress separately names phase and table and cannot authorize a
load. The existing hold retains its creation time and owner; its review is
recorded at 18:37 UTC, next due 19:37
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/hold-review-20261109.json)).

A direction-only independent review supports a fixed 21,600-second ceiling
for a fresh attempt: validation used 6,877 seconds, leaving only 323 under
the old cap for COPY, constraints and final audit. This does not approve
execution or predict its duration. The final exact image, policy, empty
target and mounts still need review. Resource, disk, relay, transaction and
core-test limits are unchanged. Notification delivery remains unresolved.

## Private verifier Postgres is ready

On 2026-11-09, the existing Dokploy Compose deployment gained a fifth support
service at private address 172.30.240.6. It publishes no host port and uses
the shared server's exact Postgres 17.11 image, C/UTF8 and 8 GiB/six CPU bounds.
All 22 captured shared settings match except fsync/full_page_writes, deliberately
disabled for scratch. Error statements and parameter values have explicit
log protections. Four capability roles and separate scoped worker/control
logins are installed on this new cluster. Both logins authenticate and lack
owner and cross-role privileges. No application database is created yet.

All five secrets were generated on the server. Three application files are
mode 0400 for UID 10001; the private-cluster bootstrap files remain mode 0600
for the host operator. No value was emitted, hashed or copied to the Mac.
The existing four service definitions and their container identities remain
unchanged, as do all seven original apps
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/private-postgres.json)).

The full-suite capacity check required at least 110 GiB on the Mac. An independent
review approved deletion of exactly 81 unused historical test scratch volumes.
Each identity and zero-reference check passed immediately before its removal;
all 29 containers,20 other volumes and36 images were preserved. Reclaim appeared
after a short delay;119.95 GB was available before the full run. No global prune
or cache deletion was performed
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/local-scratch-cleanup.json)).

The current core passes 3,027 cases in 206.96 seconds, so its unchanged 180-second
gate remains open. Aggregate measured case work is 3,022.47 seconds, an ideal
sixteen-worker lower bound of 188.9 seconds; rebalancing alone cannot recover
that run's budget. This does not identify the source of the higher costs.
The final full suite is running against an unchanged source binding.

The November 9 full suite passes all 4,599 cases in 1,343.64 seconds. Its
complete input binding stayed unchanged. The source is
`source-sha256:dd3f5a0d0b3fc93bae5ac1850fd95215075757ceb0726f5b8c66b87cc6e1e578`.
The separate core timing gate remains open; this supports the next attended
held import review, not Gate 2 acceptance
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/full-lazy-import.json)).

The controlled core check passes correctness, Ruff and mypy, but takes
192.23 seconds, so the unchanged 180-second gate remains open. Existing
selected cases account for the broad slowdown; the added cases contributed
only 2.43 aggregate seconds in the preceding failed timing run. No further
automatic timing retry is planned
([check](../../evidence/runtime/postgres-stage2-2026-11-05/developer-controlled.json)).

The third independently reviewed import started November 9 at 19:24 UTC,
using the source above, the new image and a fixed 21,600-second ceiling.
The replacement namespace operator and all eleven native mount bindings
pass their checks. Full source validation runs before the destination
session is opened. The existing seven apps and five support services are
unchanged; the schedulers remain unstarted. Import audit and stored identity
are still pending
([preflight](../../evidence/runtime/postgres-stage2-2026-11-05/import-third-preflight.json)).

The private verifier's controller lookup exposed a refusal before any wrong
write: an ambient base controller overrode the clone's settings. D-0281 makes
the selected state directory win for both worker and controller URLs, retaining
environment fallback and the endpoint/UUID guard. The native regression pauses
only a freeze/clone copy and checks that the source remains unpaused. All 67
focused cases and 4,600 full-suite cases pass; source binding stayed unchanged.
The 3,028-case core passes correctness, Ruff and mypy, but takes 203.50 seconds
([validation](../../evidence/runtime/postgres-stage2-2026-11-05/developer-copy-controller.json)).

A single twelve-worker experiment took 209.32 seconds, and fixed four-manifest
validation also refused that layout. Its aggregate case work fell, but the
wall budget still failed. All probe code and test edits were restored exactly
to the full-run source `3e815dbc7663`; the ordinary sixteen-worker core and its
180-second gate remain unchanged. No further automatic timing retry is planned
([probe](../../evidence/runtime/postgres-stage2-2026-11-05/core-worker-probe.json)).

## Rehearsal hold reviews

The active hold keeps its original November 6 creation, owner
`codex-stage2-resume` and import/validation reason. Reviews stage a public
0444 file, check both hashes, then atomically replace the hold through
network-disabled, capability-free jobs. The hold is never cleared. Detailed
command records remain in scratch; this ledger records their live control-file
hashes while the rehearsal continues.

| Reviewed UTC     | Next review UTC  | Live hold SHA-256                                                |
| ---------------- | ---------------- | ---------------------------------------------------------------- |
| 2026-11-09 19:30 | 2026-11-09 20:30 | 0b424f6d55520d4577d5bed92355082c133ff5549ac191acb2ac92609445dd6d |
| 2026-11-09 20:26 | 2026-11-09 21:26 | 8e1da70dbca3b06f26bd956f936b011b1080260f3afde4c56958c8e473d17a03 |
| 2026-11-09 21:24 | 2026-11-09 22:24 | 910ad04561df9a77dc3ab608b897f1c5d4b5d60e6d62967a0f20604a3be22182 |
| 2026-11-09 22:15 | 2026-11-09 23:15 | 5e1feca840ea42c4c1738d7e52ef055587d7a105e0b314ef3fcb55cf3a34737b |
| 2026-11-09 23:15 | 2026-11-10 00:15 | 0f6aae5db0f9580bd1a0a55636b5505eb2fe0af4842e2bb6bca7b49a7c6baf69 |
| 2026-11-10 00:01 | 2026-11-10 01:01 | d65f460ae2a07424cce2ac8acca2174be130343ca2038c90ada84b3864d97767 |
| 2026-11-10 00:54 | 2026-11-10 01:54 | 304be743d89e438ea6f7e2f6624ef21cc24c28770621516352519d87c42d4cc6 |
| 2026-11-10 01:51 | 2026-11-10 02:51 | cc75e10a06777796b14206f3c423db3b373c59bd0b7b4770d68811754acad27c |
| 2026-11-10 02:36 | 2026-11-10 03:36 | 7a55a41a1f3986ec072b022cb1ae22af58b9f878e79faa4d99d52a7d23390519 |
| 2026-11-10 03:26 | 2026-11-10 04:26 | fe7107255e639257448cb160d2c04d3f1c768d1e6344eb4d3194105ab9aeac6a |

## Writable verifier scratch and current import

The verifier now creates its temporary directory under explicit `TMPDIR`, with
`/tmp` as the default. This uses its existing private writable spill mount.
The native graph regression checks that location, complete replay, scratch
cleanup and unchanged source. All 47 focused cases and 4,600 full-suite cases
pass. Full time is 1,318.44 seconds with no failures, errors or skips. Core
correctness, Ruff and mypy pass; core time is 207.85 seconds, leaving its
180-second gate open. No timing retry is planned.

The unchanged full-run input binding is
`79e5eb35fd90326a6e1fad6a551a5f8ee2b30ad2cd0add9a8c9024f34b97d70e`;
the JUnit SHA-256 is
`b3852d152ba0cec05981a36a0ffed4ec42372cbe2df6609a96c15e1744ce6d1d`.
Detailed validation stays in
`/tmp/swingset-stage2-verifier-temp-full-20261109/validation.json`.

The runtime is cached on the server, without switching the active import:
OCI `sha256:8d1793a9dca4f66ad76c0e51ecbbb0f837c4bf77c475261be5191775baf51b4b`,
daemon config
`sha256:04a5fca1b351bb548a8119e5909a4f777132ec8e9e73b82a34346b32d9d4b64a`.
A created, unstarted container supplied the installed package for comparison.
All 545 package files, including 364 Python modules, match tested source
`source-sha256:1f0fefcc90d05693b917a07a9ef678fc38fb507ad36f37a0795ccaba760321f1`.
The scratch proof is
`/tmp/swingset-stage2-image-verifier-ready-20261109/installed-proof.json`.

At 21:24 UTC the attended import had finished validation of all 141 source
tables and loaded 11 tables, 713,738 rows. It remains incomplete; native audit
and stored identity are not yet accepted. The seven original apps remain
unchanged. No private copy or graph run has started.

## Source collection pulse at 21:18 UTC

The saved production acquire report `run_20261109T211005Z` finishes at
21:10:24 UTC with one page checked and 29,738 bytes from `eepro.com`.
It reports no failure or error. The inspected acquire, derive and plan reports
through 21:16 UTC also report no failure or stage error. This proves a recent
real fetch; it does not prove continuous collection. Only saved reports were
read; the agent sent no collection request or production mutation.

Scratch reports: `/tmp/swingset-stage2-source-pulse-20261109-2118/`.
The captured `run_20261109T211005Z.json` SHA-256 is `d36c4b9893c82cd828130e57a1e5c19e85b80e7a1bf7cc50458659d108e8d3e6`.

## Attended import and private preparation at 22:24 UTC

The active import is still on its original pinned runtime and fixed six-hour
ceiling. It has loaded and checked 21 tables, 4,104,928 rows. Loading includes
a typed Postgres check before each table enters the receipt. The large
`derivation_dependency_sets` table remains in that step. At 22:22 UTC, SQL
traffic back to the importer had reached 35.6 GB, up from 10.3 GB at 21:57;
the unchanged table count alone is not a stall. No native failure, restart
or out-of-memory kill is reported, and all seven original apps remain unchanged.
The minimum server free space observed is 673,398,095,872 bytes.

A separate read-only observer samples cgroup peaks and cumulative memory events
every twenty seconds until this native process exits, with a four-hour bound.
At 22:23 UTC the native peak is 8,589,938,688 bytes, the shared Postgres peak
27,201,572,864 bytes, and the private Postgres peak 80,015,360 bytes. These
include cache; the shared value covers the cluster and its other apps. All
three cumulative out-of-memory kill counters are zero. Its scratch record is
`/var/tmp/swingset-stage2-import-job-bdb466bb78e7/native-resources-live.json`.

Seven empty private volumes are prepared, with UID 10001 and mode 0700.
Their backing device matches the working state and private Postgres. No private
application database, credentialed private helper or graph verifier has started.
Static helper and supervisor review is accepted after fixes to transactions,
file boundaries, process admission and failure handling. Actual native completion,
identity, capacity admission and execution review are still required. The
private-copy guard must retain all owned claims on failure. Detailed scratch
records live at `/tmp/swingset-stage2-private-verification-20261109/`.

## Source collection pulse at 22:17 UTC

Twelve saved acquire, derive and plan reports through 22:15 UTC have no failure,
error or stage error. The inspected acquire ticks offer and check zero pages;
there are no reported paused hosts. The last observed real fetch is still the
21:10 EEPro page. The registry's daily cap was last checked at 19:07, so it is
not a fresh explanation for every empty tick. Investigate eligibility and
progress accounting in maintenance item 40; do not silence the idle signal.
The alert log's last line is still 21:05, preceding that real fetch. The
Wayback failure repeats an expired pause, already maintenance item 39.

The earlier backup-stale alarm has been overtaken by a new local set:
`set_20261109T151640Z`, schema 37, 47 segments and zero missing items. The
scheduled backup service exited zero at 15:24:08 UTC; its next timer is
November 10 at 04:00 UTC. The manifest SHA-256 is
`0fcaf4e7a4baf4ff46fddd5c5df5ab677658ad85987507f9e39896fe2419a997`.
These read-only checks do not repeat a restore drill. Raw reports, the manifest
and the alert tail stay in `/tmp/swingset-stage2-source-pulse-20261109-2217/`.
No production code, schedule, pause or collection request was changed.

## One controlled local catalog probe

At 23:01 UTC the unchanged source passes all 3,028 core cases, Ruff and mypy
in 192.20 seconds. Its 180-second timing gate remains open. After an
independent check, exactly two indexes in the disposable local test server
were rebuilt: their combined size falls from 105,414,656 to 172,032 bytes.
All 4,338 shared dependency rows and the database, role and container
signatures remain equal. There was no reset, restart, database drop or
remote operation. The check used the same source, selection, weights and
resource caps as the 207.85-second baseline. This single probe improves the
measurement but does not identify every source of the slowdown. No further
timing retry is planned
([receipt](../../evidence/runtime/postgres-stage2-2026-11-05/core-catalog-probe.json)).

## Source collection pulse at 23:28 UTC

Fifteen saved reports from 22:58 through 23:28 UTC report no failure or error.
The six acquire ticks report zero requests and no progress. A plan tick records
6,677 progress, which does not establish a page fetch. The last observed real
fetch remains the 21:10 EEPro page. At 22:35 the watchdog appends a fresh acquire
idle alert and repeats the expired Wayback failure. Maintenance items 39 and 40
remain open. These checks read saved reports and the alert tail only; no source
request, production code change or pause was issued.

Scratch capture: `/tmp/swingset-stage2-source-pulse-20261109-2328/`.

## Private verification preparation at 23:30 UTC

The attended native import has loaded and checked 87 tables, 19,597,988 rows.
It has not finished its full audit. There is no reported restart or
out-of-memory kill; all seven original apps remain unchanged. Its fixed
six-hour ceiling and prior runtime remain in force.

Seven reviewed public helper and policy files are staged for the next phase.
Their earlier versions remain in scratch. No private helper or graph process
was started. Static review accepts the verifier launcher and graph-result
inspector, conditional on actual native completion, private-copy identity,
capacity and a concrete execution packet. The inspector checks whole-graph
coverage, current code and policy inputs, the unchanged base checkpoint and
private database identity. Cleanup still needs a separate reviewed finish
marker after the verifier and all children stop. All prepared scripts and
detailed reviews remain in `/tmp/swingset-stage2-private-verification-20261109/`;
none is a completed rehearsal proof.

## All native tables loaded at 00:01 UTC

On November 10 all 141 tables and 23,433,486 rows have passed loading and
per-table checks. The native receipt remains incomplete with no final audit.
Constraints, the independent audit and the stored identity check are still
required; the fixed six-hour ceiling is unchanged. The seven existing apps
remain unchanged, with no reported restart or out-of-memory kill. No private
copy or graph process has started.

The observed loading receipt SHA-256 is
`a624157288706104cb97f34f245252434cd8d1649b87a3dab44567dcc536aafc`.
It stays in scratch as
`/tmp/swingset-stage2-import-third-20261109/native-all-tables-loaded-20261110.json`;
it does not authorize accepting the import.

The rehearsal hold preserves its original owner, reason and creation time.
It was reviewed at 00:01 UTC, with the next review at 01:01 UTC. The detailed
hold result stays in `/tmp/swingset-stage2-hold-review-20261110-0001/result.json`.

## Source collection pulse at 00:07 UTC

On November 10 the saved acquire report `run_20261110T000100Z` finishes at
00:05 UTC after 77 requests and 342,613 bytes from `points.worldsdc.com`.
Six pages changed. It reports no failure, error, stage error or paused host.
This proves actual production collection after the earlier empty ticks.
It is consistent with the UTC daily budget reset, but this check does not
query the budget ledger or establish the reason for every earlier empty tick.
Maintenance item 40 remains open.

Only saved reports were read; no production change or source request was
issued by the rehearsal. The two captured reports stay in
`/tmp/swingset-stage2-source-pulse-20261110-0007/`.

## Read-only audit diagnostic at 00:24 UTC

A single diagnostic runs inside the exact native importer with its existing
internal owner login and a read-only connection. It emits no SQL text or
credential value. The import's backend has an open audit transaction, about
1,098 seconds old, waiting for its client. Its last audit cursor names
`derivation_building`; the next table in the maintained algorithm is
`derivation_dependency_sets`. Together with the importer's active CPU, this
is consistent with computing the next SQLite table digest. It does not
prove another completed comparison or authorize skipping the audit.

The one-use script SHA-256 is
`eeff13b1d2103d9878f3bec31afd714e6863c64aaa99337dbe8c0961be0d8176`.
Its result stays in
`/tmp/swingset-stage2-import-third-20261109/audit-progress-once-result.json`.
It is not retried; the running producer, bounds and native receipt are unchanged.

## Saved core timings checked on November 10

Comparing the saved 171.67-second and 192.20-second runs finds 3,017 common
parallel cases. Their aggregate setup time rises from 410.45 to 492.35 seconds,
call time from 1,962.71 to 2,237.87, and teardown from 67.84 to 77.48. The ten
added parallel cases contribute only 3.83 aggregate seconds. Each run also has
its separate prologue. Concurrent case times and different historical weights
do not isolate the cause; the increase spans many existing files.

This read-only comparison does not rerun the core or change code, selection,
resource caps or the timing budget. The timing gate remains open. Detailed
results stay in
`/tmp/swingset-stage2-core-catalog-probe-20261109/saved-core-comparison-20261110.json`.

## Synthetic bytea probe at 00:48 UTC

One bounded read-only probe uses the existing native container's internal
worker login. It queries no application row. SQL reports 38,501,373,619
database bytes. Each query-size trial reads 8 MiB of synthetic bytes on the
actual relay path:

| Query size | Queries | Seconds | MB/s  |
| ---------- | ------- | ------- | ----- |
| 64 KiB     | 128     | 1.275   | 6.58  |
| 1 MiB      | 8       | 0.374   | 22.43 |
| 4 MiB      | 2       | 0.322   | 26.09 |

The whole invocation exits zero in about 4.3 seconds, inside its thirty-second
bound. It changes no application row, source code, running producer, route or
resource cap. The measurement supports investigating query-size overhead;
it does not prove a TCP cause or the real audit's speedup. The probe is not
repeated. The current import keeps its exact prior runtime and ceiling.

Script SHA-256:
`e95401cb37140cc6eb448e638365d9ebb650ba11d824dcbef2d8e353c3194fff`.
Detailed measurement receipt:
`/tmp/swingset-stage2-import-third-20261109/bytea-transfer-probe-result.json`.

## Source collection pulse at 00:59 UTC

The saved 00:50 acquire tick finishes at 00:54 UTC after 74 registry requests
and 720,335 bytes from `points.worldsdc.com`; four pages changed. The 00:52
derive tick finishes at 00:56 with 73 completed units. Neither reports a
failure, error or stage error; acquire reports no paused host. The inspected
alert tail has no line newer than November 9 at 22:35 UTC.

This is another read-only check of saved reports, not a rehearsal source
request. Detailed reports stay in
`/tmp/swingset-stage2-source-pulse-20261110-0100/`.

## Audit attention signal at 01:00 UTC

The supervisor raises its sixty-minute progress flag because the completed
table count remains 141 during the independent audit. The agent reports the
signal. Observed SQL return traffic rises from 64.7 GB at 00:32 to 95.4 GB
at 00:59, so the unchanged table counter does not establish a stalled reader.
The native receipt is still incomplete. No restart or out-of-memory kill is
reported, and the seven existing apps remain unchanged. The original fixed
ceiling remains about 01:24 UTC; no retry or producer change is made.

The captured signal and resource readings stay in
`/tmp/swingset-stage2-import-third-20261109/audit-attention-20261110-0100.jsonl`.

## Fixed-budget stop at 01:26 UTC

The supervisor enforces the 21,600-second ceiling and 120-second stop grace.
The native process exits 137 at 01:26:35 UTC on November 10, PID zero and
not OOM-killed. The final supervisor result correctly refuses success. Its
cached running-state field predates the stop; actual Docker state and the
resource observer confirm the process has exited. The seven original apps
remain unchanged. No reset, deletion or retry is performed.

All 141 loaded tables and 23,433,486 rows remain in the target. The native
receipt SHA-256 remains
`a624157288706104cb97f34f245252434cd8d1649b87a3dab44567dcc536aafc`,
with `complete=false` and no final audit. This proves completed per-table
loads, not an accepted import. Detailed stopped-state receipts stay in
`/tmp/swingset-stage2-import-third-20261109/`.

Observed cgroup peaks are 8,589,938,688 bytes for the native process,
42,323,988,480 for shared Postgres and 80,015,360 for private Postgres.
They include cache, and the shared value covers other apps. All three
observed OOM-kill counters are zero.

The measured 1 MiB chunk change is local only. Its existing Unicode test
loads the large value with 64 KiB chunks, validates completed values on
resume with 1 MiB, then repeats at 4 MiB with identical typed hashes. All
14 focused import cases pass in 4.55 seconds. Full validation and fresh
independent target adoption remain necessary before a new attempt
([stop receipt](../../evidence/runtime/postgres-stage2-2026-11-05/import-budget-stop.json)).

## Local validation room restored at 01:45 UTC

The Mac was slightly below the unchanged 110 GiB full-suite admission floor.
After the independent check, exactly three owned local `runtime.tar` files
were removed: the import-fix, private-verifier and verifier-ready image
archives dated November 9. Their byte-identical server archives, metadata,
installed proofs, images, containers, databases and volumes remain intact.
No dataset state was removed. The first guard stopped before any unlink
because system Python lacked a hashing helper; a separately checked manual
continuation used fresh pinned-Python identities, SHA-256 and open-file checks.

The three files total 746,268,672 logical bytes. Observed free space at the
next admission is 119,328,956,416 bytes; its change includes ambient activity
and is not an exact physical reclaim measurement. The new full run starts
with the same 110 GiB admission, 88 GiB stop and 80 GiB mandatory floors.
Detailed preflight, independent verdict, addendum and both outcomes stay in
`/tmp/swingset-stage2-local-archive-cleanup-20261110/`.

## Chunk-size validation at 02:11 UTC on November 10

The 1 MiB import/audit transfer chunk passes all 4,600 full-suite cases in
1,465.65 seconds, with zero failures or errors. All 933 bound inputs match
before and after the run. The package source identity is
`source-sha256:55a3b82c2a553ab09a6f4c53fa1372d450358022228b7e61fcd02513aea47992`.
The focused 14-case run also passed, including exact large Unicode values
resumed across 64 KiB, 1 MiB and 4 MiB chunk sizes. Core checks now run once on
this source. These tests do not prove the full-size server audit's duration.
The full log, JUnit result and input binding remain in scratch at
`/tmp/swingset-stage2-import-chunk-full-20261110/`.

The current core check selected all 3,028 cases and passed correctness in
222.68 seconds, exceeding its 180-second budget. Ruff passed and mypy checked
all 364 source files successfully. The command exited nonzero because of the
timing gate. No repeat or performance change follows from this result. The
saved complete core summary is
`/Users/skeswa/.local/share/swingset/core-runs/40133d81950b4440b17e162a/summary.json`;
its source binding still matches the full run's 933 inputs. Timing remains open.

## Tested runtime cache and refused adoption at 02:20 UTC on November 10

The new runtime is cached on Rhuidean, without starting an application process.
Its OCI artifact is
`sha256:4701803ef20c4115d1df943ee69beef871823886585bee21fbe6155046693044`;
its daemon configuration identity is
`sha256:54390d34f630b18ee02c9374fab48ff9e4bc7803878a21aba51d3bfc1c2dbbfc`.
The archive is 248,756,224 bytes and its SHA-256 is
`12dbd7ebf4975b988700ac7c699e065838ef6869b0865de39f2c52a7f192ffd6`.
All 545 installed package files, including 364 Python modules, and all ten
configuration/override inputs match the tested 933-input binding. The source
proof container `9595dd37b379bf8082ad59306bcfc8bae1ef65f9ff42679a1eee9592825b56c1`
remains created and unstarted. Scratch proof is at
`/tmp/swingset-stage2-image-import-resume-20261110/installed-proof.json`.
The global namespace policy and stopped native import still use the prior image.

The independent read-only adoption attempt refused at 02:17 UTC with
`InvalidParameterValue`, before confirming target identity or counts. Its
helper incorrectly requested `SET ROLE swingset_dryrun_owner`; provisioning
uses the shared capability role `swingset_owner`, which the migrate login
already inherits. This was an operator helper error, not a native import or
schema failure. The failed helper and outputs are retained; it is not replayed.
All seven original apps and five support services stayed unchanged. Fresh
adoption remains required, and no native resume is authorized by this failure.
The failed packet is in
`/tmp/swingset-stage2-import-resume-20261110/adoption/observed-328ad3b07e18/`.

A proposed resume wrapper remains unexecuted in scratch. It seals the prior
all-141-table receipt in root-owned read-only configuration, then creates a new
output receipt only after the maintained full source preflight and fresh
finalized-schema, UUID and quiescence checks. Independent review and fresh
adoption must pass before a separately recorded resume. Only 344 evidence bytes
remain in this change, so a new receipts revision also requires explicit commit
permission. The current core timing gate remains open.

## Source collection pulse at 02:22 UTC

Read-only saved run records show 128 derived units completed at 02:16 UTC and
6,677 planning units at 02:12 UTC. The 02:15 and 02:20 acquire ticks finish with
zero requests and no reported failure, error, stage error or paused host. The
last directly verified page requests remain the 00:50 acquire tick; these later
empty ticks do not establish fetching. Maintenance item 40 still owns the
eligibility/accounting investigation. The alert tail adds a 01:05 UTC repeat of
the already expired November 6 Wayback failure; item 39 owns that alarm. No
production code, settings or process was changed. The captured run records and
alert tail stay in `/tmp/swingset-stage2-source-pulse-20261110-0223/`.

## Maintained target inspection on November 10

The independent reviewer rejected replaying the corrected adoption helper:
AGENTS.md requires a script used twice to become a tested `swingset` command.
No corrected scratch helper or disguised manual replay ran. The maintained
`inspect-import` command now uses the explicit owner DSN in a forced read-only
connection, verifies a hash-bound all-loaded receipt, checks target name/OID,
C/UTF8, UUID, full finalized schema, all row counts and absence of other clients.
It reads the existing inherited owner capability without changing roles. It
neither resumes nor accepts a typed audit. The receipt stays unchanged.

All 31 focused import/utility cases pass in 7.17 seconds, including refusal for
missing loaded entries, changed counts, wrong OID/UUID and another client.
Ruff passes on the changed Python files. New full/core validation is required
on this added command before another image or adoption check. The prior
source55a test/image proof remains valid for that earlier source only. The
current preparation commit request must wait for validation of the final
source; no commit or native start has occurred.

At the 02:36 UTC hold review, the owner, original creation time and reason
remain unchanged. The next review is 03:36 UTC. The root-owned mode-0444 hold
was installed atomically and rehashed; all seven original apps and five support
services match the prior public Docker state. The held import stays stopped.
Scratch receipt: `/tmp/swingset-stage2-hold-review-20261110-0236/result.json`.
The new inspection command passes all 32 focused cases in 8.08 seconds. Its
independent static review accepts the fresh-connection read-only guard and
10-second lock/240-second statement limits. Source728 is frozen for the full
4,610-case run; no server inspection or native resume has started.

## Cross-role session visibility corrected at 02:43 UTC

The first full run of the new inspection command was interrupted deliberately
after 1,603 passing cases in 563.23 seconds. It is not a completed validation.
The parent verified pytest PID 3041, birth and exact arguments before SIGINT;
pytest exited before any source edit. Logs, JUnit and interruption metadata stay
in `/tmp/swingset-stage2-inspection-full-20261110/`.

PostgreSQL 17 hides `backend_type` from roles that cannot inspect another
session's detailed statistics. Filtering only `backend_type='client backend'`
could therefore miss another role's connection. The maintained inspection and
scratch resume guard now also refuse sessions whose backend type is NULL.
This fails closed without granting statistics privileges. The source database
and PID remain visible, so the count can still target only this database and
exclude the checker itself
([PostgreSQL implementation](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/utils/adt/pgstatfuncs.c)).

A real restricted-role regression sets the inspector's current role to
`swingset_owner`, verifies that another login's backend type is NULL, and
requires inspection refusal. All 33 focused import/utility cases pass in
6.97 seconds. The first regression run contained an incorrect Row-to-tuple
assertion; it was corrected to check the returned cell, and that failed output
is retained. Core checks now run on source
`source-sha256:e8ad7d89c848d349dcd0a13967ef7e39da128ed2a629c066ba24177a3efb8e6a`.
Full validation and a matching immutable image still remain. The previous
static quiescence acceptance is superseded; no server inspection or native
resume used it.

## Current core and scratch admission at 02:48 UTC

The final NULL-safe inspection source passes all 3,039 selected core cases in
187.54 seconds, plus Ruff and mypy across 364 source files. The check command
exits nonzero because the 180-second timing budget remains open. No timing
retry or further optimization follows. Summary:
`/Users/skeswa/.local/share/swingset/core-runs/feaeedf706454f4da4032bbf/summary.json`.
The new 4,611-case full run starts at 02:48 UTC on the same 933 inputs; it is
pending. Its source is
`source-sha256:e8ad7d89c848d349dcd0a13967ef7e39da128ed2a629c066ba24177a3efb8e6a`,
with input binding
`1d92b8eb77210dfa1651a291a0aa9af9c7181116b5a011b9e342bb04c7c2e8d9`.

The interrupted run left free disk below the unchanged 110 GiB full-run
admission floor. One independent reviewer freshly checked three exact duplicate
local runtime archives: file identities, complete hashes, no open users and
matching retained server copies. After renewed guards, three exact `/bin/rm`
commands removed only those local archives, 746,240,512 logical bytes. All
metadata, installed proofs, server archives, images, containers, databases and
volumes remain. Observed free space rose from 117,465,911,296 to 118,256,537,600
bytes while core work also ran; this is not an exact physical-reclaim claim.
The next full run independently admitted 118,557,061,120 free bytes. Its 88 GiB
stop and 80 GiB mandatory floors remain unchanged. Preflight, written verdict
and result stay in `/tmp/swingset-stage2-archive-room-20261110/`.

## Full validation passed at 03:10 UTC

All 4,611 full-suite cases pass in 1,318.74 seconds on source
`source-sha256:e8ad7d89c848d349dcd0a13967ef7e39da128ed2a629c066ba24177a3efb8e6a`.
There are no failures, errors or skips. The same 933 inputs match before and
after the run, and the 3,039-case core correctness result belongs to this same
source. Its 187.54-second timing result remains above the 180-second budget.
Ruff and mypy pass. No further timing retry runs.

JUnit SHA-256 is
`c9889bdd910af19e3a1c4adae86136b80f355870c9cc5b33a410f6ac33c2e906`;
the full log SHA-256 is
`a6ab1187bf5384f5cd0fc26c46e00b415e946ddedea4ac12883019de7e310f0a`.
The exact PID/birth/argument-bound disk watcher completed without signaling the
test process; its observed minimum was 114,631,487,488 free bytes, above the
unchanged 88 GiB stop floor. The complete result, binding, JUnit and guard receipt
stay in `/tmp/swingset-stage2-inspection-nullsafe-full-20261110/`.
A matching immutable runtime is now being built. No target inspection or native
resume has started on this source.

## Tested inspection runtime cached at 03:18 UTC

The exact validated source is cached on Rhuidean as daemon configuration
`sha256:e41409879b3e2f06133a993bd31ef12cd714e3a2508bcacaec7b2b53b4432ddb`
and OCI artifact
`sha256:236f7ae07eaf21607461162da226aff079843648f0c0cf8554e72bce3413585e`.
Its 248,759,296-byte archive has SHA-256
`0bc8d43b394f9b6363b2fe8dbf5f52a9d4c71452b0b80f70d961a6baacfe0382`,
matching the server copy. All 545 installed package files, including 364 Python
modules, and all ten configuration/override files match the full run's bound
inputs. The source proof container
`4e9ca00b7f0a30f299aa64ee05a4b714beec9d42323048d2868f295d60259e2b`
remains created and unstarted. Proofs stay in
`/tmp/swingset-stage2-image-nullsafe-inspection-20261110/`.

The independent reviewer is executing one separately bound read-only
`inspect-import` envelope. It receives only the scoped owner file and the old
native receipt as read-only mounts, plus a private bounded output tmpfs. It
cannot reset, resume or grant database privileges. Its outer 300-second timeout
includes credential loading, followed by a 30-second kill grace. No scratch
adoption algorithm is replayed. The native import remains stopped.

## Source collection pulse at 03:17 UTC

The saved 03:10 acquire record reports one real `eepro.com` request, 29,738
bytes, checked once with no changed page, failure, error, stage error or paused
host. The 03:16 acquire tick and both recent derive ticks are empty. Planning
reports 6,677 units at 03:11 UTC. A 03:05 idle alert says acquire had made no
progress since 02:03; the subsequent real request supersedes that point-in-time
alarm. This does not explain the recurring empty ticks, which remain maintenance
item 40. The earlier expired Wayback alert stays in item 39. Production code,
settings and processes were unchanged. Raw records and alert tail remain in
`/tmp/swingset-stage2-source-pulse-20261110-0317/`.

## Read-only target adoption passed

At 03:33 UTC on November 10, the independent reviewer accepted one observed
run of the maintained `inspect-import` command. The target is still database
`swingset_dryrun`, OID 16398, C/C and UTF8, with stored dry-run UUID
`9bccc2d4-73b4-4ef3-894d-861213ef9831`. Native schema 38, source schema 37,
the migration checksum and full structural parity match. All 141 counts match
23,433,486 loaded rows, with 809 finalized triggers and zero other target
clients. The database is 38,501,373,619 bytes. Its native receipt stays unchanged
at SHA-256 `a624157288706104cb97f34f245252434cd8d1649b87a3dab44567dcc536aafc`.

The command reports `native_complete=false` and `typed_values_checked=false`.
This validates a resume target; it does not accept the unfinished native audit
or register the UUID for external I/O. The one actual exec
`2abe5c26f30529e2a64198ab59136cbe1755b1bcf87d38455ada6802e73dfaf3`
exited zero in helper
`65eede727fa68425fa489b4de609c502fed068d443241cb05e85582303c0cba4`.
Its bounded result and empty stderr were copied before stopping that owned
helper. The helper remains stopped with PID zero. The native import,
namespace operator, seven original apps and five support services are unchanged.

The first transport admission failed before creating any helper or exec:
Docker omitted the optional `HostConfig.Tmpfs` key from the stopped native
metadata. The failed launcher and reservation remain unchanged. Corrected
ordinary Docker/API commands completed the still-unexecuted maintained
inspection under a separate recorded addendum; the failed launcher was not
replayed. No SQL inspection algorithm lived in that transport wrapper.

The 03:26 hold review preserves owner, reason and original creation time; the
next review is 04:26 UTC. The root-owned mode-0444 file was installed atomically
and rehashed. Scratch hold receipt:
`/tmp/swingset-stage2-hold-review-20261110-0326/result.json`.
The observed read-only result SHA-256 is
`9cb28eb14ea3efbfe8610da4ce71b649419c1dc7e44a8e7d5aea2d5ba88d27af`;
independent verdict SHA-256 is
`d0271eb761e662543c9c34eaaa4df5eb1d1a1d916665abcebf820bdaf84493a7`.
Both remain under
`/tmp/swingset-stage2-import-resume-20261110/maintained-inspection/`.

A proposed native resume plan and five configuration inputs are prepared only
in scratch, bound to the observed UUID, exact old all-table receipt and tested
image. They preserve all loaded data and the UUID, repeat full source validation
and both target checks/audit, keep the 21,600-second budget and all resource
limits, and refuse a missing loaded entry. No new native container, runtime
volume, global policy switch or resume was executed. Final conformance review
and retained preflight/postcheck capacity remain required. This revision still
has only 344 evidence bytes available; closing preparation and opening a new
receipts revision needs the requested explicit commit permission. Nothing was
committed or pushed.
