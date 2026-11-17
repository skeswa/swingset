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
| 2026-11-10 04:39 | 2026-11-10 05:39 | aa36dd1bfe64b55dec9eeadb9d44015ed757259f158a7c1f5e1b74cd48a70a84 |
| 2026-11-10 05:29 | 2026-11-10 06:29 | 63bcf8f640834b018c3045f9031d6fac34f09bd2ed0efdc88f8ff9885d2a3848 |
| 2026-11-10 06:16 | 2026-11-10 07:16 | 08f1d16ba1db819bf6a013ce9f2252de87806836ebe37024512bd38f97236ded |
| 2026-11-10 07:05 | 2026-11-10 08:05 | 47f9d1d87130c41d47ae23270824daaf664188ddbe410e3ee16f6ea53007d02d |
| 2026-11-10 07:55 | 2026-11-10 08:55 | 9b9bbe23cb73690f0c7d82a2ea713b9081244bc05481bbde0c0366a692a912b3 |
| 2026-11-10 08:43 | 2026-11-10 09:43 | 4956b4e8ba82201de2d742b980006e982aadabe2b7afb14895a99538177e95ea |
| 2026-11-10 09:30 | 2026-11-10 10:30 | fac92bc9e62232b83ec69bd234cadcc0d24772d8eaa21feb18bf3fa4d4369686 |
| 2026-11-10 10:08 | 2026-11-10 11:08 | 3e16e0b7afca81488a865048c49ca54ff952da22c456bed522147a4e068cdaf0 |
| 2026-11-10 10:50 | 2026-11-10 11:50 | 7f8f1cf92fc545a2e4910cc52839934fccc1d5087ce573772aec11a2fd2855e9 |
| 2026-11-10 11:40 | 2026-11-10 12:40 | 1fdd273feb1d51b86a466f6a6070a596a9bb30eb8917db843b26cdb18c1bdea1 |
| 2026-11-10 12:32 | 2026-11-10 13:32 | ba3c1658b732b5ef932ce58604c9e96d6792305f83fac5de47cb830ad4a02a1e |
| 2026-11-10 13:15 | 2026-11-10 14:15 | 6c65c6d6c8a9b549a1827fb1b0fc4ee0fabf57e0a2baab714d812c20b8f7b5f9 |
| 2026-11-10 13:56 | 2026-11-10 14:56 | d50e967d801e7a95e8bad24cf3a17f174a497d596ffab712d1751a1a19e4035c |
| 2026-11-10 14:38 | 2026-11-10 15:38 | 21d93b4bf9ae5a3abc05563d7d629beb0fc310bc1fa4ab29f1ed82c2bf85dfad |
| 2026-11-10 15:16 | 2026-11-10 16:16 | 01f02a7576535deda5fc695246ff8bd79ff7743284ac6ace399bbc3ee59d2297 |
| 2026-11-10 15:53 | 2026-11-10 16:53 | 713443a93a0e410829bd3d1d3afc83e2f389156c61295c1c76bfaa923f6a04a6 |
| 2026-11-10 16:38 | 2026-11-10 17:38 | 9a8531298ee46a54c835047fceffa5a4d6107b3e67df92f314cd5696f3684c58 |
| 2026-11-10 17:25 | 2026-11-10 18:25 | f3e6387850f5053cf32a412d3bf5e4aaabb77450acecd244d62c35b04973b0c4 |
| 2026-11-10 18:10 | 2026-11-10 19:10 | b41d18a1c7c0feac4c22e566d37d05b278d18598c4ec0b8e033c97d459362ef0 |

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

## Preparation closed and native resume staged

The owner authorized closing preparation and continuing on November 10.
Revision `tvwqxuul` closed as `6db1ca53`; new receipts change `xkotoozx`
continues stage 2. Nothing was pushed. The exact tested source and image remain
unchanged. The 04:39 hold review preserves owner, reason and original creation
time, with next review at 05:39 UTC. All seven original apps and five support
services still match their protected state.

A fresh independent run of the tested `inspect-import` command again verifies
UUID `9bccc2d4-73b4-4ef3-894d-861213ef9831`, finalized schema 38/source 37,
all 141 counts and 23,433,486 rows, 809 triggers and no other clients. Its
result is byte-identical to the earlier read-only result. The new helper is
retained stopped. This still does not accept the incomplete typed audit.
Verdict SHA-256:
`61bc107322d0d6f19c83c00eb1b1bc586742efdd9fbe597febc574519773e3b7`.
Packet: `/tmp/swingset-stage2-import-refresh-20261110-33020a6ce0a1/`.

Five configuration files are now sealed as root-owned mode 0444 files in a
mode-0555 directory on the server. They include the exact old all-loaded receipt
and fixed UUID. A fresh runtime volume has an empty private operator directory
owned by UID 10001. Native container
`87c80de753d301e52c0211c6ba7879b291e643dcb0a66e74d1f0be8341c63a03`
and namespace operator
`eda9d63d6805b3f6fe5361f5f35da9e7bfa771ff7a332a0a1fe7f3d7a4993118`
are created with PID zero. Neither has started, and the old global policy and
namespace operator remain in place. Final independent execution review is
pending. The plan SHA-256 is
`d57e25eaff456d4c94d60ae641ffa9f25c4af4cc589c819c8b6685929373ad77`.

## Source collection pulse at 04:54 UTC

The saved 04:46 and 04:51 acquire ticks report no requests or progress, with no
failure, error, stage error or paused host. Derive completes one unit at 04:43;
its later two ticks are empty. The 04:35 idle alarm says acquire made no
progress since 03:31. The last page request directly verified here remains the
03:10 EEPro request. Keep this alarm visible and investigate eligibility in
maintenance item 40; the expired Wayback alarm remains item 39. Production was
not changed. The scheduled local backup set `set_20261110T040000Z` is present,
schema 37, with 48 segments and no missing items. Its manifest SHA-256 is
`a5292d666ab825fabf6a1744be9ac1dabb06333c69735d1b065763b333b766f0`.
This read-only capture does not repeat a restore drill. Raw records remain in
`/tmp/swingset-stage2-source-pulse-20261110-0454/`.

The source backup service reports `Result=success`, `ExecMainStatus=0` and
`ActiveState=inactive`. Its actual next timer is November 11 at 04:00 UTC; the
older dashboard claim of a noon backup was stale. The timer and service capture
is retained in the same scratch pulse packet.

## Reviewed native resume started at 05:15 UTC

The independent execution review accepted the exact sealed plan at 05:11 UTC.
The compact [preflight](../../evidence/runtime/postgres-stage2-2026-11-10/import-resume-preflight.json)
was retained and hash-verified on the server before changing the operator or
policy. Its SHA-256 is
`209f7b8029af6cf0e5e87e286456111cb95c6add031ef12b78a34dbddf0c4527`.
The exact old policy is retained. Only the runtime image changed in the new
global policy; the old operator is stopped and retained.

The maintained supervisor started native container `87c80de753d3` at
05:15:33 UTC, with the reviewed namespace operator `eda9d63d6805` running.
The fixed six-hour ceiling ends at 11:15:33 UTC, followed by at most 120 seconds
of stop grace. There is no automatic retry. At 05:22 UTC the job is checking
source integrity. Its fresh output receipt does not yet exist: the sealed old
receipt still binds all 141 loaded tables and 23,433,486 rows. The destination
load session opens after full source validation. No table reload or identity reseed is
allowed.

All twelve protected services match their baseline after activation. The native
container has no OOM event or restart. Its cgroup peak reaches the 8 GiB limit
during source integrity checking; this includes file cache and is not a process
RSS measurement. Kernel OOM counters remain zero. The attended checks continue
alongside the maintained 20-second supervisor. The 04:39 hold review remains due
at 05:39 UTC. The immutable source, core timing result and open Gate 2 are
unchanged. Transition, supervisor, authorization and resource observations stay
in `/tmp/swingset-stage2-import-resume-20261110/`.

The follow-on private verification templates were refreshed for this exact
source, image, native container and new receipt volume. Their independent
static review accepts the files, without authorizing execution or cleanup.
The verifier now reuses the exact active namespace operator and checks its
profile and process birth; it no longer replaces an already matching policy
or operator. Nine public helper/configuration files are staged mode 0444 on
the server, with old bytes retained. No private copy or graph job has started.
Final actual native success, receipt hash, identity and capacity checks remain
required. The static verdict SHA-256 is
`768523d88d47c8ff7ddfed44b37e280a6524bf2d28c71ac3dbed37ee942ea1ce`;
its manifest is
`c3c28cb1f3939ecd0e34ffabda6f5f2ad176f73787128f10e191daf2e03c754a`.
Both stay in `/tmp/swingset-stage2-private-verification-refresh-20261110/`.

## Source collection pulse at 05:39 UTC

The 05:36 acquire record has zero requests or progress. The 05:33 and 05:38
derive records also have no completed units. All three report no failure,
error or stage error; acquire has no paused host. The 04:35 idle alarm remains
the latest alert, alongside the earlier expired Wayback alarm. The last real
page request directly verified here remains the 03:10 EEPro request. Empty
ticks are not evidence that fetching works; maintenance items 39/40 remain
open. Production code, settings and processes were unchanged. The captured
records stay in
`/tmp/swingset-stage2-import-resume-20261110/source-run-pulse-0539.jsonl`.

## Source collection pulse at 06:25 UTC

A search of today's saved run records identifies 03:31 as the latest actual
fetch: two `scoring.dance` requests, 408,180 bytes, two checked pages and one
changed page. That record has no failure, error, stage error or paused host.
This supersedes the earlier directly verified 03:10 request. The 06:21 acquire
and 06:23 derive ticks are empty and report no failure or error. The expired
Wayback alarm repeats at 06:05; the 04:35 idle alert remains present. These
remain maintenance items 39/40, without a production change in this session.
The captured records and alert tail stay in
`/tmp/swingset-stage2-import-resume-20261110/source-run-pulse-0625.jsonl`
and `source-alerts-0625.txt` beside it.

## Native source validation passed

At 07:06 UTC the reviewed resume completes full frozen-source validation in
6,643.358 seconds. All 141 table digests and 23,433,486 rows match the retained
source matrix. The file proof verifies 173,592 files and 2,166,067,811 bytes,
including thirteen input bundles and all 192 legacy code scopes, with no
missing finding declaration. The destination factory then accepts the exact
finalized schema, stored UUID and absence of other target clients.

The fresh runtime receipt is byte-identical to the sealed all-loaded receipt:
31,846 bytes, SHA-256
`a624157288706104cb97f34f245252434cd8d1649b87a3dab44567dcc536aafc`.
It still says `complete=false` and `audit=null`; this checkpoint does not accept
the target audit. The native process is rechecking every loaded target table.
At 07:08 UTC its network counter has received 549,195,061 bytes, with zero
network errors or drops. This observes activity, without claiming how many
target tables have passed. Native OOM counters remain zero. The exact
six-hour deadline is unchanged. Raw receipt, source-complete supervisor sample
and counter baseline stay in `/tmp/swingset-stage2-import-resume-20261110/`.

## Source collection pulse at 07:38 UTC

The 07:36 acquire and 07:33 derive ticks complete with zero requests or units,
and no failure, error or stage error. Today's saved records contain no
`failed=true` result or nonempty error string. Their latest real fetch remains
the 03:31 `scoring.dance` run. The idle alert repeats at 07:05; the expired
Wayback alarm's latest repeat is 06:05. Maintenance items 39/40 remain open;
production code, settings and processes are unchanged. The captured records
and alert tail stay in
`/tmp/swingset-stage2-import-resume-20261110/source-run-pulse-0738.jsonl`
and `source-alerts-0738.txt` beside it.

## Native progress alarm review

At 08:08 UTC the maintained supervisor raises its 60-minute progress alarm.
Its signature uses the loaded-table count and source phase. Both remain at
141/validated during already-loaded target checks and the independent audit;
those paths emit no intermediate progress record. The alarm is retained.

The independent reviewer observes the same native incarnation at
08:11:32 through 08:11:40. Over that interval, CPU time advances 4.961 seconds,
disk reads advance 1,113,014,272 bytes and writes advance 196,571,136 bytes.
All twelve protected services, the namespace operator, policy and current
hold match. OOM counters remain zero and about 660.5 GB is free. Most charged
memory is file cache. These counters show activity, without excluding cache
churn or repeated scans and without proving any audit phase complete.

The written verdict is `CONTINUE_EXISTING_BOUNDED_RUN`: keep attended short
interval checks, the raised alarm, the 21,600-second ceiling and 120-second
stop grace. It authorizes no private operation or completion claim. Verdict
SHA-256 is
`1bb6c2736ac794257280ae99296c55d88221742c8de338578239aea5fb01d479`;
the filtered observation SHA-256 is
`87b2f107832dc90f860d0d7dd0ff86c877b56def156f90b2844d5d5cce592f40`.
Both remain in `/tmp/swingset-stage2-import-resume-20261110/`.

## Source collection pulse at 08:39 UTC

The 08:36 acquire and 08:33 derive ticks complete with zero requests or units,
and no failure, error or stage error. Today's saved records still contain no
`failed=true` result or nonempty error string; their latest real fetch is the
03:31 run. The expired Wayback alarm repeats at 08:35, and the idle alarm's
latest repeat is 07:05. Maintenance items 39/40 remain open. Production code,
settings and processes are unchanged. Raw records and alerts stay in
`/tmp/swingset-stage2-import-resume-20261110/source-run-pulse-0839.jsonl`
and `source-alerts-0839.txt` beside it.

## Source collection pulse at 09:39 UTC

The 09:36 acquire tick makes two real `scoring.dance` requests, receives
408,182 bytes and checks two pages. Neither page changes; one response is
reported not modified. There is no failure, error, stage error or paused
host. These requests supersede the 07:05 point-in-time idle alarm. The 09:33
derive tick is empty. Today's saved records contain no `failed=true` result
or nonempty error string. The expired Wayback alarm's latest repeat remains
08:35; maintenance items 39/40 remain open. Production code, settings and
processes are unchanged. Raw records and alerts stay in
`/tmp/swingset-stage2-import-resume-20261110/source-run-pulse-0939.jsonl`
and `source-alerts-0939.txt` beside it.

## Native audit passed at 09:40 UTC

The reviewed resume exits zero at 09:40:11 UTC, with PID zero and no OOM
kill. Its final native receipt reports `complete=true` and `audit.equal=true`.
All 141 loaded tables, 23,433,486 rows and typed digests match the frozen
source and independent final audit. The source hash and migration checksum
are unchanged. Native time is 15,869.686 seconds, about four hours and
24 minutes. The final receipt is 47,608 bytes, SHA-256
`56aa88e85d2c4d35d605bb51196fcf59ef8d922f9158a8834f00e2b0f07b1d6a`;
the typed matrix hash is
`b9b109cbb91fc914ca43a9230bdd5e17112e70130d40e8d664a4a76654316301`.

The supervisor also completes successfully and preserves its raised progress
alarm. Its cached `import_progress.complete=false` predates the terminal
receipt; acceptance uses the actual final receipt and process exit. The
independent alarm review above and original fixed bounds remain recorded.
Minimum observed server free space is 657,380,659,200 bytes; all twelve
protected services remain unchanged. The largest observed native cgroup
charge is 8,589,938,688 bytes, including file cache and one accounting page
above the configured eight GiB limit; this is not process RSS. Observed
OOM counters stay zero. Raw terminal and resource records remain in
`/tmp/swingset-stage2-import-resume-20261110/`.

Independent terminal identity inspection passes and closes its reader at
09:51 UTC. The stored dry-run UUID, OID 16398, C locale, UTF-8 encoding,
finalized schema 38, 809 triggers, all 141 row counts and absence of other
target clients match. Its maintained command exits zero and makes no
persistent write. This is a stored-identity/count check, separate from the
native typed audit. The inert reader is deliberately stopped with its
30-second grace; its sleep process exits 137 without an OOM kill.

The [compact postcheck](../../evidence/runtime/postgres-stage2-2026-11-10/import-resume-postcheck.json)
binds both results and their actual receipt hashes. The separate working-file
and capacity postcheck remains open. No private copy, whole-graph run, re-key,
publication or recovery step has started. Gate 2 remains open.

## Read-only working-file postcheck

The independently reviewed watcher starts once at 09:56:31 UTC, with its
process identity retained. The file postcheck starts once at 09:56:53 UTC
after the watcher acknowledges its recorded pending exec and the SQL-only
firewall is verified. Its helper is
`51ca782b52b85574701c83c364d00370e49dfefd8722ff4373f0cc69dc79155a`;
its exec is
`e4b37ce8c9034b4c98ed76eb9373b9840c4a996f8e2592a4fcdcfa983489efc6`.

The check receives only shared/private worker credentials, six read-only
mounts and one fresh output volume. The native receipt, frozen source and
working state are read-only. It checks stored identity/schema, separate
working-file closure and capacity for nine database copies above the 200 GB
floor. Its unchanged bound is 7,200 seconds plus 120 seconds stop grace,
eight GiB, six CPUs and 256 processes. No private copy or graph has started.

The final watcher plan hash is
`8a1094775c478492f2bbb887174ca5683c3e6684b4fc17a652fbfc3bafb42656`;
the postcheck plan hash is
`8161e1155c1a368fdbd470f8cb9c235d3ba59ce2f34fd68cb25f45fdde11f880`.
The independent execution verdict hash is
`66ee3fc6868b602df0d038261932011a9cf98ae36bb00d0475b6a498d4b26aeb`.
Plans, scripts and tracking output stay under server scratch
`/var/tmp/swingset-stage2-private-verification-e79c5fb48d30/` and local
`/tmp/swingset-stage2-private-verification-refresh-20261110/`.

## Source collection pulse at 10:14 UTC

The 10:06 acquire and 10:13 derive ticks are empty and finish without a
failure, error or stage error. Today's saved records still contain no
`failed=true` result or nonempty error string. The latest actual fetch
remains the 09:36 run: two scoring.dance requests and 408,182 bytes. The
alert tail is unchanged, ending with the expired Wayback warning at 08:35.
Maintenance items 39/40 remain open; no production change was made. Raw
records and alerts stay in
`/tmp/swingset-stage2-import-resume-20261110/source-run-pulse-1014.jsonl`
and `source-alerts-1014.txt` beside it.

## Existing core timing analysis at 10:25 UTC

A read of the completed 3,039-case report finds 2,757.076 seconds across the parallel cases: call 2,191.909, setup 487.116 and teardown 78.051. Ideal division
across sixteen workers uses 172.317 seconds. Adding mean client collection
and the serial prologue gives 180.858 seconds before other startup and
cleanup costs. This holds observed case costs fixed; a different schedule
can change contention, so it is not a new causal experiment or a proof of
a specific fix. The controller's observed total remains 187.506 seconds.

The [compact analysis](../../evidence/runtime/postgres-stage2-2026-11-10/core-existing-timings.json)
binds the existing report and source. No test, repair, source edit, selection
change or timing retry ran. The 180-second gate remains open.

## Working-file postcheck passed

The bounded reader exits zero after 1,632.956 seconds, about 27 minutes.
Its result matches the exact dry-run UUID, schema, native receipt and typed
matrix. Every working-file count matches the native proof: 173,592 files,
2,166,067,811 bytes, thirteen input bundles, 192 legacy code scopes and no
missing finding declaration. No persistent database write occurs.

Capacity at the start of this check is 660,826,558,464 available bytes,
above the 546,546,203,723-byte requirement for nine database copies plus
the 200 GB floor. The copy launcher must recheck current free space before
starting. Shared database size is 38,505,133,747 bytes; private Postgres
still contains only its three default databases. The maximum observed
reader cgroup charge is 3,418,066,944 bytes, with OOM counters zero.
The watcher keeps all twelve protected services unchanged.

The [compact receipt](../../evidence/runtime/postgres-stage2-2026-11-10/working-files-postcheck.json)
binds the actual exec, completion witness and result hash
`fdd5907979b37945bae8470e6bb19f4290a6d9978ff457f683b410f46a0cb9d1`.
The inert helper stays running to preserve later phase witnesses. The
concrete copy plan is frozen and awaits independent execution review;
no private copy, graph, cleanup or external publication has started.

## Private copy started at 10:30 UTC

The independently reviewed launcher starts once at 10:30:10 UTC. Its
trusted holder is
`cf365df6596dfed30113dc5b022497c969e54232e3a54fffc29e6faac5ae6596`;
the recorded exec is
`2c83652df26c79be3c0c88db08405f4d6919339c2df1b8b720945379aba72309`.
The watcher acknowledges the pending exec before its one start. The
SQL-only firewall, empty destination volumes and exact shared dry-run UUID
are checked first. Source access is forced read-only.

The [compact start receipt](../../evidence/runtime/postgres-stage2-2026-11-10/private-copy-start.json)
binds plan `a9fbe2e492ce590c2e2832de90039f5705539067467f47fe77e476698a122152`
and independent verdict
`964dc15f0be579bc572b63ef3914c5737a4a4c61b7681674c3c4ab196eac0845`.
The scope is fresh private dev base creation, all 141 typed-table audits and
a ready/wait state. No graph, finish marker or database deletion is
authorized yet. The 86,400-second bound includes the later wait, with
120-second stop grace, eight GiB, six CPUs and 256 processes. Failures keep
the private base and ownership registry.

The Mac's old owned keep-awake process is replaced after an exact PID,
birth and command check. The new system-sleep assertion is bounded through
November 11 at 16:30 UTC; the display may sleep. No permanent power setting
changes. Its ownership metadata stays beside the local private-copy packet.

## Source fetch idle at 10:41 UTC

The latest actual fetch remains 09:36, over an hour earlier. The 10:36 and
10:40 acquire ticks make no request and finish without a failure, error,
stage error or paused host. The latest completed derive tick starts at
10:37:10 and also does no work, without an error. The 10:40 plan tick does
report progress 6,677; this is planning progress, not fetched pages.
Today's saved records still contain no `failed=true` or nonempty error
string. The alert tail is unchanged, ending with the expired Wayback
warning at 08:35 rather than a new idle line.

The owner is informed of the observed fetch inactivity; maintenance items
39/40 remain open. The production code freeze remains in place. This
session changes no source setting or process. Captured records stay in
`/tmp/swingset-stage2-import-resume-20261110/source-idle-pulse-1040.txt`
and `source-runs-1041.jsonl` beside it.

At the 12:34 and 13:18 checks the latest actual fetch still starts at 09:36. The
watchdog records acquire idle at 11:05 and repeats the expired Wayback
warning. Today's saved run files still contain no failed flag or nonempty
error string. No production setting or process changes.

## Private copy audit boundary and wait failure

The E414 copy audits all 141 tables successfully and writes its complete
ready receipt, SHA-256
`29f5b81706f4015c939791c7a09f26bffd4aa1c6f9688f0d559cdee392acf892`.
It covers 108,390 units with set digest
`d58f3f45406d1ebdb9e4c5fcd9dcc4cd525118971c97089039206bad47665228`.
The private dev UUID is `2f58b861-ebd6-4fd6-a9aa-44a020589439`.
It then fails with `ValueError` in the wait phase. The statistics query
returns duplicate `coalesce` column labels; the row adapter rejects
duplicate labels. The scratch executor is retained unchanged and never
replayed.

The supervisor records the main exec's exit 1 and stops only its two owned
helpers. Their sleep processes exit 137 after their stop grace, with PID
zero and no OOM kill: holder at 11:30:41, postcheck reader at 11:32:42.
There is no database cleanup or automatic retry. Its cached live job
fields predate the stop; actual terminal Docker metadata is authoritative.
Independent failure verdict SHA-256 is
`ecf8522fc9cf04e06fc60765ec7f9258dce383d72abd6d3bfbd271177522b83b`.

A fresh private-only read-only reader then verifies base
`sst_copy_c696fead2daf4d3c83adfbbe_0`, OID 16396, 38,409,934,515 bytes,
C locale, UTF-8, the expected dev UUID and exact ownership comment. The
catalog contains defaults plus this base only; the worker has no owner
or administrator privilege and two NULL-safe probes find no other base
client. One bounded SQL exec exits zero in 1.48 seconds. Its output SHA is
`6fa290d39a8066ce5779fd0724c35f7c6fca22308984b017a7c892618ad245f9`;
its independent verdict SHA is
`ae5022a6298c3fd3ed7c324d9214ab2afa9584267eb5577d47b2ef3a1f38bd50`.
The new reader is stopped at 11:40:34, PID zero, no OOM kill. All twelve
protected services are unchanged. No source SQL, registry edit, base
adoption, graph or drop occurs.

The [compact boundary receipt](../../evidence/runtime/postgres-stage2-2026-11-10/private-copy-boundary.json)
binds these outcomes. Raw boundary and metadata packets stay in
`/tmp/swingset-stage2-private-verification-refresh-20261110/copy-boundary-failure-independent/`
and `/tmp/swingset-stage2-private-base-metadata-20261110-544ce0ab1cdb/`.

## Operator release dispatch and final graph candidate

The operator wrapper allows obsolete `loop`, absent from the CLI, and
rejects the actual `release` command. An independent review confirms no
supported one-shot publication through E414's guard. The local fix
replaces that allowlist entry. Three regressions reproduce the mismatch;
all eleven focused utility tests then pass. The new tests dispatch through
the real CLI, preserve dry-run by default, require explicit publish and
keep direct verifier publication refused before authorization.

The [completed check](../../evidence/runtime/postgres-stage2-2026-11-10/developer-release-dispatch.json)
passes all 3,042 cases in 178.72 seconds with unchanged limits, plus Ruff
and mypy. This does not isolate a timing cause. Its source predates the
subsequent verifier-supervisor adaptation, whose checks remain pending.

A new immutable candidate changes `version/repository`, so its final graph
proof must use that candidate. The completed base audit is retained as a
boundary. A maintained verifier supervisor reuses the existing import
supervisor's start/stop rules and adds private-role, mount, namespace,
capacity and memory guards. Full validation, immutable image proof and an
independent lifecycle/image/ownership transition review must pass before
any graph starts. No receipt is rewritten and no old revision is spoofed.

The final static review accepts all eight frozen implementation/test inputs;
57 focused cases pass, including the real PostgreSQL statistics query. The
core job `d3342f3f52724cbcb4393630` passes all 3,066 selected cases in
173.15 seconds, within the unchanged 180-second bound. Ruff and mypy pass
for 365 source files. This run does not isolate a timing cause. Native full
validation starts at 12:20:25 UTC with all 935 inputs frozen; its result is
recorded below. Source revision is
`source-sha256:9b9a92e0ab2ff8b491cc9f3b8323434c17b01ccccc058c5459d3b5de8d70223c`.

The [final validation](../../evidence/runtime/postgres-stage2-2026-11-10/developer-verifier-supervisor.json)
passes all 4,638 full-suite cases in 3,508.20 seconds, with zero failures,
errors or skips. Two existing JUnit property warnings remain. The command
exits zero; all 935 inputs are unchanged after the run and formatting.
This proves the final source, not a server image or graph transition.

## Retained private base data seal

The [read-only preseal](../../evidence/runtime/postgres-stage2-2026-11-10/retained-base-preseal.json)
exits zero in 258.756 seconds. It confirms OID 16396, the private dev UUID,
ownership comment, schema, defaults-plus-base catalog, no other base client,
and all 108,390 units with the ready receipt's digest. Its checkpoint is
`00c831a474ecfa1de29ca0e8402366e33ed62cd5d409a0f294d9db9c4a0e2f37`.
The historical context is 16,118 bytes, SHA-256
`fb1ec09c8836089ca46f7b04f98d887c1ddeb970c980e3ac033dcb16226cbfdf`.

E414 supplies 542 unchanged library files for this data-only observation.
It does not prove the new candidate's accepted versions or inputs. A fresh
worker-only reader receives SQL-only access to private Postgres after its
actual PID, birth and network namespace are checked. No database write,
input acceptance, registry adoption or graph occurs.

Docker archive and copy cannot see the live output tmpfs. Bounded,
nofollow file transport instead preserves both outputs locally and on the
server; the result matches the captured business stdout and stderr is empty.
Only after both copies are durable is the owned sleep reader stopped at
13:08:52 UTC, exit 137, PID zero, without an OOM kill. The business exec's
exit zero is separate. All twelve protected containers remain unchanged.

Independent review accepts the data seal with a monitoring limitation:
checks are periodic, with a largest reported parent gap of about 88 seconds,
rather than the planned uninterrupted 20-second cadence. The record cannot
exclude every transient peer or resource change. The graph still requires
the maintained supervisor and its own concrete execution review; no replay
is proposed. At this preseal, the existing security script and network
policy are UID 1000/mode 0644 with unchanged hashes. The later image
transition must seal them root-owned and mode 0444 before the new supervisor starts.

## Verified runtime and namespace transition

The [immutable image proof](../../evidence/runtime/postgres-stage2-2026-11-10/image-verifier-supervisor.json)
binds config `2a8fddd0`, OCI manifest `dcee56f5` and the truthful final
source revision. The 248,771,584-byte archive passes its SHA-256 check and
loads on Rhuidean. An unstarted proof container supplies all 546 installed
package files, including 365 Python modules, and all ten config/override
files. Every byte matches the completed 935-input validation binding.

The [transition preflight](../../evidence/runtime/postgres-stage2-2026-11-10/runtime-transition-preflight.json)
and [postcheck](../../evidence/runtime/postgres-stage2-2026-11-10/runtime-transition-postcheck.json)
record the independently reviewed image-only policy change. Old EDA stops
at 13:38:11 UTC, exit 143, PID zero, without an OOM kill. One helper with
CHOWN only and two writable public-file binds seals the security script
and new policy to root ownership and mode 0444; their contents stay fixed.
The host atomically installs the new policy before creating the replacement
operator. No credential or app directory is mounted into the sealing helper.

New operator `20443b3c` starts at 13:45:35 UTC, PID 1513012, birth 971477399,
on the unchanged boot. Its image, code, four mounts, three capabilities,
limits and effective profile match the old operator. Docker reports the
OOM-disable default as null, false while cold, then null while running;
only these enabled-OOM-killer forms are accepted. The old operator and
sealing helper remain stopped. All three control files are regular,
root-owned, mode 0444 and single-link. The twelve protected containers
and hold identity remain unchanged.

Independent postcheck accepts this transition. It does not authorize a
graph, base cleanup, registry adoption, re-key or publication. The verifier
is prepared separately in a stopped container, with the original private
base retained and a fresh empty graph ownership registry.

## First graph startup fails before copying

The [failed attempt](../../evidence/runtime/postgres-stage2-2026-11-10/graph-startup-permission-failure.json)
starts once under the maintained supervisor at 14:01:48 UTC. Its database
identity log names the expected private dev UUID. The process then fails
reading `/app/overrides/series_aliases.csv`: Docker COPY kept its host mode
0600 while assigning root ownership, so UID 10001 cannot read it. The
byte-only image proof did not catch this permission defect.

Actual Docker state is exit 1, PID zero, no OOM kill, finished 14:02:01 UTC.
The supervisor records failure after 22 seconds; its cached running-state
field predates that terminal observation and is not authoritative. No
graph database, receipt or ownership claim is created. Independent read-only
checks find the retained base alone beside the defaults, with its exact
OID and ownership comment and no client session. The twelve protected
containers, namespace operator, root files and hold remain unchanged. The
failed container and supervisor are not restarted.

## Public input permissions and current validation

The [packaging fix and smoke](../../evidence/runtime/postgres-stage2-2026-11-10/runtime-public-input-permissions.json)
change only the Dockerfile. Public source, config and override trees gain
read/traverse permission before installation. A build step under UID 10001
opens every public source and installed input file. The new immutable image
has config `d0450245` and OCI manifest `123e4fef`; source identity `9b9a92e0`
is unchanged because no package Python file changed.

An isolated UID-10001 process then opens all 1,102 public files and runs the
complete maintained input capture. It captures 562 files and 23,113,476
bytes, bundle `c107f5aa`, with root read-only, no capabilities, no network
and no database or storage credential. It exits zero without an OOM kill;
the formerly unreadable CSV is root-owned mode 0644. Installed package and
config bytes still match all 934 unchanged non-Dockerfile inputs.

The current binding is `c9704d91`, not the earlier full run's `2bb650dc`.
Those 4,638 passes remain unchanged application-code evidence. A fresh full
run starts at 14:45 UTC against the new binding. It is later interrupted before
the necessary guard fix; the partial result is recorded below.
The [completed fixture cleanup](../../evidence/runtime/postgres-stage2-2026-11-10/completed-full-fixture-cleanup.json)
removes only the independently reviewed, finished pytest-3269 tree, after
fresh owner, process, open-file, mount and catalog checks under both test
locks. Unlinking reclaims about 29.7 GiB; no chmod or database cleanup occurs.
All full-run results and reference copies remain outside that tree.

Current core job `951f1837ec444e719ec07afb` passes all 3,066 cases, Ruff and
mypy, but takes 205.58 seconds and misses the 180-second budget. Its case
membership and resource limits match the 173-second run; measured scheduling
weights and assignments differ. The timing gate remains open. The trigger
for further investigation is this measured miss with unchanged application
code: inspect existing per-case reports and weight provenance before any
controlled fix or retiming. No automatic timing retry is authorized. Keep
the case selection, assertions, four clients, sixteen workers and limits fixed.

## Replacement graph stops on a supervisor timeout

The [policy preflight](../../evidence/runtime/postgres-stage2-2026-11-10/readable-policy-preflight.json)
and [postcheck](../../evidence/runtime/postgres-stage2-2026-11-10/readable-policy-postcheck.json)
bind image D045 and the root-owned read-only policy to new namespace operator
`e7a1402f`. All twelve monitored container states remain unchanged. Independent
review permits the whole graph and cleanup of its own new copies; retained-base
cleanup stays excluded.

The [new graph](../../evidence/runtime/postgres-stage2-2026-11-10/readable-graph-start.json)
starts once at 15:03:52 UTC. The maintained supervisor verifies its private UUID,
authorization and network namespace, then samples resources and SQL progress.
At the last successful sample, 15:18:36 UTC, the private catalog has two scratch
databases and all kernel OOM-kill counters remain zero. The supervisor then
records `TimeoutExpired`, stops the owned actor and confirms exit 137, PID zero,
no OOM kill at 15:21:18 UTC. Its cached running-state field predates that stop.
This is a failed operation, not a graph acceptance. Preserve the attempt and
inspect the timeout and remaining ownership before any separate operation.

## Current core cold-preparation measurement

The [existing-report analysis](../../evidence/runtime/postgres-stage2-2026-11-10/core-cold-preparation-analysis.json)
finds that 29.3 seconds of the 32.4-second increase precede the first client
sample. Changing the Dockerfile invalidates the native Linux core image cache;
its new build accounts for about 29 seconds. The longest complete client grows
by 3.0 seconds. The scheduling weights come from prior Linux core runs; the
native full run does not train them. These observations leave the actual
205.58-second failure intact.

One matched warm-cache comparison may run only after the current full suite
finishes successfully, resources are idle and binding `c9704d91` is unchanged.
It must use the exact failed run's input weights, membership and caps through
the normal check command. Preserve the prior cache before a temporary atomic
pin under the core lock; release that lock before the normal command acquires
it. Validate the actual job's inputs and absence of another launcher, then
preserve its output and compare-and-restore the prior cache under the lock.
A changed input, unexpected build, competing job or ambiguous result invalidates
the comparison. No second timing attempt or timing subtraction is authorized.
The separate graph timeout may require a source change, which would invalidate
this frozen-binding comparison before it starts.

## Guard failure boundary and source fix

The [independent boundary](../../evidence/runtime/postgres-stage2-2026-11-10/graph-supervisor-timeout-boundary.json)
confirms the namespace exits one after its status export times out inspecting
the completed hold-install helper. The host supervisor's own timed-out command
was not logged and cannot be identified. Event-emission delays do not establish
a daemon-wide cause. A partial copy `69e883f3` and its registry survive; there
is no graph receipt, shard proof, acceptance or cleanup authorization. The
original retained base keeps OID 16396. Fresh checks find all twelve protected
container states unchanged; the guard itself is stopped.

The [interrupted full run](../../evidence/runtime/postgres-stage2-2026-11-10/developer-full-interrupted.json)
ends by a deliberate SIGINT before the necessary source fix. It reports 2,734
passes, no failures, and exit two after 2,906.22 seconds. Its complete 935-input
binding stays unchanged; its processes and test owners are gone. This is partial
validation, not a full pass. The proposed C970 warm-cache comparison is canceled
without execution or cache changes because the fix changes the source.

The guard now separates status collection from publication. A collection error
publishes fresh incomplete inventory and observer failure with a sanitized error
class; critical authorization and publication failures still propagate. The
supervisor retains the first failed Docker action, its pinned target and timeout,
without arguments or output. Failed jobs distinguish last observed running state
from a confirmed terminal state or an explicit unknown requiring escalation.
These changes do not establish that a later host Docker request cannot time out.

## Healthchecks.io selected

The owner selects Healthchecks.io on November 10, replacing the earlier provider
exclusion. Slack stays excluded. A project-only API key is requested in the
private server template; no key or private ping URL is requested in chat.
Separate alert and liveness checks must preserve an alarm when heartbeat pings
succeed. Provisioning, owner delivery and missed-heartbeat tests remain open.

## Source fetch resumes at 15:40 UTC

The 15:43 read-only source check finds `run_20261110T154007Z`: two requests
and two checked pages from scoring.dance, 13,423,440 bytes, two progress events,
`failed=false`, no error, no stage errors and no paused host. This proves actual
fetching after the earlier 09:36-to-15:40 gap. No source deploy or repair occurred.
At 15:35 the watchdog reports 77.8 GB state above its 50 GB cap, paused remote
backup upload with the successful 04:00 local set, and 629 partitions withheld
for unavailable source support. These remain production limits, not rehearsal
successes or fixes.

## Current guard candidate validation

The [guard candidate](../../evidence/runtime/postgres-stage2-2026-11-10/developer-guard-status-isolation.json)
passes 108 focused checks, all 3,074 core cases, Ruff and mypy. The normal core
takes 231.14 seconds, so the 180-second gate remains open. Its source is
`94c8cb77`, binding `4c7b6dbe`. Independent static review accepts the guard
failure separation and privacy/terminal-state regressions. A fresh required
full suite starts at 15:49 UTC on that frozen binding with 120.09 GB free;
its result is pending.

The [existing-report analysis](../../evidence/runtime/postgres-stage2-2026-11-10/core-guard-shared-work-analysis.json)
finds warm startup, but shared-case work rises 31.17 percent. The eight new
regressions add only 0.018 aggregate seconds. Better assignment balance cannot
fit the measured work into 180 seconds; the existing reports lack CPU, I/O and
SQL-wait observations needed to identify the underlying cause. No timing retry,
weight pin, cache rewrite, resource change or optimization follows.

The [current image proof](../../evidence/runtime/postgres-stage2-2026-11-10/image-guard-status-isolation.json)
binds OCI manifest `8190b606`, configuration `2cc3f9d1`, AMD64 and source
`94c8cb77`. Mac containerd identifies the image by manifest; the server identifies
it by configuration. Their hash chain is checked. All 546 installed package
files and ten config/override files match the frozen validation inputs. The
proof container remains created with PID zero; no process, namespace transition
or new graph has run from this image yet.

The independently reviewed pytest-3275 tree is already absent when the manual
cleanup's first fresh guard runs. No manual move, removal, chmod or reservation
occurs. Its removal by ordinary focused-test retention is inferred. All retained
interrupted-run receipts still match their hashes; actual free space exceeds
the next full run's 110 GiB admission requirement.

## Healthchecks.io provider drill

The project key authenticates to an empty project with one email integration.
Two check creations return HTTP 201. The event-alarm check has a 365-day period
for this bounded rehearsal and receives failure signals; it is not a heartbeat.
The separate watchdog check expects a ping every five minutes, plus two minutes
of grace. Both require POST. Their private URLs stay in mode-0600 server files;
the management key is not mounted into application containers.

At 15:48:26 UTC the controlled event failure receives the provider's exact `OK`
response. At 15:48:28 the watchdog receives one successful ping, then no further
ping. At 15:56:14 both check statuses are down, with exactly one ping each and
the configured email integration attached. This proves provider acceptance and
its missing-heartbeat transition, not receipt in the owner's inbox or guarded
application transport. Owner confirmation is requested separately.

At 15:57 the test event alarm is cleared by its own success signal, and the
watchdog check is paused until the application's first authorized ping. The
original down observations remain durable on the server. No rehearsal pipeline
or scheduler is claimed to be monitored while it remains unstarted.

The owner confirms both test emails arrived. The
[provider drill](../../evidence/runtime/postgres-stage2-2026-11-10/healthchecks-provider-drill.json)
therefore proves the configured provider's explicit alert and missing-heartbeat
notification. Delivery from the guarded application topology remains open.

## Guard transition and fresh graph

The [preflight](../../evidence/runtime/postgres-stage2-2026-11-10/guard-transition-preflight.json)
and [postcheck](../../evidence/runtime/postgres-stage2-2026-11-10/guard-transition-postcheck.json)
record installation of source `8d69abde` and policy `ea8efe99` as root-owned,
mode-0444 single-link files. New operator `420954b2` starts once at 16:19:15 UTC,
PID 1863060, birth 972399397, on the unchanged boot. Its complete profile and
mount objects match the previous operator. Sorting Docker's unordered mount
array changes no object or permission; the initial order-sensitive guard
refused before any reservation or start. The CHOWN-only public-file helper exits
zero, PID zero, without an OOM kill. All twelve protected states stay unchanged.
Host access to the operator's network inode is denied; no privilege is added.
The graph must verify its own actual inode through the trusted operator.

The [fresh attempt](../../evidence/runtime/postgres-stage2-2026-11-10/fresh-graph-start.json)
starts once at 16:47:03 UTC, container `ee9d5a67`, under the maintained server
supervisor. Its three new writable volumes and empty ownership registry exclude
the failed attempt's registry, spill and checker volumes. Only this new job's
three owner/comment-bound databases and scratch may be cleaned. C696 and failed
copy 69 remain excluded. Startup verifies dev UUID `2f58b861`, authorization,
actual network inode and process birth before database work.

At 16:50 this job's writer has copied 4.2 GB of its current table; by 16:59 its
owned database reports 17,254,343 inserted rows and COPY advances to a different
relation. These are copy observations, not verified semantic units. Attendance
uses this actor's IP and owned databases; aggregate scratch-cluster counters
cannot dismiss a stall. The supervisor checks protected states, hold, disk and
kernel OOM counters every twenty seconds, with an unchanged 24-hour bound and
120-second stop grace. At the latest sample all twelve protected states remain
unchanged, no OOM kill is observed and over 555 GB remains free.

## Final guard-source full validation

The [completed full suite](../../evidence/runtime/postgres-stage2-2026-11-10/developer-guard-full-passed.json)
passes 4,646 tests, zero failures, errors or skips, in 3,756.83 seconds. Its two
warnings are the existing record-property/xunit2 warnings. All 935 input hashes
and source `94c8cb77` remain unchanged; no test owner remains active. The graph
started while this validation was pending under the explicit 544-unchanged-file
bridge; this later result completes the current full proof. It does not change
the actual 231.14-second core timing failure.

## Source lock failure and later fetch at 16:10 UTC

The source's 16:00 acquire tick fails with `OperationalError: database is locked`,
one write-lock timeout, no request and no progress. The frozen code is unchanged.
The 16:43 read-only check then finds `run_20261110T161020Z`: five successful
requests and five checked pages, 3,980 bytes from EEPro and 256,991 from
scoring.dance, five progress events, no error, stage error or paused host.
The later success does not erase the failed tick. Maintenance item 37 owns the
lock behavior; items 30, 39 and 40 keep the state-size, expired-host and idle
findings. The 629 withheld partitions remain a separate maintenance finding.

## Input acceptance exceeds its atomic-write budget

The fresh graph starts once at 16:47 UTC and exits naturally at 17:11 UTC,
with exit 1, PID zero and no OOM kill. Its frozen copy finishes, then normal
candidate input acceptance exceeds the unchanged 60-second atomic-write
budget. No graph receipt or shard proof is produced. Approved cleanup removes
this job’s new copies and empties its registry. The independent boundary finds
exactly the default databases, C696 OID 16396 and retained69 OID 465992, with
no sessions; it does not repeat the base’s typed audit. The guard and all
twelve protected container states are unchanged
([receipt](../../evidence/runtime/postgres-stage2-2026-11-10/graph-input-accept-deadline.json)).

The measured failure triggers bounded batching in normal queue invalidation
and scope registration. A real Postgres regression measures 12,310 SQL calls
and 4.271 seconds for three overlapping invalidations of 4,097 units; registering
4,097 scopes takes 4,111 calls and 1.048 seconds. The initial registration
probe has an invalid test timestamp; its corrected run passes before any
implementation change. The first batched run reduces those counts to 31 and
19 calls, taking 2.218 and 0.410 seconds. Eighty-eight focused cases pass.
These local measurements do not establish a full-size server pass.

Repeated invalidations advance work generations and the history fence. Each
batch therefore ends before a repeated key; no invalidation is discarded.
Callbacks still observe earlier accepted digests and complete queues, and a
later failure rolls back the entire acceptance. Registration keeps its first
timestamp and reports only inserted rows. The same 60-second budget applies.
Per-input start and queued logs distinguish unfinished invalidations from
committed acceptance; queued output is not a commit receipt.

The proposed controlled core probe for source 94c8cb77 is canceled without
execution or cache changes because this necessary fix changes the source.
The earlier 4,646-case full pass remains evidence for the preceding source.
Its completed pytest-3282 tree is independently reviewed, renamed with exact
inode checks and removed without chmod or regular-file writes. Cleanup frees
31,869,349,888 bytes; raw full results remain outside the tree
([cleanup](../../evidence/runtime/postgres-stage2-2026-11-10/completed-full-3282-fixture-cleanup.json)).

## Source collection idle at 17:34 UTC

The latest acquire tick, 17:31 UTC, finishes without error but fetches no pages.
The last real fetch finishes at 16:11 UTC: five scoring.dance/EEPro requests.
About 82 minutes have passed without fetching. The existing 16:00 SQLite
write-lock failure remains a failure; later successful ticks do not erase it.
The latest watchdog lines still report state size, paused remote upload,
629 withheld partitions and the expired Wayback failure. This observation
fires the unattended-work idle signal. It does not establish the cause or
justify a production repair during the isolated migration rehearsal.
Maintenance item 40 owns the empty ticks
([read-only pulse](../../evidence/runtime/postgres-stage2-2026-11-10/source-idle-1734.json)).

## Batched input acceptance validation

The final focused run passes 88 cases. Its overlapping invalidation uses
31 SQL calls in 2.242 seconds; registration uses 19 in 0.420 seconds. The
new history-fence expectation initially omitted secondary increments from
work generations and accepted digests. The corrected regression checks all
24,597 increments, unchanged callback visibility, complete duplicate work
generations and rollback. The independent reviewer adds a correction without
changing the earlier failed result.

The normal check then passes all 3,075 core cases, Ruff and mypy across 365
source files, but takes 209.04 seconds. The unchanged 180-second timing gate
remains open. The package source is 94a24e6c; all 935 bound inputs have hash
bc222965. Only queue and scope registration source and their two existing
test files differ from the preceding full pass. No full pass is claimed for
this source
([checks](../../evidence/runtime/postgres-stage2-2026-11-10/developer-batched-input-acceptance.json)).

The new AMD64 image builds once, is transferred to Rhuidean and passes exact
installed-byte checks for 546 package files and ten config/override files.
Its proof container is created but never started. Image configuration 2fd1e17e
and OCI manifest 6b49b7f9 bind the current source
([image](../../evidence/runtime/postgres-stage2-2026-11-10/image-batched-input-acceptance.json)).

A new full run waits on scratch space. The independently reviewed cleanup
removes four exact local transport archives, each with an unchanged,
byte-identical server copy. It recovers 998,301,696 bytes and leaves
116,733,825,024 bytes free, below the 118,111,600,640-byte full-run admission.
No fixture, reference, profile, cache, image, container or volume is removed.
The owner is asked to free four GiB; the 80 GiB floor and full admission stay
unchanged
([cleanup](../../evidence/runtime/postgres-stage2-2026-11-10/local-duplicate-image-archives-cleanup.json)).

Healthchecks.io’s watchdog description is corrected to the actual 300-second
application interval. Its existing 300-second period, 120-second grace,
identity, email integration, paused status and ping count remain unchanged;
no ping is sent. The API key and private URLs stay server-only. Guarded
application delivery and the missing-heartbeat drill remain open.

## Image-only guard transition for the batching fix

The first concrete plan for the batching image is refused before start: its
new image 2fd1e17e does not match the current guard’s 2cc3f9d1 pin. The frozen
plan and refusal remain unchanged, and the verifier remains created with PID
zero. No data job ran under the mismatched policy.

A separately reviewed transition changes only network.json’s image_id. The
one-file, networkless CHOWN helper exits zero; the old guard is deliberately
stopped once with exit 143 and PID zero. The new root-owned policy is installed
by exact atomic rename and directory fsync. The guard code and inode remain
unchanged. The new namespace’s complete Config, HostConfig and four mounts
match the prior profile, allowing only its generated hostname and the enabled
OOM default representation
([preflight](../../evidence/runtime/postgres-stage2-2026-11-10/image-pin-transition-preflight.json)).

The replacement guard starts at 18:08 UTC with ID e6ef7463, PID 2307333 and
birth 973055595 on the same host boot. Its independent post-check confirms
policy db505e0a, canonical digest 62f01071, unchanged security code 8d69abde,
fresh resource status without observation error and all twelve protected
container states unchanged. Incomplete application inventory is expected while
schedulers remain unstarted. This transition starts no graph and proves no
application health
([post-check](../../evidence/runtime/postgres-stage2-2026-11-10/image-pin-transition-postcheck.json)).

The current guard pointer is updated only after that post-check. A distinct
corrected graph plan binds the actual new guard and matching image policy;
it needs a new independent execution verdict. Current full validation and
the 180-second core timing gate remain open. The hold receives its ordinary
18:10 review, preserving owner, reason and creation time, with review due
19:10 UTC.

## Approved build-cache cleanup and new full run

The owner asks for safe deletion candidates, then explicitly approves one
reviewed inactive Xcode build-intermediates directory. It is about 27 days old,
with no active build, open reference, link, mounted subtree, immutable flag or
foreign owner. Fresh guards match the independent check before ordinary
same-parent rename and exact quarantine removal. No source package, build
product or index sibling changes. No chmod or file-content rewrite is used.
The measured free-space increase is 4,687,261,696 bytes; actual available
space reaches 122,609,491,968 bytes. Exact unrelated-project paths and commands
remain in private scratch
([cleanup](../../evidence/runtime/postgres-stage2-2026-11-10/approved-build-cache-cleanup.json)).

At 18:25 UTC the current source’s full suite starts once through `mise run test
-- --full-suite`. All 935 bound inputs still match bc222965, package source
94a24e6c. Its 20-second disk watch preserves the 80 GiB floor. This is a running
check, not a full pass; the core timing gate remains open
([start](../../evidence/runtime/postgres-stage2-2026-11-10/developer-batched-full-start.json)).

## Batched graph starts under the matching image policy

The corrected plan 95e8b90f receives a distinct execution verdict. The frozen
4a1b plan and its refusal remain unchanged. The maintained supervisor starts
once at 18:32 UTC; actor e79eee46 starts at 18:32:54 UTC, PID 2401195 and birth 973201320. Its authorization matches the actual network namespace, host boot
and process incarnation, and precedes credential loading. The startup identity
is the expected private dev database C696, UUID 2f58b861. The new image is
2fd1e17e under guard e6ef7463 and policy db505e0a
([start](../../evidence/runtime/postgres-stage2-2026-11-10/batched-graph-start.json)).

The independent startup check binds only new owner token
21ee0a208fbc4a87821d7ab8. Its registry contains public ownership metadata,
not DSNs. All twelve protected containers, the native import result, current
root files and hold are unchanged. IPv4 permits the private database, bounded
proxy, private resolver, loopback and established flows before rejecting other
traffic; IPv6 permits loopback and established flows before rejection. No OOM
kill is observed. These checks prove startup and ownership, not a graph pass
([ownership](../../evidence/runtime/postgres-stage2-2026-11-10/batched-graph-startup-ownership.json)).

Progress attendance is scoped to that token and the current actor’s address;
retained69’s activity does not count. The COPY stream advances through tables
while its byte/tuple counters reset per table. Database insertion statistics
include internal work and are not the typed-row audit. The full suite runs in
parallel on unchanged source; its result and the core timing gate remain open.

The later Healthchecks transport draft adds only the exact proxy origin
https://hc-ping.com. The existing CoreDNS configuration remains unchanged:
workers use the proxy for external HTTPS, and the proxy resolves CONNECT
hosts. Private worker DNS intentionally resolves only internal hosts. No proxy
or DNS change is applied during guarded verification or inspection. Actual
application alert delivery and the withheld-heartbeat proof remain open.

### Batched acceptance still exceeds sixty seconds

At 18:57 UTC on November 10 the batched graph exits 1 naturally, with no OOM.
Its frozen copy completes, but normal input acceptance exceeds the unchanged
shared sixty-second transaction budget. The source checkpoint and retained
failed copy remain protected; ownership cleanup removes the new copy. No graph
receipt exists. The independent boundary check is retained
([failure](../../evidence/runtime/postgres-stage2-2026-11-10/batched-graph-acceptance-deadline.json)).

The per-input logs show scoringdance.round taking 39.538 seconds. The last
observed wsdc_registry.dancer input has only 18.451 seconds left. These timings
include watch updates, snapshot selection and work enqueueing; they do not
isolate the slow phase. Read-only source counts are 5,710 watches and 7,069
snapshots for scoringdance.round, and 29,305 watches and 33,023 snapshots for
wsdc_registry.dancer. Only zero and twenty-two matching watches, respectively,
already have a NULL extract version. Skipping those writes would change trigger
and history-fence behavior and is not selected.

The freeze path omits an explicit ANALYZE after bulk loading; import and restore
already perform it after committing DDL and grants. Missing statistics are a
hypothesis, not an established cause. One new owned full copy will measure the
exact acceptance order before and after ANALYZE, keeping the shared sixty-second
limit and rolling back each trial. Execution completion and materialization
will be timed separately. No retained database will be analyzed or changed.

At 19:25 UTC all 4,648 full-suite cases pass with zero failures, errors or skips,
on the unchanged 935-input source binding. The core correctness result remains
3,075 passing cases in 209.04 seconds, above its unchanged 180-second gate
([full validation](../../evidence/runtime/postgres-stage2-2026-11-10/developer-batched-full-passed.json)).

The source pulse at 18:57 still shows empty acquire ticks; its last real fetch
finished at 16:11. No production repair was deployed
([pulse](../../evidence/runtime/postgres-stage2-2026-11-10/source-idle-1857.json)).
The rehearsal hold was reviewed at 19:45, with its next review at 20:45 UTC;
its owner, original creation time and reason remain unchanged. Schedulers remain
unstarted.

### Completed full-suite fixture cleanup

The independent review identifies pytest-3293 as the completed 4,648-case run;
pytest-current points at a later empty root. After fresh ownership, process,
mount, open-file, ledger and lock checks, only that exact generated fixture tree
is renamed and unlinked. No file contents or permissions are changed. The
external logs and JUnit report retain their exact hashes. Actual free space rises
by 31,876,349,952 bytes to 124,097,069,056 bytes, above the 110 GiB admission
floor for another full run. Other roots, reference R, physical profiles, caches
and databases are unchanged
([cleanup](../../evidence/runtime/postgres-stage2-2026-11-10/completed-full-3293-fixture-cleanup.json)).

The two Healthchecks runtime endpoint files are staged server-only, UID 10001,
mode 0400 and single-linked. The project management key is not copied; this
step changes no application mount or proxy and sends no ping
([staging](../../evidence/runtime/postgres-stage2-2026-11-10/healthchecks-runtime-endpoint-staging.json)).

### Rollback-only acceptance profile starts at 1959 UTC

The independently reviewed diagnostic starts once at 19:59 UTC with the
already-tested immutable image. Startup checks confirm authorization before
credential loading, the exact private dev source, actual process incarnation,
and a new owner with one database claim. Fresh spill, runtime and checker
volumes exclude every older registry. The source state mount is read-only.
Only the newly owned database may be analyzed and removed. The attended
supervisor retains the twelve protected services, current namespace, root
policy files, unexpired hold, OOM checks and 200 GB free-space floor. The
actor has 8 GiB, six CPUs, 256 processes and a 3,500-second internal ceiling;
the supervisor bounds it at 3,600 seconds plus its existing stop grace.
The normal acceptance transaction retains its sixty-second limit
([startup](../../evidence/runtime/postgres-stage2-2026-11-10/acceptance-profile-startup.json)).

The diagnostic buffers at most 10,000 measurement events during each trial,
then flushes after rollback. It records watch-update completion, snapshot
selection, affected-unit materialization, enqueue batches and digest writes.
EXPLAIN uses the exact application predicates for updates and snapshot
semijoins. Selected mutation tables receive before/after rollback fingerprints.
Baseline fingerprint reads warm data; the second trial shares caches and
aborted tuples. Unexpected exceptions stop the experiment; only the expected
write deadline or deliberate rollback permits its next phase. Completion
proves a diagnostic finished, not a graph pass or a causal speedup.

The source correction developed while this old-image diagnostic runs adds
ANALYZE after freeze's final catalog/ACL transaction and before publishing
settings. Three targeted regressions and all 35 graph/copy-area cases pass.
They assert an idle transaction, destination ownership, actual column
statistics, unchanged source/copy semantics and failure cleanup without a
success settings file. New core and full validation are pending; the earlier
4,648-pass report covers the preceding batched version.

### Source collection pulse at 2015 UTC

At 20:15 UTC, the 20:10 acquire tick finishes without an error but makes no
request. The last real fetch still finishes at 16:11, 244 minutes earlier.
The bounded scan skips no large file and retains the earlier SQLite lock
failure. ALERTS.log adds a 19:05 request-budget guard alarm. No production
state or code is changed; the maintenance queue retains the empty-tick work
([pulse](../../evidence/runtime/postgres-stage2-2026-11-10/source-idle-2015.json)).

### Explicit statistics do not resolve the acceptance deadline

The disposable freeze completes at 20:22:37 UTC. Both rollback-only normal
acceptance trials reach the sixty-second limit. All selected mutation-table
fingerprints match before and after each trial, including the same digest
across trials. ANALYZE completes in 49.4977 seconds; it is insufficient to make
acceptance fit. Before ANALYZE the observed SQL totals are 18.9745 seconds for
watch updates, 37.5163 for enqueueing and 2.1361 for snapshot selection. After
it the totals are 33.7619, 24.7155 and 0.3896 seconds. Both prefixes stop during
wsdc_registry.dancer's watch update. The second prefix leaves that update more
time, not proof of a slower completed update. Baseline fingerprint reads warm
data, and caches and aborted tuples remain confounds.

The actor exits naturally at 20:25:57, status zero, without OOM. The independent
20:32 boundary check confirms its process, cgroup and supervisor are gone, the
new ownership registry is empty, and the exact defaults plus C696 and retained69
remain with their original OIDs and comments and no sessions. The twelve
protected services, native audit actor, namespace and root files match. No
acceptance or graph pass follows from diagnostic exit zero
([result](../../evidence/runtime/postgres-stage2-2026-11-10/acceptance-profile-result.json)).

The source readiness correction remains useful for explicit post-copy planner
statistics, but it is not presented as the deadline fix. Core and full checks
wait for a settled candidate. A separate synthetic singleton-counter experiment
will test whether the repeated row-trigger pattern has increasing cost before
any counter or trigger change is selected. Its scope is one fresh local bare
test database, generated tables and exact owned cleanup.

The hold is reviewed at 20:36 UTC; its owner, creation time and reason are
unchanged. Its next review is 21:36 UTC. The checkout session lock is refreshed
at 20:36 UTC as well.

### Healthchecks proxy origin applied

The reviewed proxy-only Compose recreation runs once at 20:37 UTC. Only the
public gateways input and Squid output change, adding `https://hc-ping.com`.
CoreDNS, the namespace policy and all internal addresses remain unchanged.
Both original anonymous proxy volumes retain their names, destinations and
read/write modes. The other eleven protected services remain exact, including
the seven unrelated applications. Compose removes the replaced proxy container;
it deletes no volume.

The adapter records a conservative post-check failure because its expected
replacement label was the old container ID. The installed Compose 5.0.0 writes
`proxy-1`; the earlier controller used 5.5.1. It also makes reused anonymous
volume mounts explicit and reports mount mode `z` on this host with SELinux
disabled. The original failure stays sealed. No deployment is repeated.
An independent read-only post-check validates those narrow bookkeeping
changes, the actual resource and security profile, exact volume mappings,
pinned image defaults and a TCP listener reached from the owned mock. The
same PID and birth remain stable after two seconds, without OOM or restart.
Native import status, root policy files, namespace, hold and retained private
catalog all match. Raw inspection credentials are neither retained nor hashed
([post-check](../../evidence/runtime/postgres-stage2-2026-11-10/healthchecks-proxy-postcheck.json)).

The current proxy is `298bc1f45189`; later job baselines and the ordinary hold
review protocol must use this observed container. Provider ping URLs are still
server-only. No application notification or liveness ping has yet been sent
by this proxy operation.

### Counter model and maintained acceptance profiler

The one local synthetic counter experiment completes in 1.4005 seconds. All
nine cases preserve the exact changed-row count and final counter value, then
roll back. At 8,192 rows, the row-trigger SQL takes 186.346 ms and splitting
it into 1,024-row updates takes 186.447 ms. The statement-count model takes
6.796 ms. The independent result confirms its one bare generated database is
gone, the owner registry is empty, the process group is absent and the original
local test service is unchanged. The result does not establish application
causality, semantic equivalence or an accepted trigger change
([result](../../evidence/runtime/postgres-stage2-2026-11-10/synthetic-counter-result.json)).

A separate bounded read-only source observation reports 444,178 estimated
execution-admission rows, 78,414 work-generation rows, 4,198 pending rows and
one history-dispatch fence row. Plain EXPLAIN selects the fence primary key.
The publication-guard SELECT uses the state index and returns no row in
0.801 ms on a cold read. It executes no UPDATE and does not reproduce a trigger
inside the failed frozen copy. Captured inputs and query plans remain in
`/tmp/swingset-stage2-postprofile-readonly-20261110/`.

The next diagnostic is the maintained `profile-input-acceptance` command,
with one owned copy, one normal acceptance and mandatory rollback. It records
statement categories without SQL parameters and PostgreSQL function self and
total times. Function tracking is session-local; acceptance runs with both
session and current user changed to the worker login and its capabilities
checked. No role grant or cluster setting changes. The source must be dev,
the external registry fresh, the capture path separate, and the output unused.
Twenty focused cases pass, including normal acceptance, an injected atomic
deadline, unexpected failure, real rollback after the measurement bound,
production-source refusal, connection isolation and parameter privacy.

The health entrypoint now waits for its namespace grant before reading
credentials, as the other daemons already do. Its new delayed-grant regression
fails on the old entrypoint and passes after the change; all 61 deployment and
health-area cases pass. These additions await broad validation and a matching
immutable image before server use. They change no running actor.

At 20:47 the ordinary hold review adopts only the independently verified new
proxy ID in its expected twelve-service snapshot. All other fields match.
The hold identity and reason remain unchanged; next review is 21:47 UTC.

The first broad check of the maintained profiler selects 3,086 core cases.
It finds one missing recipe-scope classification; mypy also finds a missing
worker-name type check. Both are corrected. The core takes 228.50 seconds,
so the unchanged 180-second timing gate remains failed. The later path and
area run passes all 91 cases, including legacy `.db` and `.sqlite` aliases
and admission before any capture or database work
([focused checks](../../evidence/runtime/postgres-stage2-2026-11-10/developer-input-profile-paths.json)).
Review then finds a usability gap when the normal scratch parent is an
ancestor of the checkpoint; that correction is pending, alongside promotion
of the one-use diagnostic supervisor into the maintained command.

At 21:39 the hold review confirms all twelve services unchanged and extends
the review to 22:39 UTC. At 21:41 a read-only production pulse finds one
successful EEPro fetch at 21:15, 29,738 bytes. The 21:40 tick makes no request.
The expired Wayback and earlier stale-fetch alarms remain recorded. No
production change was made
([pulse](../../evidence/runtime/postgres-stage2-2026-11-10/source-progress-2141.json)).

The corrected broad run passes all 3,106 selected cases, Ruff and mypy.
Its 234.44 seconds still exceeds 180; source binding is unchanged during
the run
([check](../../evidence/runtime/postgres-stage2-2026-11-10/developer-input-profile-core-correctness.json)).
The remaining scratch admission now allows a normal `/tmp` ancestor while
refusing scratch inside the retained source. Its real native success case
uses `/tmp` and leaves no new scratch directory.

The maintained supervisor now has a fixed `verification-job --input-profile`
mode: one owned copy, a 3,600-second ceiling, a separate diagnostic review
and a read-only retained state mount. It keeps the graph supervisor's exact
namespace, endpoint, protected-service, startup identity and one-start checks.
The final focused run passes 85 cases, including source-mount and command
refusal, review scope, a boolean copy-limit refusal and the bounded stop.
Final broad validation and matching image are pending.

The final source review accepts the maintained profiler and supervisor
([review](../../evidence/runtime/postgres-stage2-2026-11-10/input-profiler-source-review.json)).
All 3,114 core cases, Ruff and mypy pass on the unchanged binding. Its
219.95 seconds still fails the 180-second timing gate
([check](../../evidence/runtime/postgres-stage2-2026-11-10/developer-input-profile-final-core.json)).
The corrected passive sampler captures CPU and I/O counters. The earlier
sampler discarded its output because pressure files were absent; no cause
is inferred from that failed observation. The successful samples also do
not establish a root cause.

The AMD64 image contains all 557 exact public files, including 366 Python
modules. Its proof container remains created and never started
([installed bytes](../../evidence/runtime/postgres-stage2-2026-11-10/image-input-profiler-installed.json)).
A final full suite starts at 21:54 with the 110 GiB admission and 80 GiB
floor. Image-policy transition and profiler execution still need separate
review. Two initializers fail on permission ordering in the three new owned
volumes. Neither mounts a retained state, credentials or database; no
diagnostic actor is created and no active policy changes. Their failed
containers are preserved while the ordering is corrected.

The corrected initializer passes after a separate ordering review. It
reclaims the three fresh mount roots before traversal, sets modes while
root-owned, then transfers children before parents. A read-only helper
running as UID 10001 confirms all five paths are mode 0700, with only the
two expected empty spill directories. The diagnostic actor is created
and never started. Its image-only policy transition remains staged for
independent review; the active E6 guard and root policy are unchanged.
The transition adapter keeps full environment and command comparisons in
memory and records public namespace fields only.

The image-only transition completes once at 22:12. Its independent
post-check accepts the new namespace, exact full profile comparisons,
root policy and protected state. The old E6 guard exits 143 without OOM
and its process is absent; the profiler remains unstarted
([preflight](../../evidence/runtime/postgres-stage2-2026-11-10/input-profile-image-pin-preflight.json),
[post-check](../../evidence/runtime/postgres-stage2-2026-11-10/input-profile-image-pin-postcheck.json)).
The current pointer adopts only that observed guard. At 22:17 the hold
review confirms the same twelve-service snapshot and extends the review
to 23:17 UTC. The final maintained-profile plan is staged for a separate
execution and one-copy cleanup review.

The separate execution review accepts one maintained diagnostic and only
its new owned database cleanup. The corrected launcher compares all
eleven complete mount objects after sorting by destination; Docker had
reordered the same objects before launch. It starts the maintained host
supervisor once at 22:30:23 and the actor at 22:30:24. The supervisor verifies
the dev UUID, namespace grant, process birth and network namespace. At
22:31 the actor is freezing one copy, with no reported error or OOM
([startup](../../evidence/runtime/postgres-stage2-2026-11-10/input-profile-start.json)).
Acceptance and function timing remain pending.

At 22:29 the production pulse finds two successful scoring.dance requests
at 21:46, 408,179 bytes. The 22:26 tick makes no request and the checked
alert tail has no new line. Production is unchanged
([pulse](../../evidence/runtime/postgres-stage2-2026-11-10/source-progress-2229.json)).

The independent 22:33 startup observation confirms the actor's full
profile, both firewall families, authorization and exact process identity.
The private catalog adds only owner `8517b274abbe42429560e856` and its one
new database; retained C696/69 and their owner records are unchanged.
The observation is sealed in scratch, SHA256
`31b8e0898474d02cf0a549205bace20fae59119eff1bf330ab50c66671e122ae`.
It proves startup and freeze isolation, not diagnostic completion.

The maintained diagnostic finishes naturally at 22:55:52 with all 108,390
units, matching rollback fingerprints and removal of its sole owned
database. Normal acceptance still expires at 60.015 seconds. Four
history-timing functions account for 47.528 seconds of self time. The
independent terminal audit confirms exact retained state, empty registry
and no remaining actor or supervisor processes. It separately records
the namespace exit 1 and supervisor refusal: the guard tries to authorize
a stopped actor's PID 0 as a new incarnation
([result](../../evidence/runtime/postgres-stage2-2026-11-10/input-profile-result.json)).

The same diagnostic source passes all 4,687 full-suite cases in 3,588.19
seconds. Its binding is unchanged and minimum free space stays above
80 GiB
([full suite](../../evidence/runtime/postgres-stage2-2026-11-10/developer-input-profiler-full-passed.json)).
The independently reviewed completed `pytest-3310` scratch is removed with
ordinary unlink, reclaiming 31.95 GB; external full-suite proofs and other
roots remain unchanged
([cleanup](../../evidence/runtime/postgres-stage2-2026-11-10/completed-full-3310-fixture-cleanup.json)).
At 23:04 the ordinary hold review preserves the twelve-service snapshot
and extends the review to November 11 at 00:04 UTC.

The local guard fix retires only confirmed stopped or removed actors.
It preserves fatal live-profile and unknown-state failures, with 53
deployment-area cases passing. The measured native-39 candidate counts
transition rows for four history-fence statements and retains exact deltas.
Its upgrade test preserves all 141 tables, identity, function grants,
observer visibility and rollback, with publication row fencing intact.
Real native-38 profiler and two-process source replay pass through the
explicit read-only checksum gate. All 234 combined area cases and the
separate explicit forward-upgrade CLI case pass. Settled source review,
core/full validation and a new reviewed runtime/guard remain required.
No server schema or retained source was changed.

The first native-39 core check exposes a duplicate version constant: backup
manifests still declare 38 while the schema is 39. The failed fixtures fill
one client scratch cap; this incomplete run is not a timing pass
([failure](../../evidence/runtime/postgres-stage2-2026-11-10/developer-native39-core-backup-failure.json)).
The version now has one canonical source. The exact AFTER/event trigger
audit and its wrong-event regression are also fixed. All 94 schema,
backup and publication cases pass after both corrections. At 23:50 the
hold review preserves the twelve-service snapshot and extends to
November 11 at 00:50 UTC. At 23:43 production has fetched nothing since
21:46; the 23:05 Wayback alert repeats an expired deadline. No production
repair is made
([pulse](../../evidence/runtime/postgres-stage2-2026-11-10/source-idle-2343.json)).

The corrected native-39 source passes independent source review and all
3,130 core cases, Ruff and mypy. Its unchanged-budget run takes 185.48
seconds, so the 180-second gate remains open
([core](../../evidence/runtime/postgres-stage2-2026-11-10/developer-native39-corrected-core.json)).
A new AMD64 image matches all 558 installed public files. Current full
validation starts at 23:55 with more than 110 GiB free and an 80 GiB
stop floor. Its packet is `/tmp/swingset-stage2-native39-full-20261110`;
no source edits occur during the bound run. A new cold verifier and
three fresh scratch volumes are prepared; no business actor is started.
The failed guard is retained pending a separate code-and-image transition
review. Shared native-38 data and the retained C696/69 copies are unchanged.

The separately reviewed transition installs the fixed root security source
and a policy differing only in its runtime image. A new namespace starts
once at November 11 00:03:41. The failed previous guard is retained
without stop or restart. Independent observed post-check accepts both
files, full operator profile, process identity, fresh status, protected
services, retained catalog, source files and native import. The platform
pointer adopts only that observed guard; no business actor is started
([preflight](../../evidence/runtime/postgres-stage2-2026-11-10/native39-guard-preflight.json),
[post-check](../../evidence/runtime/postgres-stage2-2026-11-10/native39-guard-postcheck.json)).
The fresh maintained diagnostic remains cold pending its own execution
and one-owned-copy cleanup review.

A separate independent preflight accepts one new maintained diagnostic
and cleanup only of its own new database. The supervisor starts once at
00:15:58, followed by the actor at 00:15:58.841 UTC. At 00:16 it is freezing
the owned native-39 copy through the verified read-only native-38 source
path, with one ownership record and no reported error or OOM. Acceptance,
cleanup and the healthy-guard terminal check remain pending
([startup](../../evidence/runtime/postgres-stage2-2026-11-10/native39-input-profile-start.json)).

Independent 00:17:57 startup observation verifies the actor birth, exact
namespace grant, both firewall families and first OUTPUT jump, full
profile and eleven mounts, protected services and retained catalog. The
only added database belongs to owner `e29bd58a71c14532aeb0bc66`. The
startup verdict is sealed in scratch, SHA256
`e44458b167b156ddcebafa5dc72d2acfd461bb7aa8796bf648662861747fdaf3`.
It proves startup and freeze isolation; target validation, acceptance and
cleanup still await the terminal receipt.

At 00:21, production collection has resumed after UTC midnight: the
00:15–00:19 tick fetches 58 points.worldsdc.com pages, 776,313 bytes, with no
reported error, failed flag or paused host. Its stopped flag follows a
248-second run with a configured four-minute budget, consistent with
normal deadline completion; this does not prove which stop cause fired.
The service reports success. A new 00:05 derive-idle alert says no progress
since 16:25; maintenance item 42 owns that investigation. No production
repair is made
([pulse](../../evidence/runtime/postgres-stage2-2026-11-10/source-progress-0021.json)).

Read-only analysis of the existing corrected core report decomposes its
185.439 seconds into a 3.343-second prologue, 178.067-second critical client
and 4.029 seconds of other runner time. Available counters do not establish
a safe performance correction. The report remains a timing failure; no
extra timing run, scheduling change or cache adjustment is selected while
the current full run and diagnostic are active. The scratch assessment
is sealed, SHA256
`eef22413b83219f53544e0f1ea626b784101768971d77ef3bf17185f6a4869df`.

At 00:34:59 the ordinary hold review confirms the same twelve protected
services and extends the review to 01:34:59 UTC. Its identity and reason
are unchanged; the running diagnostic sees the renewed out-of-band hold.
The root file is sealed, SHA256
`e3f0868dd70cf4563e22cd11f50b7726671350b9dfcb823a2e1f54b6f87579d2`.

The native-39 diagnostic exits naturally at 00:40:55, and its supervisor
completes successfully with a healthy guard. The independent terminal audit
confirms no owned database, registry, cgroup or child processes remain and
all protected state is unchanged. Acceptance still expires at 60.023 seconds
with the same rollback fingerprint. Its work-generation history function
now accounts for 41.526 self-seconds across 30,629 statements, nested under
27,268 row-level enqueue callbacks. The plan selects a native-40 batch
follow-up; the current full run stays bound to native-39 source
([result](../../evidence/runtime/postgres-stage2-2026-11-10/native39-input-profile-result.json)).

The bound native-39 full suite finishes at 00:55:44 with all 4,703 cases
passing, no failures/errors/skips, unchanged source binding and minimum
free space above 80 GiB. Its 3,643.82-second pytest run is complete before
any native-40 source edit. Core and acceptance timing remain failures
([full suite](../../evidence/runtime/postgres-stage2-2026-11-10/developer-native39-full-passed.json)).

The native-40 candidate batches the two pending-work callbacks and keeps
separate transition aliases for pending rows and history counts. All three
38→39, 39→40 and 38→40 worker upgrade comparisons pass, including all 141
table contents, retry fields, generation defaults, missing-generation
updates, exact fence deltas, observer visibility, commit and publication
refusal. The initial watch wrapper reports EPERM on shutdown after its three
cases pass; that failure is kept separate from the test result and is under
read-only investigation. Ordinary pytest pruning removes completed full
scratch `pytest-3326`; external full XML and log hashes remain unchanged.
The prepared manual cleanup adapter is never executed.

### Native watch shutdown permission error

A separate read-only audit finds the old watch PIDs and groups gone, both
local lifecycle locks available, no live test ledger owner and no leftover
client database. The exact EPERM syscall is unconfirmed; `_stop` process-group
signaling is the likely source. No cleanup or source fix is performed. The
subsequent ordinary native test command passes all 164 selected native-40
cases and exits zero. Maintenance item 43 owns the watch shutdown finding.
The independent scratch audit is sealed, SHA256
`a4e0e5740272f09a726bbc250f984e5cee01ec250e74fb2de6d907bedbfb4d39`
([native-40 area](../../evidence/runtime/postgres-stage2-2026-11-10/developer-native40-area.json)).

The settled native-40 source passes all 164 selected area cases and all
3,141 core cases, Ruff and mypy. Its normal core run takes 204.06 seconds,
so timing still fails; no extra timing retry is selected
([area](../../evidence/runtime/postgres-stage2-2026-11-10/developer-native40-area.json),
[core](../../evidence/runtime/postgres-stage2-2026-11-10/developer-native40-core.json)).
The new AMD64 image is built from that binding. Its bound full suite starts
at 01:26:21 with 119.124 GB free, above the 110 GiB admission threshold,
and keeps the 80 GiB stop floor. The 01:25 hold review preserves all twelve
protected services and extends review to 02:25 UTC; root hold SHA256 is
`998600ecf60853ebba8c0aa704d6e76b1e170a3d163462ab0e2624fc7d53178d`.

At 01:38, the latest completed production cycle fetches 57 registry pages,
752,767 bytes, at 01:30–01:34, with no failed flag, error or paused host.
The 01:35 Wayback alert repeats the expired November 6 failure already
owned by maintenance item 39. No production repair is made
([pulse](../../evidence/runtime/postgres-stage2-2026-11-10/source-progress-0138.json)).

The source-reviewed native-40 image matches all 559 installed public files
([image](../../evidence/runtime/postgres-stage2-2026-11-10/image-native40-installed.json)).
Its separately reviewed image-only transition stops healthy 6ebbc once
and starts the equivalent 1a4f guard. The failed older guards remain
unchanged; independent observed post-check accepts the protected boundary
and the platform pointer adopts only the observed guard
([preflight](../../evidence/runtime/postgres-stage2-2026-11-10/native40-image-pin-preflight.json),
[post-check](../../evidence/runtime/postgres-stage2-2026-11-10/native40-image-pin-postcheck.json)).

A separate execution review authorizes one fresh native-40 diagnostic and
cleanup only of its owned copy. The supervisor starts at 01:46:23.851 and
the actor at 01:46:24.620 UTC. At 01:47 the actor is freezing its one owned
copy with no reported error or OOM. Acceptance and terminal inspection
remain pending
([startup](../../evidence/runtime/postgres-stage2-2026-11-10/native40-input-profile-start.json)).

The independent native-40 startup observation verifies actor PID 4016982,
its birth and boot identity, exact namespace grant, both firewall families
and the first OUTPUT jump. Full profile and all eleven mounts match, with
source and three private SQL files read-only. The only added database is
`sst_copy_35969bfc62314211b4e4c63a_0`; retained C696/69 and protected state
remain exact. The sealed startup verdict has SHA256
`64e8b3d3d72f07adf832e3bf61d1f030f39f870f68f4173a6301830f8e4ea19d`.
At 01:49 the phase remains freeze; readiness and acceptance are not claimed.

The native-40 diagnostic finishes naturally at 02:13:03 with acceptance
complete in 58.9197 seconds under the unchanged sixty-second budget. All
108,390 units have the expected `d58f3f45…` digest, rollback fingerprints
match and owned cleanup completes. The supervisor also succeeds with 79
samples and no stop attempt. Independent terminal review remains pending.
At 02:14:37 the ordinary hold review preserves the twelve-service snapshot
and extends to 03:14:37 UTC; root hold SHA256 is
`e352ec60ff50d3b83d3caca9e5938ae5e108f47a5ecf076bfeb98311a0330e91`.

Independent terminal audit accepts the native-40 diagnostic and retained
boundary. Acceptance has 1.0803 seconds of headroom; the guard remains
healthy, and the owned database, registry, cgroup and child groups are
absent. Source, private catalog and all protected services are unchanged.
This single complete trial is not a whole-graph proof or a causal timing
comparison with earlier aborted trials. Full validation and core timing
remain open
([result](../../evidence/runtime/postgres-stage2-2026-11-10/native40-input-profile-result.json)).

### Native-40 full validation and developer call profile

The unchanged native-40 binding finishes its full suite at 02:27:25 UTC:
4,714 pass, no failures/errors/skips, in 3,662.27 pytest seconds. The
minimum observed free space is 89,591,783,424 bytes, above the 80 GiB floor
([full](../../evidence/runtime/postgres-stage2-2026-11-10/developer-native40-full-passed.json)).

The selected native call profile passes its one existing scoring.dance
end-to-end case, leaving source unchanged. The call takes 22.53 seconds.
Its connection wrapper executes 89,897 calls and accounts for 14.79
cumulative seconds; psycopg wait has 10.74 seconds of self time. The largest
direct callers are derivation storage and projection writes, followed by
signature and recipe reads. This locates many database round trips, not a
proven cache or scheduling fix. The native-host profile is not a Linux core
timing comparison; the 180-second core gate remains failed and no source
optimization or default-core timing retry is selected
([profile](../../evidence/runtime/postgres-stage2-2026-11-10/developer-core-call-profile.json)).

The first cold whole-graph profile is refused before start because its
filesystem state was read-only. The maintained graph captures current
inputs there. A new cold profile copies only metadata and input files into
a fresh private writable state volume, internally compares private values
without printing or hashing them, and keeps retained checkpoint SQL
read-only. Its separate graph execution review is pending. The refused
actor remains unstarted; neither attempt changes the retained base.

### Corrected whole-graph startup

The separate execution review accepts plan `9b942f75…` and only its three
new owned databases. Review SHA256 is
`6f4d9b8641383116d3c7c01ef55171ad615695e3b5908c95e24b34d4fe20812c`.
The exact one-use launcher starts the maintained supervisor at 02:57:15 and
actor `3f48e55…` at 02:57:16 UTC, PID 175667. The first observation has
one ownership record, no error, no OOM and no restart. Independent startup
inspection and the complete graph result remain pending. No authoritative
migration, re-key, publication or retained-base cleanup is authorized by
this start
([startup](../../evidence/runtime/postgres-stage2-2026-11-10/native40-whole-graph-start.json)).

The independent startup review accepts actual PID/birth/boot/network inode,
root grant, both firewall families and first OUTPUT jumps. Full configuration
and eleven mounts match in memory; protected services, root files and the
retained C696/69 identities remain exact. Only owner `190daa522f4c40e3827d6495`
has a new database. Startup verdict SHA256 is
`7df7c4728263bd029f0d96d55713f9d883a0693f8a83ba1df31671a8ab90df4c`.
At 03:00 the owned COPY has processed 5.37 GB and 162,000 tuples; its
database is 8.06 GB. These are in-progress counters, not graph acceptance.

The 03:02:31 hold renewal preserves all twelve protected services and extends
review to 04:02:31 UTC. Root hold SHA256 is
`5b3279c240fdc947e6e2c8081faaebac003cff7fd927972a3fe87c077369910a`.
A static review accepts the post-graph reader draft and its explicit
read-only native-38 compatibility check. Its execution remains gated on the
actual complete graph receipt, terminal inspection and a fresh SQL-only
reader perimeter. No reader, migration or re-key is started by this draft.

### Source idle alarm at 03:08 UTC

The read-only pulse finds no new acquire request for 3,652.99 seconds. The
last actual fetch is 02:05–02:07: 35 registry pages and 346,006 bytes. Later
ticks complete empty, without failed/error/stage errors or paused hosts.
The required sixty-minute alarm is raised under existing maintenance item 40. The previous Wayback and derive-idle findings remain items 39/42.
Production is unchanged
([pulse](../../evidence/runtime/postgres-stage2-2026-11-10/source-idle-0308.json)).

At 03:10:35 UTC, a fresh read-only pass verifies all 59 retained reference R
files, 381,686,344 bytes, against their original hashes and file sizes. The
verification manifest still hashes to
`2fc7d83fb37a07796d368ebd4b48ec08471a573dd5a144e1c044531371c1512f`.
No reference file, receipt or physical workspace is changed. B/C/R comparison
still awaits the two new releases.

### Whole graph fails before its first shard

The actor exits 1 naturally at 03:29:31 UTC, PID zero, no OOM or restart.
Its incomplete receipt has the exact 108,390/D58 unit set and current
accepted context, but zero results and zero completed clones. The receipt
is persisted at 03:29:17.255; Postgres cancels a statement at 03:29:27.540.
The public error is scratch SQLSTATE 57014. Code order and this ten-second
interval strongly indicate the first owned template clone timed out; the
SQL text was deliberately not logged, so this attribution is an inference.

Independent terminal review accepts all owned cleanup and the retained
boundary: no owner190daa database, registry, process group or cgroup remains;
C696/69, protected services, guard, root files and hold remain exact. The
review SHA256 is
`76df9d775f2951cb769641522844aa9bbeb1ceb55b78fceb0eb21aa50db8a829`.
The graph remains unproved. Its candidate checkpoint `5039effb…` also
differs from the source preseal `00c831a4…` after input acceptance. This
supersedes the draft inspector’s equality assumption; explain and bind the
transition before another run, without dropping the retained-source check
([failure](../../evidence/runtime/postgres-stage2-2026-11-10/native40-whole-graph-failed.json)).

### Clone and checkpoint follow-up

The clone fix retains empty creation at ten seconds, permits 600 seconds
only for owned physical templates, and adds a separate ten-second lock
limit. The real scaled timeout regression and all 16 copy cases pass.
The new checkpoint module preserves ordinary hash bytes and predicts exact
watch invalidation plus NULL-scope registration without writes. Four real
acceptance cases and the graph-receipt/tampering case pass; all 72 focused
copy, graph and input-profile cases pass in 43.55 seconds. Mypy passes the
three changed source modules. Current core/full validation and the actual
retained-source projection remain pending; no new image or graph is started.

The 03:47:34 hold renewal preserves the twelve-service snapshot and extends
review to 04:47:34 UTC. Root hold SHA256 is
`783241db31c9dac953ccc4a75c273b2af03fcac8d08e669b7622eb7af1121a84`.
The repository session lock is refreshed at 03:43:47 UTC.

The first normal follow-up check passes all 3,147 core cases in 192.79 seconds
but fails import-order lint and timing. After import sorting, a second run
exposes the missing offline-module scope exclusion and a 30 ms year-body
test alarm that can instead interrupt libpq admission. The scope exclusion
now names its reason. That test delivers real SIGALRM only after entering
the year body; other real-time deadline tests and runtime bounds remain
unchanged. All 142 focused cases pass. The required normal check on the
corrected exact binding passes all 3,147 cases, Ruff and mypy for 367 source
files. Its 182.89 seconds still fails timing; earlier failures remain
recorded. The bound full suite starts at 04:20:46 with the 110 GiB admission
and 80 GiB stop floor. No source edit follows that start
([validation](../../evidence/runtime/postgres-stage2-2026-11-10/developer-checkpoint-binding-validation.json)).

The 04:15:48 hold review preserves the twelve protected services and extends
to 05:15:48 UTC; root hold SHA256 is
`33db4709e26398de7b6cf2c314bc9ce9ccc2f7666c628204fc8cb7f156129561`.
The guarded read-only projection observer starts once at 04:21:59, supervised
by PID 613656. Exact pending exec, watcher and actual SQL6-only namespace
are independently accepted. No database write, graph replay or credential
export is performed by this diagnostic; result and terminal review remain
pending. Its retained-source and candidate hashes must both match their pins.

### Source-to-candidate checkpoint change explained

The read-only diagnostic completes in 318.987 seconds. It reproduces exact
fdb capture, raw source `00c831…` and candidate `5039effb…`. Only watches
change: all 36,066 rows remain, and the exact extraction-version projection
explains the complete hash. No NULL scopes are added; the other 32 of 33
checkpoint components are unchanged. The original observer exits zero and
the twenty-second watcher succeeds without a guard failure or OOM.

Docker file copy does not collect the tmpfs result. A separate recorded
bounded read-back exec reads only that existing result with stable-file
checks and exits zero; no SQL or diagnostic is replayed. Its body matches
the original observer’s SHA, `e58a83b0…`, 19,435 bytes. Server and local
outputs and directories are fsynced. Independent terminal review accepts
the proof and authorizes only a controlled stop of the inert reader.
The stop ends at 04:39:30 with PID zero, exit 137 and no OOM; this deliberate
sleep-process stop is separate from observer success. Guard and protected
services remain exact after the stop. The graph remains unproved
([result](../../evidence/runtime/postgres-stage2-2026-11-10/accepted-checkpoint-projection.json)).

### Recipe query validation

The 04:49:50 interruption ends the previous full run with 1,156 passes,
zero failures/errors and actual exit 130. Its source binding is unchanged
and the process group is gone. This is an incomplete validation, preserved
in the interruption receipt; no completed full proof is claimed.

The measured recipe lookup now combines global audit/code presence and
selected pipeline inputs in one SQL snapshot. Four parameterized regressions
check empty inputs, audit-only presence, an unselected case-insensitive code
scope, legacy input selection, selected values among unrelated inputs, and
missing-scope refusal. Their red run observes the old two statements. The
first implementation run fails on an unescaped percent in parameterized SQL;
after escaping it, all 36 recipe/retention-input cases pass in 3.58 seconds.
This proves behavior and statement count, not a core timing improvement.
Normal core and full validation on the changed source remain required.

The 04:58:53 hold review preserves the twelve protected services and extends
review to 05:58:53 UTC. Root hold SHA256 is
`5f00c61b5fa18ce261b2283be21f32aa53d95318ad79ee2490c52cd4665e2674`.

### Source idle and backup pulse at 05:01 UTC

At 04:59:23 UTC, acquire has made no request for 4,077.77 seconds
(68.0 minutes). Its last actual fetch at 03:50–03:51 checks two scoring.dance
pages, 408,181 bytes. Later ticks finish empty without errors or paused hosts.
The required idle alarm is raised under existing maintenance item 40;
Wayback and derive-idle findings remain items 39/42. Production is unchanged.

The 04:00 backup succeeds at 04:09:28, service result success and exit zero.
Its new local set `set_20261111T040000Z` names schema 37, 49 segments and
zero missing items. Manifest SHA256 is
`71a77325b5acd8e245cf0ebd2a22f583fc0e15e7e81b0e6bd29444335a83463c`.
The next timer is November 12 at 04:00 UTC. This checks a local backup;
it does not prove a new restore or remote upload.

### Remaining core timing and streamed writes

Both recipe-query core runs pass all 3,147 cases. The first takes 180.58
seconds and fails Ruff's unused loop-variable rule. After making scope
presence explicit, Ruff and mypy pass; core still takes 181.90 seconds.
Both source bindings remain fixed, and neither timing failure is waived
([validation](../../evidence/runtime/postgres-stage2-2026-11-10/developer-recipe-query-validation.json)).

Existing profile evidence identifies `_store` as the largest connection
caller: 8,120 calls and 1.750 cumulative seconds in one end-to-end case.
The four latest core clients finish within 173.6–175.8 seconds, so the
remaining gate is broad work rather than one long case. The plan selects
one SQL statement for each interned payload/reference pair, preserving
streamed consumption and the enclosing transaction. The strengthened
streaming regression first fails with 400 statements for 200 rows.
Candidate focused validation remains pending; no new full/image/graph runs.

The revised post-graph inspector receives independent static acceptance,
SHA256 `07e55125abcfb1a6514949bf42f8d72428e77050d63f23e871d057170cd9eec3`.
It retains source/preseal equality, reconstructs the entire maintained
acceptance binding at the graph clock, and checks the candidate hash,
inputs, audit and versions. This approves the draft only, with no execution.

All 55 focused payload, read-set and publication-control cases pass in
165.66 seconds. The streaming regression now observes 200 write statements
for 200 rows, with the first reference still written after the first row.
Existing duplicate-payload, rollback, label and publication-fence coverage
passes. Formatting and targeted Ruff pass. The required normal core check
starts on this changed binding; no timing improvement is claimed yet.

The first streamed-write core run ends with 3,146 passes and one failure,
186.006 seconds. The build-completion SQL observer only recognized a leading
reference INSERT; it misses the same INSERT inside the combined statement.
Both observers now match the inner INSERT while retaining their original
ordering and no-write assertions. All nine build-completion cases pass in
28.18 seconds. The complete required normal check follows formatting on the
new test binding; the earlier failed run is preserved in scratch.

The corrected streamed-write binding passes all 3,147 core cases, Ruff
and mypy but still misses timing at 181.14 seconds. Its unchanged-binding
summary and log remain in scratch. The next measured target is scope
ownership: the original boundary fixture issues 1,025 claims and advances
the history fence by 5,128. Bounded batches reduce those claims to two while
preserving that exact fence delta. All 63 projection/materialization cases
pass in 10.18 seconds, including unchanged rows, shared ownership, rollback
and deletion. The next normal check follows formatting on this changed source.

The independently checked completed pytest3357 scratch is removed under
both validation locks after the preceding core ends. Exact root/tree and
no-reference checks pass; ordinary same-parent rename and unlink both exit
zero. Actual free space rises by 2,017,841,152 bytes to 119,799,144,448.
Sibling roots, the current link, external XML/log, databases and caches are
unchanged. Both locks are released before testing resumes
([cleanup](../../evidence/runtime/postgres-stage2-2026-11-10/completed-focused3357-fixture-cleanup.json)).

### Source pulse at 05:25 UTC

The 05:25:30 pulse still finds no request since 03:51:25, about 94 minutes.
The source watchdog independently logs acquire idle at 05:05:09. The sampled
history also exposes failed run `run_20261111T032014Z`: 03:20:14–03:22:01,
`OperationalError: database is locked`, one write-lock timeout and zero
requests. The later 03:50 fetch succeeds; it does not erase that failure.
Maintenance item 37 owns lock behavior; item 40 retains the collection-idle
finding. No production repair or deploy is performed.

### Current core passes the development budget

The ownership-claim batch binding passes all 3,148 core cases in 174.80
seconds. Ruff and mypy for 367 source files pass; the complete binding
remains unchanged. Earlier failures remain recorded, and no caps, timeouts,
test selection or placement rules change. This is a passing normal core
check, not a controlled causal timing comparison. The current full suite
starts at 05:28 UTC with the 110 GiB admission and 80 GiB stop floor. Source
and tests remain fixed for that run; graph proof is still pending.

### Current runtime prepared without activation

The AMD64 image binds source `d6bee16f…` and passes all 560 installed public
file hashes, including 367 Python files. Config digest is `ba677233…` and
OCI manifest `e8d04c54…`; the archive is 248,811,008 bytes. The source-proof
container remains unstarted. A fresh graph actor `ec9747e6…` and four new
writable volumes are prepared but unstarted; its private metadata comes
from the exact frozen read-only e79 source, including 1,689 input files and
three server-only SQL key files. No database is created by preparation.

The image-only policy transition is prepared with an unstarted sealing
helper. Its executor requires the completed current full-suite receipt,
exact source binding and a matching final independent verdict before any
transfer or server mutation. Static readiness review accepts the seven
changed public source files and the transition logic, with execution denied
while full validation is pending. Review SHA256 is
`3ab6ec2d92b702e80518dd7844fb75c246b286fc1afe310b80838df2b904a15c`.

The 05:44:13 hold review preserves all twelve protected services and extends
review to 06:44:13 UTC. Root hold SHA256 is
`a37af60b2134288ea5bcb6a7b07d3bc4309e4d1b40cec2c07ee851e114326739`.
The later migration-plus-audit helper is only a draft, unexecuted and
unreviewed. It uses the maintained typed encoder around the maintained
migration command. Graph completion, exact import hashes and a separate
execution verdict remain prerequisites.

### Later migration audit draft reviewed

Static review finds and closes a same-host owner-target gap before any
execution: all worker/control/owner DSNs and both required state settings
now bind the same database, UUID, OID, locale and exact restricted roles.
The helper checks the three SQL filenames before loading secrets and pins
the target migration checksum before calling the maintained command.
Because admission recovery commits before DDL, it writes a durable attempt
marker first and records unknown mutation outcome on any failed call or
unparseable result. The full post-audit and exact admission normalization
share one read-only snapshot; recovered nonpublication rows must have one
common bounded timestamp. Eleven offline encoder checks pass, including nine
negative cases, with no database access. The corrected helper receives
static acceptance, SHA256
`b573819b1dbfba8b65cdadfebe5f2daeb466501789c2d38807b9cebf3f085b32`.
It remains unexecuted. Actual graph proof, a concrete bounded SQL-only
operator envelope, fresh target checks and an execution verdict are still
required; this review grants no database mutation.

### Source pulse at 06:09 UTC

The 06:09:39 read-only pulse still finds the 03:50–03:51 scoring.dance fetch
as the newest request: about 138 minutes idle. There is no newer failed
acquire tick; the earlier 03:20 lock failure remains recorded. At 06:05:01
the watchdog repeats the expired Wayback `ServerError x7` warning with its
November 6 expiry. Maintenance items 37/39/40 retain these findings. The
backup and production deployment are unchanged.

### Current full suite passes

At 06:26:41 UTC, the current full suite finishes naturally with actual exit
zero: 4,725 pass, zero failures/errors/skips, 3,497.484 seconds. Source
`d6bee16f…` and complete binding `096a6a5b…` remain unchanged. The sampled
minimum is 87,993,528,320 free bytes; after completion it is 87,908,605,952,
both above the 80 GiB floor. Core, Ruff, mypy and current full gates now
pass ([validation](../../evidence/runtime/postgres-stage2-2026-11-10/developer-clone-checkpoint-roundtrip-passed.json)).

The read-only obsolete-archive review identifies four old archives with
exact retained server byte copies. The full run finishes above its floor,
so no archive deletion is authorized or performed. The prepared image-only
transition now binds actual full validation SHA256
`31e43d4a6653944e3d5c2096469867d1e4e2ce09824a68e4b1530167e12734be`.
Its final dynamic preflight and execution verdict remain pending; no guard
transition, graph or shared-import migration has started.

### Current image-only transition accepted

The final preflight accepts the current full proof and exact image-only
plan. The executor seals the new public policy, stops the healthy old guard
once, installs only `image_id`, and starts one fresh guard at 06:36:52 UTC.
The old guard ends deliberately with exit 143, PID zero and no OOM. All
twelve protected services, retained databases and native import remain
unchanged. The new guard `1ddfa37a…`, PID 1272374 and birth 977545098, has
the exact expected configuration, host settings and four mounts. Root policy
SHA256 is `fd2d2146…`, canonical `f07b64f5…`; security bytes remain exact.

Independent post-check SHA256
`225e94824537f0130a81c7ea573e01189fa67b50bdb136f2479bd3cedc91abe3`
authorizes adoption of only that exact new guard pointer. The parent adopts
it after the verdict. The metadata-only verifier permission helper exits
zero, opening no credential contents and changing no files. The verifier
remains created and unstarted; the final whole-graph execution preflight is
pending ([post-check](../../evidence/runtime/postgres-stage2-2026-11-10/clone-checkpoint-image-pin-postcheck.json)).

The 06:41:09 ordinary hold review preserves the protected services and
extends review to 07:41:09 UTC. Root hold SHA256 is
`21f5509f5fa65459fcbd233ce818029ada84e62ba1f6f0f8494b96944b796f62`.
The independent post-check verifies that renewed hold and the new guard.

### Current whole graph is running

Fresh independent preflight accepts one graph launch and cleanup of at most
three new registry/comment-bound databases; C696/69 cleanup remains forbidden.
The execution verdict SHA256 is
`ee09a766a48b115e050ad84b031ebaf54050f5f5fbfa8ff6a7e12c4d04e7095c`.
A first local invocation stops on a staged-package import shadow before any
remote transfer/reservation. The parent verifies no remote reservation and
that the actor is still unstarted, then uses Python safe-path mode to import
the tested checkout. Launcher bytes are unchanged; only one remote launch
occurs, at 07:00:53 UTC.

The maintained supervisor is PID/group 1389092, birth 977689165. Actor
`ec9747e6…` starts once at 07:00:53.954 UTC, PID 1389577, birth 977689249,
network namespace 4026533551. Independent startup review accepts actual
firewall/first-OUTPUT rules, authorization, UID, caps, limits, complete
configuration/host/mount conformance and protected boundary. Review SHA256
is `38c28c9a69a3cff75429d12208594f534e0a3afdab7c94ea2ae8748e7ead1881`.

New owner token `b0ebb1c1dbd24a639d93dfa5` owns only its database suffix 0.
Logical freeze is active; no shard result or complete graph proof exists.
The retained databases, shared import and schedulers remain unchanged.
The parent continues supervision and renews the hold before its review time.

### Hold and graph progress at 07:26 UTC

The hold was reviewed at 07:26 UTC and renewed through 08:26 UTC. All twelve protected containers remain unchanged. The current verifier is running without a supervisor error or OOM; its first owned copy reached 39,960,966,835 bytes and no COPY remained active. This is progress, not a completed graph proof. The source binding still matches all 940 full-suite inputs.

A legacy local `.venv` interpreter path disappeared after validation. Monitoring now uses Mise Python with an explicit source path when needed; no project environment, source, lockfile or completed result was changed.

### Completed full-suite scratch cleanup

The independently reviewed exact `pytest-3361` root was quarantined and unlinked under both validation locks at 07:30 UTC. Both commands exited zero; target and quarantine are absent. Free space increased by 32,164,048,896 bytes to 121,475,850,240 bytes. External full-suite proof, sibling roots and current link remain unchanged. Both locks are released. No database, server object, personal cache or permissions changed. The scratch execution receipt is `/tmp/swingset-stage2-completed-full3361-cleanup-20261111/cleanup-result.json`, SHA `ae4eb1cd9a5e8022ff80552c41293f556f9593ddecfec238e9dc8d80f286cde9`.

### Physical clone reaches the 600-second limit

The current actor ends naturally at 07:47:46 UTC, exit one, PID zero, no OOM or restart. Its supervisor confirms terminal state at 07:47:54, with no stop attempt. Input acceptance and ordinary checkpoint hashing pass: source `00c831a4…` maps to candidate `5039effb…`, with all 108,390 expected units and unit digest D58. The unchanged initial receipt is 4,375,764 bytes, SHA `3044916632517111595dfbe989cdf13e9501f65f7d12f998cc5b0d4f2bc77aea`; it has no clone methods or unit results and is neither complete nor clean. This is not a graph proof.

At 07:47:04, the first `CREATE DATABASE` backend has run for 561 seconds and is waiting on data-file I/O. Earlier observations also show WAL writes. The actor reports SQLSTATE 57014. Minimum observed free server space is 517,709,287,424 bytes and all observed OOM counters remain zero. The selected 600-second physical-copy allowance is insufficient for this default copy strategy. The independent terminal ownership and cleanup check is required before any new operation.

PostgreSQL 17 documents that default `WAL_LOG` writes each copied block into WAL. `FILE_COPY` reduces WAL for large templates but forces checkpoints before and after creation ([official documentation](https://www.postgresql.org/docs/17/sql-createdatabase.html)). No new clone operation or deadline increase has been selected. The read-only postgraph and authoritative migration drafts remain unexecuted.

The database log independently confirms backend 39882 cancellation at 07:47:43.509 UTC. The second agent accepts the failed terminal boundary: owned registry and databases are gone, source C696 and retained 69 catalog identities remain, no scratch clients or actor/supervisor groups remain, and protected services, root policy, guard and current hold are unchanged. Server free space returns to about 580.75 GB. This accepts cleanup, not graph success. See the [compact measurement receipt](../../evidence/runtime/postgres-stage2-2026-11-10/physical-clone-600s-failed.json).

### FILE_COPY candidate validation

Source `702328d6…`, binding `81dd552b…`, passes all 16 Postgres copy cases and the normal 3,148-case core in 178.50 seconds, plus Ruff and mypy over 367 source files. The second agent accepts the single public source-file change and D-0289. The full suite starts at 07:58:32 UTC on the fixed 940-input binding, using Mise Python 3.12.16 in a new scratch environment; the broken legacy environment remains untouched. Its 110 GiB admission and 80 GiB stop floor remain unchanged.

Candidate config `d746c7f0…`, OCI manifest `3c182315…`, passes all 560 installed public-file hashes; only `postgres_copy.py` differs from the previous image. The source-proof container remains created with PID zero. The guard remains on the previous image; the new transition is only a draft and requires actual current full success and fresh independent preflight.

### Source idle pulse at 08:06 UTC

The 08:05:43 UTC production pulse finds the newest tick at 08:05:03–08:05:17, with no error, requests or progress. The last actual request remains the 03:50–03:51 scoring.dance fetch: two pages and 408,181 bytes, about 254.3 minutes before this pulse. The 07:35:01 ALERTS line repeats acquire-idle. Maintenance item 40 remains open. The older 03:20 database-lock failure remains visible, with one write-lock timeout (item 37); no newer failed tick is observed. No production repair is made. The bounded pulse is in scratch at `/tmp/swingset-stage2-source-pulse-20261111-0806.json`.

The rehearsal hold is reviewed at 08:09:56 UTC and renewed through 09:09:56 UTC. Owner and reason are unchanged; all twelve protected containers pass. The staged public policy and CHOWN-only sealing helper are prepared, but the helper remains unstarted and the active policy/guard remain unchanged. Preparation-only verdict SHA `7701c4b79d4aba333f621a970cd6796cef193f3855c65e065b16ad116a5d9343` binds this step.

The fresh FILE_COPY verifier preparation is independently accepted at 08:21 UTC. Four new `72140b0c03f4` volumes hold an exact internal copy of the retained metadata, 1,689 input files (69,548,306 bytes), three SQL files and the read-only blob link. The verifier `80137f45…` remains created with PID zero. Its separately reviewed UID-10001, no-capability, no-network permissions probe exits zero and reads no credential contents. The proof has SHA `b7537731…`; its fresh reservation and transport bind the new nonce and image. The full-suite and image-transition gates remain open. No SQL, clone, migration or verifier start follows from preparation.

### FILE_COPY full-suite pass

At 08:55:46 UTC the current source completes all 4,725 full-suite cases, zero failures, errors or skips, exit zero, in 3,434.05 seconds (printed 57 minutes 13 seconds). All 940 bound inputs remain exact; the observed minimum free space is 89,029,599,232 bytes, above the 80 GiB floor. Four existing record_property/xunit2 warnings remain. The full proof SHA is `f27d52ead7d9d2addeea5caacff5bb18690ab5331987f2fdd494daa4f2a9f90f` ([compact validation](../../evidence/runtime/postgres-stage2-2026-11-10/developer-file-copy-passed.json)). The [candidate image](../../evidence/runtime/postgres-stage2-2026-11-10/image-file-copy-installed.json) remains inactive.

The hold is reviewed at 08:57:39 UTC and renewed through 09:57:39 UTC, with all twelve protected containers unchanged. Final transition preflight is requested against plan `e63a78f1…` and executor `ccad86d3…`; no seal start, guard stop or policy replacement has occurred. Whole-graph and shared-import migration proofs remain open.

### Source idle pulse at 09:06 UTC

The 09:06 UTC source pulse still finds no new request after 03:51 UTC. Empty ticks continue, and the older 03:20 lock failure remains in the bounded scan. The acquire-idle alarm and maintenance item 40 remain open; no production repair is made. The bounded input is `/tmp/swingset-stage2-source-pulse-20261111-0906.json`.

### FILE_COPY graph startup

The image-only policy transition completes at 09:04 UTC; independent postcheck `06788769…` accepts the exact new guard `417b8ad4…`. The prior guard ends deliberately with exit 143 and no OOM; its PID, process group and cgroup are gone. All twelve protected services, native import, retained catalog and public pins remain unchanged. The exact new guard pointer is adopted after that verdict ([preflight](../../evidence/runtime/postgres-stage2-2026-11-10/file-copy-image-pin-preflight.json), [postcheck](../../evidence/runtime/postgres-stage2-2026-11-10/file-copy-image-pin-postcheck.json)).

The fresh graph starts once at 09:18:39 UTC after independent execution review `b4e61455…`. Its actor `80137f45…` runs as UID 10001 with no capabilities, NNP and a private PID namespace; the complete configuration, eleven mounts, authorization, exact IPv4/IPv6 chains and first OUTPUT jumps pass independent actual-startup review `a8db4204…`. Egress allows the private SQL endpoint plus designated proxy/DNS routes, then rejects; this guarded actor is not the separate SQL-only postgraph reader.

New owner `d685bf2d7bf2413aaa024fbc` initially owns only its `_0` database. At 09:25 it holds 15,603,816,115 bytes and COPY FROM PIPE is active. No freeze, clone or unit-result completion is claimed. The three-new-database cleanup limit, retained C696/69 protections, 24-hour operation ceiling and resource caps are unchanged. Shared migration, re-key and publication remain unstarted. The session is refreshed at 09:26 UTC.

At 09:34 UTC the current owner’s first database holds 31,789,504,179 bytes and COPY FROM PIPE remains active. The hold is reviewed at 09:38:18 UTC and renewed through 10:38:18 UTC, with all twelve protected containers unchanged. The 09:39 observer still shows the same actor incarnation, one claimed database, no supervisor error and no OOM. No graph unit result is available yet.

### Completed FILE_COPY full-suite scratch cleanup

The exact completed `pytest-3368` root is independently reviewed and removed at 09:51 UTC. Fresh metadata and reference checks pass under both local validation locks; exact same-parent quarantine and ordinary unlink both exit zero. Both locks are released. Free space increases by 32,159,965,184 bytes to 121,155,272,704 bytes. External full-suite proofs, siblings 3371/3372/3373 and current link remain unchanged; source, profiles, reference R and active server work are untouched. The compact scratch execution result is `/tmp/swingset-stage2-completed-file-copy-full-cleanup-20261111/cleanup-result.json`, SHA `11d2a23192a0b76916810bb63ab505fa3bd6d8d2e946464017892243ad2291a3`.

At 09:56 UTC the current graph has its initial 4,375,764-byte receipt: all 108,390 expected units, source checkpoint `00c831a4…`, candidate checkpoint `5039effb…` and a strong binding. It has no unit results and is incomplete, not clean. The first physical CREATE DATABASE starts at 09:55:32.442 UTC. At 09:56:41 its elapsed query time is 69 seconds and PostgreSQL reports CopyFileWrite. The template holds 40,033,130,163 bytes. The 600-second statement and ten-second lock limits remain unchanged; clone completion is not yet claimed.

### FILE_COPY reaches the 600-second limit

The actor ends naturally at 10:05:36.883 UTC, exit one, PID zero, no OOM or restart. PostgreSQL logs cancellation of backend 42798 at 10:05:34.695; its physical CREATE DATABASE began at 09:55:32.442. The initial receipt remains incomplete with no clone methods or unit results: 4,375,764 bytes, SHA `96bd2859d9be3bb2264f52391955d1b2856892a7924a2da9a8dcec0133a6c96c`, 108,390 expected units and source `00c831a4…` bound to candidate `5039effb…`. The supervisor confirms natural terminal failure with no stop attempt, SHA `6ebf30e4…`.

Independent terminal review `c798d97f…` confirms empty owned registry and no owned databases, clients, processes or cgroup. Retained C696/69 catalog identities, native import, all twelve protected services and guard remain unchanged; authorization is retired. Server space returns to about 580.33 GB. This accepts the failure boundary, not graph success. The raw packet is `/tmp/swingset-stage2-file-copy-whole-graph-20261111`; no retained receipt is added beyond the current change's evidence budget.

The private PostgreSQL cgroup writes 31,892,938,752 bytes on device 8:0 over 481.882 seconds while this clone is active, about 66.18 MB/s. Reads total 29,642,420,224 bytes, about 61.51 MB/s. At the observed write rate, the 40,033,130,163-byte template alone takes about 605 seconds. Checkpoints and other overhead need additional time. This is not a measured successful clone time or proof of a specific host cause. D-0290 selects a finite 1,200-second physical clone ceiling; fresh validation and a new independently reviewed graph are required. No failed job is replayed.

### Source requests resume at 09:21 UTC

The 10:05:53 UTC source pulse finds actual requests at 09:21 (one eepro.com page, 29,738 bytes) and 09:56 (two scoring.dance pages, 408,181 bytes). The latest tick at 10:01:00–10:01:12 is empty with no error. The bounded scan starts at 04:10, so the previously observed 03:20 lock failure is outside this window; this does not erase it. Maintenance items 37/39/40 remain open. The scratch input is `/tmp/swingset-stage2-source-pulse-20261111-1006.json`. No production repair is made.

The rehearsal hold is reviewed at 10:13:46 UTC and renewed through 11:13:46 UTC. All twelve protected services remain unchanged; schedulers remain unstarted. Session `codex-stage2-resume` is refreshed at 10:13 UTC.

### Twenty-minute clone candidate misses the first core timing gate

The existing sixteen copy cases pass in 3.66 seconds, including real cancellation, source isolation, ownership refusal and cleanup. Independent source review `2e8227bc…` accepts the constant-only implementation and partial D-0287/D-0289 replacement. Source `41a0adf0…`, binding `0d6ab288…`, then passes every one of 3,148 core cases, Ruff and mypy, but takes 181.576 seconds against the unchanged 180-second gate. This is a failed normal check, not a validation pass. Its scratch result is `/tmp/swingset-stage2-clone-1200-check-20261111/validated.json`, job `cb604e8fc5dd4c17a5172c26`.

The prior source's core took 178.466 seconds. All four clients are close together in both reports; there is no demonstrated single-case bottleneck or regression from changing a scalar that the focused timeout test replaces. Membership, execution image and caps match. The ordinary runner's measured partition weights differ naturally and are preserved. The comparison is `/tmp/swingset-stage2-clone-1200-check-20261111/prior-current-timing-comparison.json`, SHA `67646a0ba7417c52bc37ab37cfc40e641f0be44f4c0c06d9cfb1ac35809cbf3f`. No weight, cache, resource or selection manipulation occurs. Full validation and image promotion remain unstarted pending a decision on this near-budget result.

### Confirmation exposes shared lock table exhaustion

The single permitted unchanged confirmation ends at 10:24:36 UTC, exit one. It takes 194.192 seconds and reports seven failed cases plus one error across all four clients, all observed with PostgreSQL SQLSTATE 53200. Its source binding remains exact. The PostgreSQL log independently records nine shared-memory exhaustion errors from 10:23:59 to 10:24:03, with the hint to increase max_locks_per_transaction. All observed cgroup OOM counters remain zero; the maximum sampled primary working memory is 1,160,470,528 bytes. The failed report remains `/tmp/swingset-stage2-clone-1200-confirm-check-20261111/validated.json`, job `6dfcb54d7f79447e9f680553`. No further unchanged timing retry follows.

The actual primary has max_locks_per_transaction 64, max_connections 100 and no prepared transactions. A separate normal native import case passes in 1.53 seconds while a scratch observer samples only its own backend's pg_locks after successful DDL. Its peak is 1,503 locks, including 1,486 outside the fast path. Sixteen simultaneous transactions of that size can require 23,776 entries before other connections, beyond the nominal 6,400 allocation. This is not a direct failed-run peak occupancy measurement or a timing improvement claim. PostgreSQL documents the shared-table sizing behavior and startup-only setting in its [version 17 lock management reference](https://www.postgresql.org/docs/17/runtime-config-locks.html).

The compact scratch measurement has SHA `a4744d2af8f635fae4d2d319eb95af23c17c3600fd80d737543b3367d08b4f4f`, at `/tmp/swingset-stage2-lock-table-measurement-20261111/measurement.json`. D-0291 selects 512 for the disposable test/CI primary within its existing memory cap. The exact old owned primary awaits independent idle/inventory review before the maintained reconciler replaces it. Physical lifecycle, persistent development and rehearsal server settings remain unchanged. Focused, core, full, image and runtime gates remain open.

### Disposable test lock capacity reconciled

Independent preflight `b3c149cf…` accepts the exact idle owned primary replacement through the maintained services.ensure_service(test) path. The guarded call runs once under core-executor.lock at 10:38 UTC; the reconciler acquires services.lock internally. Old container `1f9a123f…` is absent. New `4555d664…` reports max_locks_per_transaction 512 and retains the 2 GiB RAM cap, actual 4 GiB Docker memory-plus-swap setting, six CPUs and 1,536 MiB data tmpfs. Persistent development and durable physical containers remain exact. No credential value is emitted or hashed. Result SHA `5ac0a7d52a8147aedc6b0c5e10e2886f0cae11aaf587e1d4364cdcf38c8164a6`, at `/tmp/swingset-stage2-test-lock-capacity-reconcile-20261111/result.json`. Affected service, import and copy tests start next. This is local test capacity, not a production or rehearsal-server change.

All 86 affected service, import and Postgres copy cases pass in 18.34 seconds after reconciliation. The normal combined check begins at 10:39:11 UTC on source `385838ce…`, binding `8ceecb48…`, with all 940 inputs fixed. Both earlier failed core reports remain; no cache or measured-weight manipulation occurs. All memory/CPU, concurrency, selection and timing bounds remain unchanged.

### Bounded projection and certificate lookups

The test-capacity source passes all 3,148 core cases, Ruff and mypy but misses timing at 199.652 seconds, job `07b584826a464cf981ba9418`; no unchanged retry follows. Its current selected scoring.dance cProfile passes in 20.43 seconds, recording 77,651 SQL calls and 12.987 cumulative seconds in connection.execute. `_upsert` accounts for 8,616 calls/1.481 seconds and certificate token reads for 7,064 calls/0.838 seconds. The scratch measurement SHA is `340552c7a91430fdbde1dbe8b465f269eec8f72bb36e088436f3d7199c22b10d` at `/tmp/swingset-stage2-current-core-profile-20261111/measurement.json`. This is not a core speed comparison.

Projection now reads current rows for at most 1,024 desired keys at a time, retaining Python comparison and the original individual write order. All 39 projection/read-set/publication-fence/materialization cases pass in 117.53 seconds. The existing 1,025-row case verifies bounded reads, unchanged no-op behavior, the exact 5,128 trigger delta, shared ownership and rollback. An initial selector names a nonexistent test file and runs no cases; the corrected run is separate.

Certificate construction collects token positions and fills them from bounded 1,024-pair reads, preserving basis order, duplicate positions and zero defaults. The calendar/dancer fast path and group-selected generation overrides remain unchanged. All 72 certificate/currentness/equivalence cases pass in 20.84 seconds, including the new manual inventory digest and one-read assertion. A scratch comparison against the prior function passes 36 comparisons across eleven unit types, baseline/mutated/rollback states, SHA `b62aa10c4781cfb262f117ea47e37339ff60663143ff044d03154606a3d8676e`. Its first external test lacks the repository's autouse ownership fixtures and fails setup before opening a database; the corrected test imports those fixtures and passes.

Independent review `86794d97…` accepts both bounded lookups and their trigger/transaction assumptions. Source `3c7dd010…`, binding `c81f50ff…`, starts its normal check at 10:58:46 UTC. The added regression follows its file’s existing extended classification: core remains 3,148 cases and the full suite becomes 4,726. Full validation, installed image and new graph remain unstarted.

### Source idle pulse at 11:00 UTC

The 11:00:22 UTC production pulse finds the latest tick at 10:56:00–10:56:13, empty with no error. The last actual fetch remains 09:56:20, two scoring.dance pages and 408,181 bytes, about 64 minutes earlier. No newer failed tick is seen in this bounded scan; the older 03:20 lock failure is outside it, not erased. Maintenance item 40 remains open. No production repair is made. The scratch input is `/tmp/swingset-stage2-source-pulse-20261111-1100.json`.

The rehearsal hold is reviewed at 11:00:14 UTC and renewed through 12:00:14 UTC, with all twelve protected services unchanged. Schedulers remain unstarted.

The combined lookup source passes all 3,148 core cases in 177.919 seconds, within the unchanged timing gate, plus Ruff. Its normal check still exits one because mypy requires an explicit type for the token-version dictionary. The failed check is retained at `/tmp/swingset-stage2-read-lookups-check-20261111/validated.json`, job `b4594b14a0204da08886a0d2`. Add only that annotation, then run the required normal check on the resulting source binding; this is not an unchanged timing retry. No full or image run starts from the failed check.

The annotation-only source `409fecf5…`, binding `eae855be…`, passes all 3,148 core cases, Ruff and mypy but takes 181.582 seconds, so the normal check fails its unchanged timing gate. Its result remains `/tmp/swingset-stage2-read-lookups-final-check-20261111/validated.json`, job `c5633786e22d452e8217b196`. Independent final source review `625cc971…` accepts the type-only difference. A normalized executable comparison confirms the sole bound-file difference is that annotation and that code instructions/constants/names/exception tables match, ignoring debug positions; source bytes and inspect.getsource still differ. Measurement SHA `770009af…` is in the same scratch packet and does not replace validation or prove timing variance.

Independent direction `ac9eb10c…` accepts one unchanged normal confirmation, preserving both results, resources, selection and the ordinary measured-weight lifecycle, with no cache/reset/warmup/tuning. It starts at 11:11 UTC. If it also fails, no further timing retry follows without measurement and a new fix. No full or image run is started from the failed normal check.

### Batched canonical writes after the failed timing confirmation

The single unchanged confirmation passes all 3,148 core cases, Ruff and mypy but still takes 183.269 seconds, so it fails. Its result is `/tmp/swingset-stage2-read-lookups-confirm-check-20261111/validated.json`, job `839566590b2a49f2a5f1040c`. No further unchanged core retry follows. A current scoring.dance cProfile passes in 19.49 seconds, with 66,434 execute calls. Canonical inserts remain second at 2,879 calls and 0.580 cumulative seconds; scratch measurement SHA `012098b3118f67bc47fd0655f916b2924dc0c018fbb6e6b110a2928329996347`, `/tmp/swingset-stage2-current-writes-profile-20261111/measurement.json`. This is not a core timing comparison.

Changed canonical rows now use bounded multi-VALUES inserts within the already bounded 1,024-key groups. The original Python comparison, NULL/NaN handling and owner/seen preservation remain; unchanged groups send no canonical insert. Table order, native row triggers, writer lock and deadlines remain. The independent static audit finds no trigger blocker; AFTER-row scheduling moves to statement end, so operational registered_at times are not claimed identical.

The existing 1,025-row regression retains the exact 5,128 history delta and now checks two canonical writes as well as two bounded reads and no unchanged-row write. Its mixed 1,026-row phase compares first/last updates plus a final insertion against individual one-row statements inside a rolled-back reference transaction. Full canonical/ownership/history-fence/input-version/work-generation/pending-work/revision snapshots match. The initial focused run fails only because the later shared-ownership assertion reuses the pre-update event name; its armed subprocess repeats that test failure. The corrected test shares the current row. All 39 focused cases then pass in 113.71 seconds, including armed read-set, publication-fence and materialization coverage. Direct mypy over 367 files and formatting pass. The new normal check is next; no full or image is started yet.

### Scope pointer and staging-presence snapshot

The batched-write source passes every one of 3,148 core cases, Ruff and mypy but takes 182.246 seconds, job `60a62ecbac8c4594bb95e3f0`, source `856209d2…`, binding `40c510f3…`. This fails timing; it is not retried unchanged. The retained profile's unchanged currentness bodies separately issue a scope-pointer query, a visible-table check and a staged-row check. The follow-up reads pointer and presence together, using the exact existing pg_class/PG_TABLE_IS_VISIBLE predicate, then reads a staged row only when present. Public building behavior, proofs, restore guard and missing/old-schema refusal remain; no presence cache is added.

The new visibility regression initially assumes the temporary namespace comes first, but the managed role puts it last. Its corrected transaction-local search path puts the temporary shadow view first; currentness bypasses that view and rollback restores ordinary behavior. Application source is unchanged between those focused attempts. All 73 currentness/certificate/equivalence cases pass in 21.86 seconds. Direct mypy over 367 files and formatting pass. Independent source review `473f0322…` accepts the metadata consolidation. Its normal check starts at 11:37:34 UTC, with core membership still 3,148; the new extended case makes the full suite 4,727. Full/image/graph remain unstarted on this source.

### Bounded selected-generation reads

The pointer/presence source passes all 3,148 core cases, Ruff and mypy but misses timing at 183.167 seconds, job `b26af7660deb4ede8212e32c`, source `29bd8024…`, binding `e8939105…`. That failure remains and no unchanged retry follows. The retained profile measures the still-unchanged selected_generation helper at 4,166 calls/0.515 seconds. Certificate construction now gathers selected-pointer positions, performs bounded reads and fills each position with the same scalar/group rule. No cache is added; singleton requests retain the unchanged scalar lookup, group effective overrides preserve explicit NULL, and unknown units remain NULL.

The new extended boundary test compares the bulk helper to scalar behavior across ordinary, selected, effective, NULL and foreign-connection group contexts, including duplicate requests and 1,025 distinct units. All 74 currentness/certificate/equivalence cases pass in 23.19 seconds; direct mypy over 367 files and formatting pass. A fresh comparison against the original certificate implementation passes all 36 baseline/mutation/rollback comparisons over eleven kinds, SHA `a958f5516cda1850b3393366c0c790cb31c0501e9bd63f448517324941ece6ca`, `/tmp/swingset-stage2-selected-generation-equivalence-20261111/equivalence-result.json`.

Independent source review `186e2738…` accepts the selected-pointer change. The next fixed-source normal check begins at 11:45:25 UTC. Core membership stays 3,148; full membership becomes 4,728. No full, candidate image or new server actor has started from the latest failed checks.

### Combined normal check passes; full suite starts

The selected-generation application source passes core timing at 178.485 seconds, but that first normal check fails Ruff on the new extended test's loop callback. Binding its local reads list fixes the warning; application bytes are unchanged. Direct Ruff, mypy and the boundary/group case pass, and independent final addendum `f8f797e7…` accepts the test-only fix. The required final-binding check passes every case and static check but takes 180.300 seconds. It remains a timing failure.

Independent direction `2e3474f2…` permits one unchanged final-binding confirmation with no tuning or cache reset. That confirmation passes at 11:58:43 UTC: all 3,148 core cases in 179.054 seconds, Ruff and mypy, exit zero. Job `f73694d3e84043c9b025a440`, source `8aae03bf…`, complete 940-input binding `d45094e6…`; all inputs remain exact. The source is accepted for the required full check. This pass does not erase earlier failures or establish timing robustness.

The 4,728-case full suite starts at 11:59:06 UTC on that same binding, PID/group 67405. Initial free space is 119,751,311,360 bytes, above 110 GiB; the 80 GiB stop floor remains. Source stays fixed through completion. Candidate config `346f30a4…`, OCI manifest `5f9b83c5…`, is built from those inputs with an exact hash chain, linux/amd64, unchanged dependencies. Its 248,813,568-byte archive has SHA `29afe5a1fcde3eb0f802c29f2be398fbb0ee064d9aaa7a4b5fe3d93f92778606`. Installed-byte proof and all runtime operations remain pending.

The rehearsal hold is reviewed at 11:49:41 UTC and renewed through 12:49:41 UTC; all twelve protected services remain unchanged. The 11:50 production pulse still finds the last actual request at 09:56:20 UTC. Empty ticks continue with no newer failure in the bounded scan; the 11:05 alert repeats the old Wayback failure. Maintenance items remain open; no production repair is made. Scratch pulse: `/tmp/swingset-stage2-source-pulse-20261111-1150.json`.

### Current image and inert rehearsal preparation

The server's installed-byte proof passes all 560 public files, including 367 Python modules, SHA `d13c0e3f4688c6bbcc51a8339266487cda96a86d20cc9f441c3d5ca92180cd74`. Proof container `48f2972b…` remains created, PID zero. The full suite remains active on source `8aae03bf…` and binding `d45094e6…`.

Independent preparation-only reviews `bc3714d6…` and `44168a9d…` accept fresh verifier metadata and image-policy staging. New nonce `53eb8583ff2c` owns four new volumes. Its state-copy proof matches the immutable 1,689-file/69,548,306-byte input closure and internally copies exactly three SQL files, with no private values emitted or hashed. New verifier `62f14044…` remains created, PID zero; cold metadata SHA `9e9e9962…`. No SQL, copy, graph or migration starts.

The image-only new public policy has SHA `b4d8e22e…`, canonical `a4ebe3e9…`, and remains staged as owner 1000, mode 0600, one link, inode 17726198. Its CHOWN-only sealing helper `66800fe1…` is created and unstarted. Existing guard `417b8ad4…` and root policy/security remain unchanged. Full success and a fresh execution review are still required before sealing, guard replacement or graph launch.

Independent prepared-boundary/probe review `9d804d0e…` accepts only the fresh net-none permissions probe. It runs once as UID 10001, without capabilities, exits zero and opens private SQL files without reading their contents. Generic metadata proof SHA `b7537731…` matches earlier metadata, while this fresh reservation/transport binds nonce 53eb and image 346f. It neither starts the verifier nor changes files.

Later read-only postgraph and authoritative-migration drafts are cloned to current pins, with execution false and missing full/graph/postgraph proofs left NULL. Inspector `6d6f576f…` and migration helper `fedf56e8…` remain byte-identical. Independent static pin reviews `2544b7ef…` and `95503e5f…` accept their consistency only; no preparation or execution follows. Scratch packets use the `swingset-stage2-selected-generation-` prefix. Gate 2 remains open.

## Source idle pulse at 12:49 UTC

The read-only pulse at 2026-11-11 12:49:45 UTC finds the latest acquire tick
at 12:46:00–12:46:13 UTC empty, with no error. The last actual requests
remain the two scoring.dance requests at 09:56 UTC, about 173 minutes ago.
Maintenance item 40 remains open. The bounded scan finds no new failed tick;
it does not erase the earlier recorded SQLite write-lock failure. The
latest alert lines remain the 11:05 acquire-idle and stale Wayback warnings.
Scratch pulse SHA-256: `e4d41aeb5a52cbc13657c54039322de7ac9ad917f4637a50aa868663d964debb`.

The existing rehearsal hold was reviewed at 12:28:05 UTC, with next review
at 13:28:05 UTC; its owner, reason and unstarted schedulers are unchanged.
Hold SHA-256: `487fe229736a32b0de8122978321de5c9737606964a8528e807a38e3de2c38de`.

## Final selected-generation full suite passes

The fixed `8aae03bf` application source and `d45094e6` complete input binding
pass all 4,728 full-suite cases at 2026-11-11 12:54:54 UTC: actual exit zero,
zero failures, errors or skips, and no source-binding change. Wall time is
3,347.78 seconds; sampled minimum free space is 87,894,138,880 bytes, above
the 80 GiB floor, with no stop reason. Four pytest warnings remain. This
completes the current local validation alongside the 179.05-second core,
Ruff and mypy checks. Earlier timing and lock-table failures stay recorded.

Scratch validation SHA-256: `a1a796ef96dc9f99196a087a008ede2466dcef2b044d41e5df86522908e266d3`; JUnit SHA-256:
`222d693dcdcb1585b9fd04f2b2241e8af4a871748650d6fc2e7bd1b2a137dbfa`; log SHA-256:
`4c5a72322258623c19c9baef35a792dc74e6fc09ebf296f9a87d3d56d2e61c37`. The new image and graph remain unstarted
at this result. The image-only transition now has a final plan; execution
awaits its fresh independent preflight. Gate 2 remains open.

## Selected-generation image update completes

After full-suite success, the independent transition preflight accepts the
final plan and exact executor. The one-time operation completes with exit
zero: the seal completes, the exact old `417b8ad4` guard stops, and the
replacement `cbcb3ef8` guard starts at 2026-11-11 12:57:27 UTC. The public
policy changes only its runtime image pin to `346f30a4`; root security
bytes remain unchanged. All twelve protected containers are unchanged,
and the source-proof and graph containers remain unstarted. Independent
postcheck and pointer adoption are still pending at this result.

Scratch plan SHA-256: `29a9b404f380c813f60bb81285a988d88811083b85853dab10df865fb62d2fc6`;
preflight verdict: `d558ff6d4d2c269ed26499f470d8141f2b58307ede71f4a4f7814cb1cc46f98e`;
execution result: `bccab436a8bc06dfe1eb3be966b58d42ab7bb79f426a0566b9634b18b5d6a60a`.
This does not establish whole-graph equivalence or authorize a migration.

The independent observed-transition postcheck passes, bound to the final
plan, full-suite result and actual transition result. Its SHA-256 is
`a321b32c4637f71c02c5294950c605b3560bf42ec3d15529886cd849f307b32c`.
Only then was the current-platform pointer updated to the exact observed
`cbcb3ef8` identity. The whole-graph plan is finalized with SHA-256
`1d8086082a1c994ae2618806603d1f2d0b10efcf6b53d872a0a63b82c462c24a`;
its separate execution preflight is pending. The verifier remains unstarted.

## Selected-generation whole graph starts

After the accepted guard postcheck and final preflight, the exact fresh
`62f14044` verifier starts at 2026-11-11 13:02:51 UTC. Its maintained
supervisor acknowledges PID/group 3338705, birth 979860879, on the
unchanged server boot. The run covers all 108,390 units, two shards, with
no scopes or version bumps. Its reviewed cleanup applies only to its own
three new registry-bound databases. The retained C696/69 databases, source
import and shared native-38 database remain protected. Independent actual
startup review follows; this launch is not a graph result.

Scratch execution verdict SHA-256:
`5987995069fd033b603d9967f63759cfc1ee5484fde8ee11c924374cf28ae10b`;
launch acknowledgment SHA-256: `7aba2bd20d735261463a7106f3819df56fac4e6282cfbe7003eb92ffd50d4265`.

The independent startup review passes for actor PID 3338970, birth
979860966, network inode 4026533551, matching authorization and both IP
firewalls. It verifies the fresh owner token `a51a754add8443d2855045f7`,
with only its `_0` database claimed so far, and unchanged retained
boundaries. The logical freeze is active; there is no whole-graph result.
Review SHA-256: `efbdf93056a4b29744c46577aa3e0ef7f523fd56930286eb1c9f44348ce77dfa`.
The reviewer preserved two observer-only failures before the final passing
metadata capture; neither replayed or changed the business operation.

The existing hold is reviewed at 13:09:19 UTC, next review 14:09:19 UTC,
with unchanged owner, reason and creation time. All twelve protected apps
are unchanged. Hold SHA-256:
`ba22ba8049d65288a12b68dfe54b373ce855e016f226b081e4d673319f8a3150`.

## Fresh graph candidate binding and first clone

The initial receipt is present at 13:44 UTC. It reports the expected
108,390-unit D58 set, strong input-acceptance binding, raw source
`00c831a4` and candidate checkpoint `5039effb`, with no scopes, version
bumps, errors or unit results. This is an initial binding, not a clean
whole-graph result. The first owned physical clone starts at
2026-11-11 13:41:26 UTC; its backend is 47239 and its observed wait is
`CopyFileRead`. Preparation before that clone took about 38 minutes
35 seconds from the actor start, including the logical copy and candidate
acceptance; no separate copy-only duration is claimed. The reviewed
1,200-second statement and ten-second lock limits remain unchanged.

Scratch initial-field snapshot SHA-256: `523337b0b8d4291b1a5bf8d63b2b3c90f86b7a163492e12a45af5185e4a04263`;
clone observation SHA-256: `5583458932c4d0d260e1eb84914d2cb9b75b7cf74e12f55fe154334cb1a84953`.
The latest supervisor sample still reports all twelve protected apps
unchanged and no OOM event.

## Source idle pulse at 13:49 UTC

The read-only pulse at 2026-11-11 13:49:18 UTC finds the latest acquire
tick at 13:46:00–13:46:12 UTC empty, with no error. The last actual
requests remain the two scoring.dance requests at 09:56 UTC, about
233 minutes ago. The 13:35 UTC alerts report the same acquire-idle
condition and stale Wayback warning. Maintenance items 40 and 39 remain
open; this session deploys no production repair. The bounded scan finds
no new failed tick and does not erase the earlier SQLite write-lock
failure. Scratch pulse SHA-256:
`ffc465e5f2e4f9bc5c1e973af73414963566d9ba8e2ecedcc538e6ad3a9ea1e0`.

The existing rehearsal hold is reviewed at 13:49:30 UTC, next review
14:49:30 UTC. Owner, reason, creation time and unstarted pipeline
schedulers remain unchanged; all twelve protected apps are unchanged.
Hold SHA-256: `ced2bac7db115c49801bc07066d43ccf9ed88d9cff381d3cdd35b3a6c597d2d1`.

## First bounded physical clone passes

The 13:53:13 UTC owner-scoped catalog/activity snapshot finds completed
`a51a754add8443d2855045f7_0` and `_1` databases with exact owner comments,
and the second FILE_COPY clone into `_2` active on backend 47433. The
second starts at 13:52:01.503015 UTC. From the first start at
13:41:26.033138 UTC, the first clone and handoff take at most 635.47
seconds, about 10 minutes 35 seconds. That exceeds the old 600-second
limit and fits the reviewed 1,200-second limit. The separate SQL duration
is not available from this snapshot. No unit comparisons have completed
at this observation.

Scratch snapshot SHA-256: `11782c3164894f68536e5f4ca84bc34ecea2b67c297416bcc91dd30871a72e96`.
This is a successful first clone, not whole-graph acceptance.

## Both bounded clones pass and workers start

At 2026-11-11 14:03:16 UTC, all three new `a51a754add8443d2855045f7`
databases have the exact owner comment, and no CREATE DATABASE is active.
The second clone completed within 674.53 seconds of its 13:52:01 start,
including observation delay, below the 1,200-second limit. Docker process
metadata shows two active children, host PIDs 3700645 and 3700648, under
the unchanged verifier parent 3338970.

The 14:05 and 14:10 owner-scoped SQL samples show both shards connected
as `swingset_verify_worker` and advancing their own transaction/read
counters. Retained C696/69 activity is excluded from these samples. These
counters establish activity, not completed-unit counts or matches. The
14:10:54 supervisor sample reports no error or OOM, unchanged protected
apps and 459.75 GB minimum free space. The final graph receipt remains
pending; shared native-38 migration and re-key remain unstarted.

Scratch clone snapshot SHA-256: `9d74164611504574688e31c1c90d0dbc7a70123e7ba6c1956747759c0036d307`;
owner-progress snapshot SHA-256: `6b0ac082678468252f00bb3dfbec6fe663bd09fa6bc52fe79163eeba67f0e188`.

The existing rehearsal hold is reviewed at 14:29:42 UTC, next review
15:29:42 UTC, with stable owner, reason and creation time. All twelve
protected apps remain unchanged. Hold SHA-256:
`44fbda99cd6292ab7f09c2ee9cc0f375ab2720038fc9e7340fd8aa12b16d0f1e`.

A project-scoped provider GET at 14:37:26 UTC confirms exactly the two
expected checks, both with channels assigned. Alerts are up, with two
prior pings; the watchdog remains paused, with one prior ping and its
300-second period and 120-second grace intact. This sends no ping or
mutation. The key, private response and private URLs stay server-only
and are not hashed. A parallel guarded-health test remains a proposal,
not an executed application-delivery proof.

## Source idle pulse at 14:51 UTC

The read-only pulse at 2026-11-11 14:51:36 UTC finds the latest acquire
tick at 14:51:00–14:51:12 UTC empty, with no error. The last actual
requests remain the two scoring.dance requests at 09:56 UTC, about
295 minutes ago. No new failed tick appears in the bounded scan; the
earlier write-lock failure remains recorded. Maintenance items 40 and 39
remain open, and production code is unchanged. Scratch pulse SHA-256:
`8baba7c9af9a5be4eca4ff6bc4c8af007d3478360654964e42f5b200cdc9cb0b`.

The rehearsal hold is reviewed at 14:59:24 UTC, next review 15:59:24 UTC,
with stable owner, reason and creation time; protected apps are unchanged.
Hold SHA-256: `269daaa63462252f72ee8936ff85e0dcbb4113558f689bcd739297ef21876c21`.

The guarded-health proposal now has an exact cold container, `3eef417a`,
with PID zero and no start. It has read-only state, fresh monitoring
volumes and only a worker SQL file plus the two sealed notification files.
Preparation sent no ping and changed no pipeline data. Its bounded
normal-watchdog test controller is awaiting independent action review.
The graph workers remain active; neither application delivery nor Gate 2
is claimed by this preparation.

## Guarded health attempt fails its timing check

The independently reviewed health container starts once at 2026-11-11
15:14:31 UTC. Its normal watchdog sends the application alert and heartbeat
at 15:16:18; the provider records the alert down and the watchdog up, each
with exactly one additional ping. The report has no delivery failures, but
its timestamp precedes observation by about 106 seconds, exceeding the
test's 90-second cycle allowance. Its alert keys also report stale,
incomplete platform status. These are failed monitoring checks, not a
passing application monitoring gate. The withheld-heartbeat test is not
reached, and no new email confirmation is claimed.

The controller stops this exact health incarnation at 15:16:23 with exit
zero, PID zero and no OOM. It clears the owned event alarm and pauses the
owned watchdog; the provider confirms both cleanup states. All twelve
protected services remain unchanged, and the graph verifier continues.
The failed result, application report and controller remain in scratch at
`/tmp/swingset-stage2-guarded-health-20261111`. Do not replay the attempt or
relax the timing check. Inspect the normal platform observer and measure
the watchdog path before selecting another monitoring test.

The hold is reviewed at 15:25:42 UTC, with its next review at 16:25:42.
Owner, reason and original creation time are unchanged; protected services
remain unchanged. Hold SHA-256:
`ca9af323a65b7f6d233ac627b2325edd6ada05e0584702fdedab41cc4bcd801e`.

Independent terminal review accepts the stopped boundary and confirms
application delivery to the two intended checks. It leaves the monitoring
gate failed. The heartbeat stores cycle start, while the loop sleeps 300
seconds after completion; the next heartbeat's maximum age also includes
both cycles' work. The first-cycle allowance alone cannot prove steady
liveness. Platform status is read after database checks but compared with
cycle start, so a newer observer timestamp can also be rejected as stale.
The historical observer timestamp was not captured, so that mechanism is
not established as this attempt's cause. No threshold change or retry is
selected; inspect these clock semantics after the active graph completes.

## Source requests resume at 15:16 UTC

The read-only production pulse at 2026-11-11 15:36:43 UTC finds three
requesting ticks since the previous pulse: 70 requests at 15:16, 73 at
15:26 and one at 15:36. They fetch 9,952,597 bytes across scoring.dance,
eepro.com and worldsdc.com, with no failed tick in that interval. This is
observed collection progress, not just successful empty cycles. The earlier
write-lock failure and stale Wayback finding remain recorded; this session
performs no production repair. Scratch pulse SHA-256:
`89cee3c99de7c1648d7c0d90458b93f3439017068198ab640e3d023e0e43773f`.

## Graph progress at 15:58 UTC

The 2026-11-11 15:58:07 UTC supervisor sample finds the same graph actor
running, with advancing transaction/read counters, no reported error or
OOM and all twelve protected services unchanged. Unit results remain
pending; activity counters do not establish completed matches. The shared
native-38 database is still unmigrated and unrekeyed.

The existing hold is reviewed at 15:57:57 UTC, next review 16:57:57 UTC.
Owner, reason, creation time and unstarted schedulers are unchanged.
Hold SHA-256:
`f9341d23b131860c603f8926dc25c5d07d73e92971f1ec700e4e5a8273cd1c7b`.

## Completed full-suite scratch cleanup

Independent review finds the exact `pytest-3395` root belongs to the
completed 4,728-case run and has no active process, mount, container,
database or ledger references. Its metadata inventory and all guards
match again under both local validation locks. The operator quarantines
only that root, then unlinks it; both commands exit zero. External test
reports, neighboring roots and the current-root symlink remain unchanged.
No server or database object is removed.

Cleanup finishes at 2026-11-11 16:14:40 UTC. Free space rises from
88,912,310,272 to 121,155,936,256 bytes, an observed increase of about
30.0 GiB; APFS and concurrent system activity affect this measurement.
Both locks are released. The review, fresh admission, command results and
postchecks remain in scratch at
`/tmp/swingset-stage2-completed-full3395-cleanup-20261111`. This selects
no additional test run or application source change. The server graph
continues without a reported error.

## Source pulse and state-size alert at 16:28 UTC

The read-only production pulse at 2026-11-11 16:28:19 UTC finds seven
requests and 13,684,418 bytes fetched since the preceding pulse, with the
last request at 16:16:27 and no failed acquire tick in that interval.
The 16:05 watchdog reports state size of 77.8 GB against the 50 GB cap,
629 partitions withheld for `source_support_unavailable`, the known stale
Wayback warning and paused remote backup upload. The size is a watchdog
observation, not a fresh database-size audit or a proven cause of test
slowness. Item 41 already tracks the withheld partitions; add the size
finding to maintenance. No production repair or cleanup is performed.
Scratch pulse SHA-256:
`6008ec84a2b314fd4580c92b9d42c9bb59d7bd20b445012813a710a1122e4a4a`.

The rehearsal hold is reviewed at 16:31:12 UTC, next review 17:31:12 UTC.
Owner, reason, creation time and unstarted schedulers are unchanged; all
twelve protected services remain unchanged. Hold SHA-256:
`8f56bda4e09193f57c8c302ea9b8436b691b01722b57223a50e618ad0c270a08`.

## Isolated health clock repair

The separate jj workspace
`/private/tmp/swingset-stage2-health-clock-fix-20261111`, change `ypskmqrr`,
reproduces two clock errors offline: a platform snapshot published during
checks is falsely stale, and 102 seconds of work becomes a 402-second
start-to-start interval. Completion timestamps, fresh platform time and
monotonic remainder pacing pass all 70 focused health/deployment cases.
Independent source review accepts the repair under D-0292; actual cadence
and delivery remain unproved. The original checkout and active server graph
keep source `8aae03bf…`; no runtime is replaced.

The first isolated core run, `f790d2c8af21427c9580a503`, fails two DCN tests
and timing at 273.135 seconds; Ruff and mypy pass. A tracked-only workspace
omitted the externally archived, explicitly ignored index fixture. The
existing approved 2,804,496-byte body is restored unchanged and verified
against its provenance and the original full-run input hash. All 940
baseline paths are now present, differing only in health code and its tests.
All 73 affected DCN controls pass. This does not explain the timing failure.

The repair source is `7abf02ae…`, corrected binding `4c07fef6…`. Its normal
core check is active with inputs fixed, at
`/tmp/swingset-stage2-health-clock-restored-check-20261111`; the failed
939-file check and host observations remain separate. Full validation,
installed bytes and graph/monitoring proofs remain open for this source.

The restored-input run passes all 3,150 cases, Ruff and mypy but takes
246.199 seconds. An ABBA source-capture probe finds similar cached path
costs and does not justify relocation as an optimization. Sequential native
profiles execute equal SQL-call counts; their first-run library-loading
costs differ, without explaining core timing. The clock regression is
strengthened to publish status during the read itself; it fails before the
correction and all 70 focused cases pass after it. Source `dce34a49…`,
binding `b03ffca5…`, passes independent source review. A check with an outside
read-only observer passes correctness and static checks but takes 214.028
seconds. Host compression rises during the run; detailed VM pressure files
are unavailable. No causal performance claim or timing waiver follows.

At 17:03 UTC, the byte-identical reviewed repair is integrated into the
normal checkout. The original source and all 940 old validation paths are
preserved in a sparse jj workspace at
`/private/tmp/swingset-stage2-held-source-20261111`, exactly matching the
earlier full-run binding. The active server image and graph stay unchanged.
Validation of the integrated source uses the normal repository location;
no cache reset, warm-up, resource, weight or test-selection change is made.

## Current validation and source pulse at 17:24 UTC

The integrated check fails one crash-recovery test and timing at 217.06
seconds. The killed client can leave its PostgreSQL backend briefly alive.
The test now records that backend's PID, database and start time, then
observes its retirement and lock release through a bounded read-only
connection. The original nonblocking reopen and replay assertions remain.
Independent source review accepts the correction. The focused case passes.

The next normal check, job `adeab3baad294f7b84a778a6`, passes all 3,150
cases, Ruff and mypy but takes 198.154 seconds and fails timing. Application
source remains `dce34a49…`; the corrected test changes the full binding to
`6bb19eb7…`. All results and outside pressure observations remain in
`/tmp/swingset-stage2-health-clock-retirement-check-20261111`. No timing
waiver or causal claim follows. The active server graph still uses the
separate, unchanged `8aae03bf…` image.

The read-only production pulse at 17:24:54 UTC finds no requests since
16:16:27, about 68 minutes idle, although acquire ticks finish without an
error. The state-size and withheld-partition alerts remain open in the
maintenance queue. No production repair is performed. Pulse SHA-256:
`f1563ff6bb77c3a72cd53fa272bd187174bffaee03b4425072c0093369cf56b6`.

The existing rehearsal hold is reviewed at 17:24:56 UTC; its next review
is 18:24:56 UTC. Owner, reason, creation time and unstarted schedulers are
unchanged. Hold SHA-256:
`8367e7f5acc51f3f9e519df5b82112107c0f388e79d85ff96ad0a53c70a2e6c1`.

## Conservative SQL timeout reuse

The measured developer timing failure selects one narrow target: the native
scoring pipeline profile has 9,466 timeout-setting SQL commands costing
1.120 cumulative seconds. D-0293 rounds remaining budgets down to
10-millisecond boundaries above one second and preserves precise integer
milliseconds below that. Absolute expiry is still checked before every
query. All transaction, configuration and error invalidation remains.

The real-PostgreSQL regression fails before the change and passes after it.
All 83 engine, deadline and crash cases pass; a stronger expiry assertion
proves late SQL never reaches the raw cursor. An initial test failure at
a floating-point boundary remains recorded; offsets away from that boundary
correct the test. Independent source review accepts the final code/tests,
SHA-256 `c04da655ea6838f22a228fb2c8772afedfa1c55cc11029cbf7a417954e11898d`.

The next native profile keeps 62,846 pre-query checks and reduces setters
to 3,065, costing 0.478 cumulative seconds. That demonstrates fewer SQL
commands, not normal-core speed. Current source `99f66aa8…`, binding
`df87edc1…`, passes all 3,151 core cases, Ruff and mypy in job
`53521196bcb84c4fbef43b86` but fails timing at 206.973 seconds. Its complete
failed result remains in
`/tmp/swingset-stage2-timeout-buckets-normal-check-20261111`. No further
timing retry is selected without measured cause or a substantive correction.
The server graph and shared native-38 database remain unchanged.

## Hold review at 17:51 UTC

The existing rehearsal hold is reviewed at 17:51:53 UTC, next review
18:51:53 UTC. Its owner, reason and original creation time remain unchanged;
pipeline schedulers remain unstarted and all twelve protected services
match. Hold SHA-256:
`d7122a66b10e1e2a16045d49aab0fbaa466a219611912f4816a7408809cd99e6`.
The same graph continues without a reported error or OOM; transaction
activity is not proof of completed unit matches.

## Source still idle at 18:04 UTC

The read-only production pulse at 18:04:42 UTC finds eight more successful
empty acquire ticks since 17:24 and no new request. The latest tick ends
at 18:01:13; last fetch progress remains 16:16:27, about 108 minutes idle.
Existing maintenance items retain this finding and the state-size/withheld
alerts; no production repair or source request is performed. Pulse SHA-256:
`19e6ae4c770d0daa975cf66b05271c072d51dddcee088e5bfeab5f414517540c`.

## Bounded Linux SQL-shape diagnostic

After independent review and fresh admission under both validation locks,
one unchanged archived scoring pipeline case runs in the pinned normal
Linux client image next to the existing test PostgreSQL. Source `99f66aa8…`
and all 940 bound paths remain unchanged. The case passes, owned test
cleanup completes, the actor is absent and its volume is retained. No
measurement or host cleanup ceiling fires. Execution review SHA-256:
`aac0161798b19613a1c3e1cf63a2905283733afa80726839010334d19846a7cb`.

The accepted trace records 64,730 driver calls and 62,846 pre-query checks,
with 3,250 groups, no overflow or metadata errors, and 97 local read-context
ordinals. The body takes 9.657 seconds, including 5.904 seconds of thread
CPU. Driver execute intervals total 3.744 wall seconds and 1.073 CPU
seconds. Measured metadata/aggregation overhead is 0.464 wall seconds;
wrapper dispatch, context-manager work and final serialization are outside
that accounting. Query-shape hashes and local ordinals do not establish
equal arguments or memoization safety. Credential/configuration SQL is
suppressed before hashing; no SQL text or parameters are retained.

The complete result stays in
`/tmp/swingset-stage2-linux-query-shapes-20261111/run-77b5b3997dde4f5da8e0aa66/result.json`,
SHA-256 `21a294f3e6415152e8cee09b6dc9e8f7bc7b5a2ae5d94b4843c676e0900ee802`.
It is an instrumented single-case diagnostic, not normal core timing or
proof that any proposed optimization closes the budget. No second trace,
core retry, server mutation or optimization is selected by this result.

## CPU measurement selected after SQL analysis

The accepted Linux trace's snapshot recipe-input lookups cost 0.042 wall
seconds, and their repeated shapes do not establish equal arguments.
The largest row-write path costs 0.666 seconds and must preserve immediate
stream visibility. No SQL memoization or row buffering is selected.
The analysis receipt remains in scratch, SHA-256
`ce3829c3d54412cb9a741c1f63c874367267201151c3d5fa9d98897665f26a59`.

The measured next target is 4.831 main-thread CPU seconds outside base
driver execution; it includes observer work and is not an identified
application hotspot. Prepare one CPU-self profile using a thread clock,
with generator suspension artifacts explicitly excluded. Before that
second diagnostic, promote the repeated lifecycle into a tested
`swingset` subcommand, reusing normal PID1 cleanup, resource checks and
finite ceilings. This selects tooling and a measurement, not an
optimization, normal-core retry, deployment or server data change.

## Pinned-image migration direction

All seven native SQL/explicit owner migration files match between the
held `8aae03bf…` source and the latest checkout. The plan now permits
only the authoritative dry-run schema transition to continue on that
exact fully tested image after its actual graph and read-only postgraph
proofs pass. No hybrid source closure is allowed. Desired DDL identity
must be rechecked at preparation. The normal cold-profile, execution,
typed-table, admission-transition, identity, hold and no-retry gates remain.

Independent review accepts this direction, SHA-256
`ac94fa85d0847edcd1a78f1d07386be3de6b6b285300f845b9355269abcec07a`.
This grants no preparation or execution authority. The graph remains
active; no shared database change has been made. Rekey, backups, private
publication, recovery, lifecycle and final monitoring wait for the final
source/image qualification. Current-source and Gate 2 checks remain open.

## Hold review at 18:31 UTC

The existing rehearsal hold is reviewed at 18:31:31 UTC, next review
19:31:31 UTC. Its owner, reason and original creation time remain unchanged.
Pipeline schedulers remain unstarted, and all twelve protected services
match. Hold SHA-256:
`55b25f153012d7be3df840313208143da95f273e8c7d3f49f15bd5ef2e17e89a`.
The same graph remains active without a reported error or OOM; unit results
are still pending.

## Whole-graph failure at 18:33 UTC

The exact source-8aae actor exits 1 at 18:33:08 UTC after about five and
a half hours, with PID zero and no OOM. The supervisor's terminal zero-exit
check raises its generic `ValueError`; its later generic stop reason does
not establish that the supervisor interrupted the actor. The collected
receipt contains all 108,390 results: 107,888 match, 500 mismatch and two
fail. It reports `complete=false` and `clean=false`. No replay is performed.

Inventory/all returns without completing its unit. Year/2026 exceeds the
unchanged 60-second atomic write limit and rolls its output back. Of the
500 mismatches, 492 resolution and four year units keep identical output
labels while changing dependency digests. Four link/event cases change
output: three lose one row and one keeps its count with a different hash.
These classifications do not explain or accept any difference. Source
investigation and independent terminal/cleanup review remain open.

The public receipt is 63,892,390 bytes, retained only in scratch at
`/tmp/swingset-stage2-selected-generation-whole-graph-20261111/collected-graph.json`,
SHA-256 `24358187745cf37c30edc576106f9085a7ccb570162874ad8a9228f88ee108bc`.
The supervisor reports all twelve protected services unchanged and a
minimum 459,749,584,896 free bytes. Private PostgreSQL has nonzero memory
pressure events, with no OOM; this is not a no-pressure claim or proven
cause of the year deadline. Migration, rekey, backups and private
publication remain unstarted.

## Failure-boundary check and error-report correction

Independent terminal inspection confirms natural exit 1, PID zero, no OOM
or restart, and no supervisor stop attempt. All three exact A51 database
names and OIDs are absent, its registry is empty, and no owned sessions,
process groups or cgroup remain. Protected C696/69 metadata, the shared
database's native-38 identity, guard, roots, hold and twelve services match.
This is a failed-graph boundary acceptance, not graph acceptance. Verdict
SHA-256 `42b699259e17a97a248d38cb228722567f196377f4d076b816ab23a08fa7d45e`.

Correction to the initial inventory classification: the old graph wrapper
overwrites any pre-completion error with its generic message. That message
does not prove a normal transform return. The lost original error cannot
be reconstructed from this receipt. The new wrapper still fails missing
completion, preserving an existing error and reason. Three regressions use
real transform exceptions/normal noncompletion and actual transaction
writes, proving both error retention and rollback. All 29 tests in the
verification file pass, with Ruff and mypy. No graph replay or transform
version bump is performed.

## Source still idle at 19:05 UTC

The read-only pulse at 19:05:40 UTC finds twelve more successful empty
acquire ticks and no request since 16:16:27, about 169 minutes idle. The
watchdog has also recorded this idle state at 17:35. The old Wayback
server-error warning remains. No production repair is performed. Pulse
SHA-256 `ee657f35f289521fc989034bbce78d9848da2f603f4e6ac3389c752d8156d2cb`.

## Hold review at 19:08 UTC

The existing rehearsal hold is reviewed at 19:08:12 UTC, next review
20:08:12 UTC. Its owner, reason and original creation time remain unchanged;
pipeline schedulers remain unstarted and all twelve protected services
match. Hold SHA-256:
`175cdf49ebb5b26371bc64e5d48e2ed4d9961a34d5fa3622cf97513bbb470eda`.
The proposed read-only forensics packet is being revised for customer
execution after root SSH and passwordless sudo prove unavailable. No
forensics staging, SQL execution or business replay has taken place.

## Forensics capture fails before query stream

The customer-owned v2 packet passes remote hashes and private ownership
checks, then its one capture exits 1 before installing the SQL-owner
fence or sending the forensic query stream. Docker rejects `top -eo pid=`
because suppressing the header prevents its PID-field parser from working.
A separate read-only probe confirms `top -eo pid` succeeds with the header.
No target dependency or link data is captured. The cleanup metadata query
returns zero named backends; the Docker exec client exits zero.

Independent failure-boundary inspection confirms no owned clients remain
and protected services, guard, roots, hold and preseal metadata match.
The collector's own cleanup error remains recorded. Result SHA-256:
`5adb706fcc9dc4a33b5889741bd831f289abf7e917541e8cb84585324ada06f5`;
boundary verdict SHA-256:
`32b48015f75d63733325ca30be43b605556dc057be047ae9ab10fa202bfcf1bd`.
All v1/v2/v3 packets remain preserved. V3 is a prepared one-option correction
with a fresh nonce; it is not executed. Before another capture, the repeated
collector moves into a tested `swingset` command under the repository's
script-reuse rule. SQL and cleanup-shell files stay separately reviewed
operation inputs. No automatic replay or migration is performed.

## Bounded metadata capture and first causal witnesses

The repeated collector is now the tested `swingset graph-forensics` command.
Its first maintained capture executes all twelve read-only queries and
cleans up its owned clients, but validation fails: the decoder expects JSON
lines while PostgreSQL emits formatted JSON documents. The global manifest
bound also withholds dependency bodies when one nested object exceeds the
per-object bound. Neither condition is accepted as successful capture.
The original result remains unchanged, SHA-256
`0bd54b49e3d8e53e9e8d9bf37914a1c542aab92f8369f3f9882fe1f041b5d3dd`.

The corrected decoder reads bounded concatenated JSON documents, checks
their exact order and rejects truncated or extra content. Forty-seven
focused tests, Ruff and mypy pass. Independent source review accepts the
change. A separately reviewed capture of eight top-level roots completes
in 2.884 seconds, with matching hashes, no remaining owned clients and
unchanged protected services. It does not prove nested closure or graph
acceptance. Result SHA-256:
`2d4006f0a92fe582a767d3103852d7751a57f34bee7729109e52392b5af9ba3f`;
terminal verdict:
`5f4972a024ebc505843b72c2b7c047d03050f77704db0f1ebdbb9dc0c9249060`.

For one resolution and two year units, replacing only the stored inventory
generation group with the checkpoint's current inventory group reproduces
the graph's dependency digest exactly. These three cases contain stale
checkpoint dependencies. The remaining output-identical mismatches still
need their own witnesses. For each of the three link cases that loses one
row, removing only dancer 16984 reproduces the dependency digest; removing
that candidate and its resulting identity fields reproduces the output
digest exactly. Why the candidate disappears remains unresolved. The fourth
link case has equal row counts and needs a separate explanation. No recipe
bump or graph replay is performed.

## Hold review at 19:57 UTC

Hold renewal is now the tested `swingset review-rehearsal-hold` command.
Its independently reviewed first run completes at 19:57:40 UTC, with the
next review at 20:57:40 UTC. Owner, reason and original creation time remain
unchanged; pipeline schedulers remain unstarted and all twelve protected
services match. Hold SHA-256:
`b25fb8b15d62f5baabdee3844aebfaf9668439c8bd2a81568a30844afd62f10b`.

## Source pulse at 20:15 UTC

The read-only pulse finds two more successful empty ticks and no request
since 16:16:27, about 239 minutes idle. The latest completed acquire tick
is 19:10:29, over an hour before the pulse. Service-state inspection follows;
no production repair is performed. Existing watchdog warnings still report
the state-size cap, withheld partitions and Wayback server errors. Pulse
SHA-256:
`ffbff75590a0d23489d254fc8a8559e54e37767324b6ad6a7b09f092bfc7bd77`.

The following read-only systemd and journal checks show a new VM boot
between the 19:10 and 20:15 ticks. Acquire and watchdog timers are active;
the 20:15 acquire tick exits successfully at 20:15:36 with zero requests.
The first pulse preceded that completion. The journal records no tick
between the two boots. This corrects the apparent missing current tick,
but collection still has no fetching progress. No restart or repair is
performed by this check.

## Local diagnostic preparation refuses after VM restart

The first maintained CPU-diagnostic preparation refuses before creating a
packet because the warm test service is stopped. The existing test and
physical containers both exited zero about an hour earlier; their exact
IDs, pinned image and configuration labels still match. The supported
test-service start function starts the same test container, then refuses
role-signature drift. It reaches no physical-service start. No container,
database, role or ownership record is reset. Read-only diagnosis precedes
any recovery; no CPU profile or core timing retry is run.

## All 496 dependency differences reconstructed

Four disjoint read-only captures cover all 496 output-identical mismatches
and their 397 distinct top-level roots. Every capture completes, exits zero,
leaves no owned clients or named backends, and preserves the protected
boundary. Remote hashes before and after SFTP match each local file. The
unchanged per-operation root bound is 128; no nested manifest is read.

For every unit, replacing only the previous inventory group with the
checkpoint's current inventory group, then applying the held source's
canonical outer-list sort, reproduces its graph dependency digest exactly.
All 492 resolution and four year units have witnesses, with zero residuals.
Analysis SHA-256:
`4041b180b5e5a20438960fbb4bc9c1df0997384270a2b8aea3e4acbbbc987243`.
Independent reconstruction confirms all 496 hashes. The review's initial
counterexample omitted the required outer sort; its corrected witness
records that observer error. Terminal verdict SHA-256:
`bb49fb383e2f02971c352b7d6b4dea4010249143ebb281073fe30db6dec2dd52`.
This explains existing checkpoint dependencies; it does not accept the
four link differences, either failed unit, or the graph gate.

## Numerical boundary and local profile recovery

The captured candidate inputs identify three Emily Wilson/Eli Wilson
pairs, Anna Koch/Anna Kratochwil, and Ben Ha/Ben Howard. A local pure
comparison on ARM64 finds Emily/Eli at `0.92` in C++ and
`0.9199999999999999` in Python. The unchanged `>= 0.92` gate flips; Anna
and Ben stay at `0.92` in both. This is a local backend witness, not proof
of the held server's backend. The repeated probe is promoted to tested
`swingset link-numerics` before any server comparison. Twelve focused tests,
the standard focused test entry point, formatting and CLI help pass.

Independent local checks confirm the stopped/restarted temporary TEST
profile contains only four initdb databases and its administrator role,
with no user objects or other clients. Its reviewed one-shot recovery
removes only exact empty container `4555d664…` and dead empty owner record
`3fdfaf2d…`. Supported functions recreate TEST as `1cb24135…` and its
immutable template. The same durable physical container `6bfb6553…` starts
and validates without reset. The unmatched copy ledger remains untouched.
No test, graph replay or server migration is part of this recovery.

## Hold review at 20:40 UTC

The maintained hold command reviews the existing hold at 20:40:01 UTC,
next review 21:40:01 UTC. Owner, reason and original creation time remain
unchanged; pipeline schedulers remain unstarted and all twelve protected
services match. Hold SHA-256:
`8e7d843ee37e5382c3d20f0d0388d2439b04e334b5d6da8a255270dad3f5639e`.

## CPU profile rejects its timing quality

The reviewed maintained profile runs once on source `8a0f9c0f…`, with its
948-file binding and recreated local TEST profile. The one original
scoring case passes. Source stability, resource limits, actor retirement
and owned cleanup pass; no measurement or cleanup ceiling fires. Body
wall time is 23.095 seconds and main-thread CPU is 18.574 seconds.

The profile remains rejected: its 5,831 entries include negative self
times and an impossible 1,279-second lock entry. No performance ranking or
normal timing claim is accepted. Raw result SHA-256:
`7dbd683173a7b3381940cc8eec7b7d97c506c7558e8f5aa1d834e4f148b8dfeb`.
A tiny separate CPython 3.12 reproduction observes profiler callbacks in
multiple threads subtracting their distinct thread clocks. Filtering all
four callbacks to the enabling thread removes that error in the tiny
experiment. Select a narrow maintained-profiler correction and real
worker-thread regression before another pipeline profile. The failed
result remains unchanged; the core timing gate stays open.

## Pure numerical sandbox fails before Python

The independently reviewed sandbox's first launch fails before its Python
process starts. Docker refuses inherited logging settings: compression is
enabled while the maximum file count is one. Exact container `7f0bf2e4…`
remains created with PID zero, zero start/finish timestamps and no OOM.
The supervisor completes its failure path in 1.514 seconds; protected
services, guard, roots, hold and preseal checks still match. There is no
numerical result, database access or graph replay.

Stable remote/local hashes bind its owner, allocation and result files.
Result SHA-256:
`9f78a532d550a0a54138ad913d021f1e67694a8ccc7b6b075c08ca2ccdb867c6`.
The fixed safe projection also confirms the old verifier has no
`RAPIDFUZZ_IMPLEMENTATION` override. No full environment is exported.
Preserve this failure and the created container. Before a second lifecycle
invocation, promote the supervisor into a tested command with explicit
bounded logging settings, as the script-reuse rule requires. No retry is
performed yet.

## Server numerical gates and source watch at 21:16 UTC

The maintained sandbox runs once with explicit bounded local logging and
completes successfully. Exact container `edf941d6…` is stopped; its four
output files have matching remote-before/after and local hashes. It runs
the held image on x86-64 Linux with CPython 3.12.16 and RapidFuzz 3.14.6.
Default and generic C++ both compute the three Emily/Eli similarities as
`0.9199999999999999`, below the unchanged `0.92` gate. The old verifier and
probe have no implementation override. The archived candidate code and
normalization bytes match; the retained candidate had passed that gate.
This supplies the runtime numerical seam behind the three reconstructed
candidate losses. Independent causal and terminal review follows. Anna
and Ben remain `0.92`; the fourth link difference is still unexplained.
Result SHA-256:
`3b08831fb89351ac629ec29bfffd5da7a59b2cc940354576539db0983634c7ee`.

Read-only production journals through 21:16 contain thirteen successful
acquire ticks with zero requests. The watchdog also reports stale fetching
and the known expired Wayback warning. No production repair is performed;
the latest earlier known request is 16:16. The compact generated watch
[receipt](../../evidence/runtime/postgres-stage2-2026-11-11/source-watch-2116.json)
retains commands, exit codes, counts and log hashes. Full logs stay in scratch.

## Hold review at 21:24 UTC

The maintained hold command reviews the existing hold at 21:24:09 UTC,
next review 22:24:09 UTC. Owner, reason and original creation time remain
unchanged; pipeline schedulers remain unstarted and all twelve protected
services match. Hold SHA-256:
`838d86b4ef2dc288442402c1a6ffff69ece2c5dcce4c438fb4e9384592ec06bb`.

## All 500 mismatch labels explained

The final names-only capture reads exactly 204 retained fuzzy pairs and
260 unique public input rows, with no missing subjects or dancers. The
reviewed numerical sandbox compares that whole pair set in the held image.
It exits zero with no OOM and leaves its exact container stopped. Captured
and calculated files have stable remote and local hashes. No solver or
business transform is run.

The offline comparison first reproduces all 204 old scores exactly and
the complete 3,235-row stored label. Only two similarities change by one
bit, for the same dancer in two contests. Both candidates have confirming
printed IDs and score 1.0. Scores, confidence, membership, ranks and
decisions remain unchanged. Replacing only those two cells reproduces the
graph's complete found label:
`4f8e9d7d5209cb964fd8a3e66fa4a36e5f495b95ed2410e88b9f29aeb3267918`.
Independent stdlib-only reconstruction confirms it, verdict SHA-256
`95d600b68fa84a998275428928898e3f7b51d1b4a04ff57e64b1d0b536f99f17`.

Together with the 496 inventory-group witnesses and three complete
candidate-loss reconstructions, this accounts for all 500 mismatch labels.
It does not prove a historical runtime was executed. Neither failed unit
is accepted, and the graph remains failed. Linker version 10 marks the
observed runtime numerical changes; it does not change rounding, weights
or the identity algorithm. Re-keying must leave affected work queued.

## Current normal check and architecture correction

The first normal check of source `0b17ac3d…`, binding `3ae526d6…`, selects
3,335 cases: 3,332 pass and three architecture guards fail. The core takes
193.99 seconds and also fails the unchanged 180-second limit. Ruff and
mypy pass. Job: `92dc3dace10b463d9309b1d2`. This failed result remains
unchanged; the fresh local TEST profile is not evidence of a speedup.

The failures identify the new numerical diagnostic under the production
linking package and its dynamic imports. Move the tool to `deploy`, use
static optional C++ imports, and fail closed when a backend module is not
already loaded. Keep all recipe-scope and entry-point rules. All 70
functional cases and five existing architecture checks pass. The corrected
source still needs its own normal and full qualification; no timing pass,
migration or publication is claimed.

## Corrected source passes correctness, not timing

Source `b392a022…`, binding `b29e3fe3…`, passes all 3,336 core cases, Ruff
and mypy. The core takes 190.14 seconds and fails the unchanged 180-second
gate. The earlier three architecture failures are fixed; no rule, selection,
weight or resource cap is relaxed. Job: `ad5e3dd0053a4430a6a8287d`.
This is correctness evidence, not a completed normal qualification or an
attributed speedup. Do not retry it without a new causal reason.

## Hold review at 22:20 UTC

The maintained hold command reviews the existing hold at 22:20:32 UTC,
next review 23:20:32 UTC. Owner, reason and original creation time remain
unchanged; pipeline schedulers remain unstarted and all twelve protected
services match. Hold SHA-256:
`d6f29d164c6159aedf90e57093bbaa8705e4ffbb8fd789ede59544e3bdc35fad`.

## Publication wait diagnostic at 22:44 UTC

One bounded diagnostic of the existing uncertain-publication case passes
with fixed source `0d5fa506…`, binding `a6f52c93…`. The body takes
3.299 seconds and 1.464 owner-thread CPU seconds. Its 7,820 outermost
driver waits consume 0.781 elapsed and 0.140 CPU seconds, with zero nested,
foreign-thread, exception or invalid-clock samples. Quality, source and
owned cleanup checks pass; the actor is absent. No SQL or arguments are
captured. Result SHA-256:
`d81a92a7a338b8daadfa7926fdfdf65b4678775c769c3c094b962218a95b1f12`.

This isolated case does not reproduce an older suite's 27.94-second call;
the corrected `b392a022…` suite already records 6.61 seconds. It does not
prove a normal-suite speedup or cause. The 180-second gate remains open.
Scratch packet: `stage2-publication-waits-20261111-2240`. Independent
terminal review accepts the clock accounting, fixed source, resource caps
and cleanup; both locks are released. Verdict SHA-256:
`4aaded22bd462edc93e0b82cae4b404efa85bae35efb4d1f5d580850ee9f292e`.

The separate failed-unit diagnostic has not run. Its first source review
refuses an operator file mounted at `/probe/unit_diagnostic.py`: the actual
guardian permits only fixed destinations. Preserve that guardian and use
an already allowed destination with a real container-validation regression.
Independent refusal SHA-256:
`993dd14a6270d5468cca192fb838076ca90fae58c9541d5f9a3a1f560d8f0301`.

## Next qualification direction

Offline comparison of the corrected normal run finds five new fast files
with fallback estimates totaling 182 seconds, versus 0.820 measured phase
seconds. The runner learns their actual weights on correctness success,
including when timing fails. Do not edit, reset or pin the cache. All four
clients remain long; the error alone does not explain the timing gap.
Current common-case elapsed sums are 3.58% above the older passing run;
overlapping worker times cannot be subtracted from job elapsed time.
Analysis SHA-256:
`91917b871e18f6f7363070e1d3f5cca4161026e21df39d791cba5ef1fc918802`.

Independent review permits only the required first qualification after the
current diagnostic source closes, passes review and is formatted. Preserve
all earlier failures and every normal cap and selection rule. If that new
source misses timing, stop unchanged retries and select a measurement in
that normal context. No production optimization or further isolated profile
is selected. Direction verdict SHA-256:
`4e6c96fab0dd1d6161d9caf7f19a198fad011a12d848f09de5cbaffd1e016d80`.

## Unit diagnostic source qualification

Independent source review accepts the corrected eleven-mount composition,
using the existing checker root as a read-only operator directory. The
guardian stays unchanged. Producer image, OCI artifact, Python source and
archive hash are checked separately. Source verdict SHA-256:
`d8fcd46820fdc30eb1636914c9ce524e3fd0587e44f997cd048c9cb1399be747`.
This grants no actor-start or database-copy authority.

The required normal check of source `0aca6352…`, binding `5e9f3583…`,
selects 3,430 cases. All calls pass, but five directory-validation cases
error during Linux teardown: their global `os.fstat` mock lacks `st_ino`
when `shutil.rmtree` runs before the mock fixture restores it. Scope the
mock to the test body and retain every ownership and mode assertion.
Ruff and mypy pass; timing also fails at 184.67 seconds. Job:
`d39692c6ee7c4133a1823ac1`. Physical cleanup completes. The controller
survives the tool-server restart, finishes once and is not relaunched.
No timing speedup is attributed and no unchanged retry is selected.

## Hold review at 23:04 UTC

The maintained hold command completes at 23:04:08 UTC, with the next review
at November 12 00:04:08 UTC. All twelve protected services match; owner,
reason and original creation time remain fixed. The interrupted tool
handle is not a reason to repeat the completed command. Hold SHA-256:
`d4e44c4f047cd75eb6bd0396705fb1461ee9b7b69bd0eb9e6f884deaf60c1ba4`.

## Linux directory-test cleanup correction

The test now scopes its `fstat` replacement with `monkeypatch.context()`,
asserts restoration before return, and restores directory permissions in
`finally`. Only the test file changes; the reviewed operator and supervisor
bytes remain fixed. The same pinned Linux client passes all five cases,
including teardown, with no database credentials or network. Its actor
exits zero, has PID zero and no OOM, and remains stopped for review.
Result SHA-256:
`04fece15943f8d67d2b9761ea6710a1e7f157cca9b1441ec428c86e8b682f893`.
Independent source and terminal acceptance SHA-256:
`5e8f822ae0d9713f8a18aa9d647b39a885730e8e4ec65a323784e8414d39cbc6`.

This corrects the five known teardown errors, not the failed timing gate.
Do not repeat the normal run unchanged. Continue preparing the separately
reviewed disposable unit diagnostic; its old producer image already has
its own core and full-suite proof. Current qualification and Gate 2 stay
open. No server actor or database copy has started.

## Source watch through 23:26 UTC

Read-only production journals show twelve successful acquire ticks from
22:30 through 23:26, all with zero requests. The watchdog repeats the
existing expired archive-host warning. No repair or production change is
made; the known collection stall stays in maintenance items 40, 41 and 44.
The first SSH date-quoting attempts fail without changing anything; the
corrected queries succeed. The [compact watch receipt](../../evidence/runtime/postgres-stage2-2026-11-11/source-watch-2326.json)
records both journal hashes and the observed interval. Logs stay in scratch.

## Disposable-unit read-only preflight

Fresh direct CLI checks confirm all twelve protected services and the
guardian incarnation. The private catalog still contains only C696 and
the retained 69 copy, with no sessions on either. C696's UUID, native
schema 38, source schema 37 and migration checksum match the protected
baseline. This is metadata evidence; the actor must still verify the
full source checkpoint before copying.

Shared and private database sizes are 38,505,133,747 and 38,409,934,515
bytes. Free space is 580,654,825,472 bytes, exceeding the unchanged
508,041,069,976-byte admission requirement. No guarded worker is running,
verifier address 18 is free, and the new nonce has no root, volume or
container. Security and network hashes match. No server change is made.
Preflight SHA-256:
`637438ba7f75d46bc9a0480617205212c638241ddc2acfc23f44c36fc3f97e1e`.

The failed local Python wrapper and initial read-only template/SSH attempts
remain recorded. Corrected standard CLI observations pass; none starts a
container or changes data. Exact cold preparation still awaits review.

## Cold preparation and controller pause

Independent review accepts the exact 114-step cold preparation, with
three new filesystem volumes, 21 bounded helpers and one unstarted actor.
It grants no database copy, actor start, migration or cleanup authority.
Preparation verdict SHA-256:
`13d9e641cb340a14dd01e141d59c7bf6cb1c06d3a9a7f9ec066c55e67068c592`.

Steps 0 through 47 complete. Seven source-only helpers validate topology,
private-file metadata, 1,689 inputs totaling 69,548,306 bytes, and the blob
link. The public operator seal is the eighth completed helper: root-owned
directory mode 0555, its sole file mode 0444 and exact source hash. All
eight helpers exit zero without OOM and remain stopped. No new volume or
diagnostic actor exists yet.

The controller then mistakes the successful, empty runtime-volume listing
for a failed container lookup. It halts before step 48. The reviewed
command actually returns its declared exit zero and empty output; no
helper or command is rerun. Continuation awaits a narrow review of the
saved partial state and renewed administrative hold.

## Hold review at 23:55 UTC

While preparation remains halted, the maintained hold command completes
at 23:55:52 UTC, next review November 12 00:55:52 UTC. The twelve protected
services match, and owner, reason and creation time remain fixed. This is
an explicit hold renewal, not an unrecorded rebind of the frozen preparation
plan. Hold SHA-256:
`3a06e796f3260e540e02f8bffeda2d45221cffd1b3cb942e831b768fd1d63a0d`.

## Cold preparation completed

The continuation review accepts only remaining steps 48 through 113,
binding the renewed hold explicitly and preserving all earlier records.
No prior helper, fence or command is rerun. Addendum SHA-256:
`06db21dda95703bcefec98b642228568babc2d70487bb1f7e3c904db76ac1744`.

All 114 commands complete. The 21 helpers exit zero without OOM and remain
stopped. Three new filesystem volumes have the expected ownership; the
metadata copy matches its read-only source byte for byte through quiet
`diff`. Public input counts, file types, private-file permissions and the
blob link match. No secret value is printed or hashed. Preparation result:
`0c1a8676b0b499f1d5fafdcdbc6ad326b5bae3726c6a3d9dd932bbaa29f04a94`.
Actor `109bb869…` is created with eleven mounts and remains unstarted at
this boundary. Preparation performs no database copy or migration.

## Inventory diagnostic startup

The maintained hold review at November 12 00:11:37 UTC preserves all twelve
protected services and the original hold identity; next review is 01:11:37
UTC. Hold SHA-256:
`1a33da621c047301fc3c3075085528c9f76e2cbe7d065a76834e8e09fe20e10f`.

Execution plan `80263893…` binds the actual actor, original failed graph
receipt `24358187…`, old producer `8aae03bf…` and separately reviewed
operator. Independent verdict `40517ea3…` permits one inventory diagnostic
and cleanup of only its one owned database. The exact foreground maintained
CLI starts once at 00:37 UTC. Source and current host qualifications remain
distinct; neither graph acceptance nor Gate 2 is granted.

Independent startup checks verify PID 3309078, birth 984027405, namespace
4026533551, actual authorization and both firewall chains, source UUID and
native schema 38, private environment equality and all protected boundaries.
The initialized owner token is `dec25faf0c9f460e8f466ed4`; readiness is not
inferred from an empty registry. The actor reports its freeze phase after
checking the source checkpoint. Historical memory-pressure counters remain
explicit; there is no new OOM. Startup verdict SHA-256:
`6aa69a25fcbd5a2be72152d3f807ff78e9b5ff2f69393a781fe813a0972fca97`.

## Per-worker timing observation

The 184.67-second normal run cannot identify actual executing workers from
its merged receipts. The corrected observer records native monotonic
boundaries, owner-thread CPU and worker identity through report serialization
without changing phase totals, outcomes, selection, scheduling or weights.
Eleven focused checks pass, including real two-worker transport and clock
mock isolation. Root formatting preserves the observer AST. Independent
source review accepts one first normal qualification of this changed source,
with the same 180-second gate and every earlier failure retained. Verdict:
`d15e313880366ef4e4d2f5ab86c93ec21da9ffa86e5a42a01465ad3be25305f6`.
This adds observation, not a production optimization or speed claim.

## Observer qualification and alarm reproduction

First normal observer job `2f057650…`, source `e056b1fa…`, binding
`ed0eff18…`, selects 3,438 cases. A worker exits with a keyboard interrupt
inside the nested 30-millisecond write-deadline test. Its unfinished
partition fails membership; the job takes 199.93 seconds. Ruff and mypy
pass, all owned client cleanup completes, and sampled cgroups show no OOM.
Preserve this failure; it is not a passing timing or membership result.

The new records give complete worker timelines for clients 0 and 2.
Their workers finish nearly together and spend under 1.03 seconds between
observed phases. Client 3 completes correctness, but its clock-failure
self-test corrupts two outer observations by leaving a direct clock alias
patched until teardown. Scope that patch inside the test body: the same
test then passes with the actual plugin active and outer quality true,
while preserving its intended inner error. Focused proof:
`8a1107ba55d7fce69483a19c7a9d1181775e743c00f8c198ebc86d26224e39d8`.

Valid partial data show broad elapsed slowdown, not a scheduling-only gap
or proved redundant work. The new nested tests total about 3.32 seconds
on one late worker, which does not explain the whole overrun. The empty
restore case rises from about 29 to 41.55 seconds, with 9.44 seconds of
owner CPU; the remainder cannot be labeled SQL waiting. Analysis:
`1beff3dee853bf5543d9c1aba88c195c813ab39817f05382636b85520ffc1461`.

A deterministic no-database reproduction proves a separate deadline
cleanup defect. After a server `WriteTimeout`, delivery of the actual owned
alarm callback during disarm escapes as `_Expired` instead of being
translated and counted. The control path produces `WriteDeadlineExceeded`.
The historical worker traceback is unavailable, so this does not prove its
cause or implicate the observer. Reproduction SHA-256:
`cee7a3659ae6088be06047b1a0e24fa59dc81b931d1090c3606844869d9727c1`.
The focused fix captures the body failure before disarming, makes owned
alarm delivery during cleanup mark expiry without interrupting restoration,
and classifies once after the caller's timer and connection deadline return.
It preserves commit uncertainty and genuine keyboard interruption, and
cannot return success after finalizer expiry. All 27 focused cases pass,
including deterministic transition regressions and existing real alarm and
Postgres rollback checks. No deadline is extended. Final source SHA-256:
`69af48b70903e5701d9f15dd204b9bed80d4eca845eaf1e8b1b527eb4bb78b80`.
The old-image diagnostic uses the unchanged producer; this fix does not
explain or repair that separate preparation timeout.

## Hold renewal timeout and recovery

The 00:55 maintained renewal times out. Read-only audit proves only its
temporary file was installed: the installer is dead with exit zero, the
separate activation helper never exists, and the active hold remains
`1a33da62…`. Preserve this failed packet and temporary file; do not activate
or replay it. Audit SHA-256:
`d0cf2f9bb7bda9e9a5a9387e8661fc96c0e4f1b7eccea7f31757165b03ff093c`.

One fresh maintained renewal succeeds at 01:03:00 UTC, next review 02:03:00
UTC. The twelve protected services and the original owner, reason and
creation time remain fixed. The running diagnostic permits this ordinary
same-identity renewal. Hold SHA-256:
`073c61fa2852b19257ae49cf3e90cacdea4cf69a06584546f355a826e11bd828`.

## Inventory diagnostic stops before the trial

Actor `109bb869…` exits naturally with code 1 at November 12 01:09 UTC,
after about 32 minutes. Its freeze completes, then input acceptance reaches
the unchanged 60-second atomic-write deadline. The original error is
`WriteDeadlineExceeded`; no inventory trial, rollback witness or accepted
candidate checkpoint is produced. The supervisor's generic terminal
`ValueError` is bookkeeping after that exit. Neither the 45-minute freeze
limit nor a host interruption is established as the cause. Public receipt
SHA-256: `64dadaea1c89c5439782ad1d2a736fb74253ef0aee0784ab67244a13a75204bd`.

Independent read-only review finds the exact owned database, registry,
clients, process groups, cgroup and local SSH absent. The protected services,
source identity and schema, second retained copy, native import actor,
public file pins, guardian and hold remain intact. No OOM occurs. The actor
does not reach its final full source-checkpoint recomputation; metadata
checks cannot substitute for that proof. Review SHA-256:
`9910eda06b8125ce2cc4c1ff426ffb0966b39ca203dc120ffa27f1e83cbde6f2`.
The failed packet remains in scratch. Do not repeat it or extend its bounds;
first compare this preparation path with the earlier successful acceptance.

## Production fetching resumes

The bounded read-only watch covers thirteen successful acquire ticks from
November 12 00:20 through 01:24 UTC, totaling 748 requests. Both observed
watchdog executions finish successfully, with no alert text in this excerpt.
This is observation of unchanged production, not a deployed repair
([receipt](../../evidence/runtime/postgres-stage2-2026-11-11/source-watch-20261112-0126.json)).

A separate bounded read through 01:41 UTC observes eleven successful
derive ticks with progress totaling 1,116. The alert tail includes the known
00:05 derive-idle warning and a repeated expired archive-host warning at
01:35. Current progress does not prove those watchdog findings repaired
([receipt](../../evidence/runtime/postgres-stage2-2026-11-11/derive-watch-20261112-0146.json)).

## Corrected deadline source qualification

Independent source review `3074a86e…` accepts the final deadline correction
and clock-test patch scope. Its first normal check, job `f84f54d9…`, binds
source `d5c068d0…` and source binding `526ff958…`. All 3,446 cases pass,
including the formerly interrupted partition; Ruff and mypy pass. The job
still fails its unchanged timing gate at 213.18 seconds. Every client's
phase observation has quality true with no errors, and all owned cleanup
completes. Summary SHA-256:
`8e9418ee1ff63cbae437dbe9161dd519d540485e5fe70e5adeb385f239d84b9e`.
The historical worker crash's cause remains unproved.

The empty restore/rebuild pair now takes 25.48 and 38.31 seconds, including
6.75 and 9.60 seconds of owner-thread CPU. These cases include setup of the
source, publication, checkpoint capture with an inline restore check, empty
restore, activation and rebuild. Their totals do not identify the slow
operation. Preserve this failure and use a matched normal-context subphase
measurement before selecting another correction. No unchanged retry or
timing exception follows.

## Routine hold review at 01:47 UTC

The maintained review succeeds at November 12 01:47:07 UTC, with next review
02:47:07 UTC. All twelve protected services remain unchanged, as do the
hold owner, reason and creation time. Pipeline schedulers remain unstarted.
Hold SHA-256:
`a46dec6af3a69d2b2367958b77ac8e5ebb57c844fd237c7249a055a4d7c7e448`.

## Corrected diagnostic source and preparation packet

The standalone operator now reproduces the graph's full destination
enumeration and read-only input binding before ordinary committed acceptance.
It requires the exact 108,390-unit/D58 inventory, source `00c831a4…`,
candidate `5039effb…` and original input map. Bounded RAM observations retain
phase, owner CPU, coarse statement families and primary-only static traceback
locations. They cannot identify a precise timed SQL statement or repair the
old producer's separate alarm behavior. All 43 focused cases, Ruff and mypy
pass. Central formatting preserves its AST. Source `c745d63f…` and tests
`5c612304…` pass source-only review `1850dadf…`; no server execution follows
from that review.

The local `99ffb3d1370e` preparation packet proposes 147 standard commands,
24 helpers, three new filesystem volumes and a cold actor that never starts.
It replaces the prior one-off sealing script with four explicit path
operations and public topology/hash checks. No recursive traversal or fsync
durability is claimed. All nine staged public files match their local hashes.
Manifest `74fc704f…` is submitted for preparation-only review; no server
preparation or replay has occurred at this boundary.

## Matched restore measurement

Source review `d3d6e681…` accepts a separate call-only observer with native
clocks, fixed labels, actual worker identity and at most 64 nested records.
Twenty-one focused cases and the existing architecture guards pass, as do
Ruff and mypy. Ordinary observation faults invalidate measurement without
replacing expected exceptions; fatal error and hook-restoration cases are
covered. Central formatting preserves both source ASTs.

Its first normal job `b1de85b2…`, source `52574e9a…`, binding `e8fe879e…`,
passes all 3,474 cases, Ruff and mypy. It still fails timing at 204.51 seconds.
Summary SHA-256:
`55b2195ec151bf85ffff67a0b1ca077383ff1a43e3f1fd61065e9acf130aeedb`.
The lower wall time is not a speedup claim: source, membership, learned
weights and observations differ from earlier jobs.

Only client 0/worker `gw1` observes the ordered restore pair. Both payloads
have complete coverage, restored hooks, no foreign calls or dropped records,
and valid clocks. The first body takes 23.895 seconds, including 6.416 owner
CPU seconds and 23,220 driver waits totaling 8.025 seconds. The changed-source
variant takes 33.914 seconds, including 9.648 owner CPU seconds and 34,019
driver waits totaling 12.183 seconds. Nested costs overlap; waits include
client and scheduling work and do not measure server CPU.

Independent review verifies exact selection, resources, source and all owned
cleanup; all five core containers and volumes are absent. One resource sample
is missing; observed OOM counters are zero. The measurement is accepted while
the timing failure remains. Review SHA-256:
`24670b81881c3c265a94890e3f16983b603f282b48a4edc669efac6b12f63cec`.
Inspect measured operations and their source before selecting a correction;
no unchanged retry or optimization is accepted here.

## Routine hold review at 02:24 UTC

The maintained review succeeds at November 12 02:24:24 UTC, with next review
03:24:24 UTC. All twelve protected services and the original hold identity
remain unchanged. Hold SHA-256:
`5fa041dd1ff96d24eeaf002b1f3ebcdd7bbfe0cb862b95e53f41aada89c99e61`.

## Export scan cost remains unmeasured

Read-only analysis subtracts direct child spans to form a nonoverlapping
table that reconstructs the full 33.914-second body. Snapshot capture costs
9.142 seconds, restore installation excluding audit 6.306, restore checks
excluding nested audit 5.089, and audits 4.290. No repeated proof can be
removed from this evidence. Column/key helpers already cache the frozen
reference schema. Analysis SHA-256:
`112abb22f8be5a7d0462b84f095c9aa3df5d0247b474e71b0d67c83c652cb7bd`.

Source inspection finds two identical typed-row scans per export table,
on the same enforced read-only snapshot; the second scan applies a pure
immutable-field projection. Their share of capture time remains unknown.
Direction `d386c548…` accepts only fixed physical/immutable aggregate timing
inside existing snapshot records, with exact caller and complete 282-call,
141-pair guards. No per-call records, extra scans, cache or production change
is selected. The prior three observer files remain frozen at source
`52574e9a…` for version-1 payload validation.

## Production watch at 02:43 UTC

Nine observed acquire ticks complete with 66 requests; seven ticks make no
request. The latest request-bearing tick finishes at 02:05 UTC. Ten derive
ticks complete with progress totaling 1,099 through 02:42. All observed
cycles report no failure, and both watchdog executions succeed. The latest
alert remains the known expired archive-host warning from 01:35
([receipt](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-0243.json)).
The receipt's typed `zero_request_ticks` count is seven; its free-text limits
incorrectly say eight. This note corrects that wording while preserving the
captured receipt's bytes. No production repair or alarm clearing occurred.

## Fresh unit preparation complete

The first initial controller refuses before allocation because it compares
changing process counters instead of stable guardian identity. PID, birth,
boot and namespace match. Refusal `ba3c9dc8…` remains unchanged; independent
addendum `3332b220…` permits the still-unattempted commands after correcting
that predicate. No preparation command is replayed.

After the first source-only helper exits zero, row 35's external validator
rejects its inherited image labels because its baseline map is unset.
Actual user, command, mounts, two inherited labels plus owner/phase labels,
resources and terminal state match. The failed validation result remains
unchanged. Input audit `eacf7bdc…` initializes every remaining contract;
independent continuation `644e2211…` permits only rows 36 through 146.

All 147 commands finish once by 03:07 UTC. All 24 helpers exit naturally
with code zero and remain stopped. The three fresh volumes contain the
expected 1,689 files totaling 69,548,306 bytes; quiet comparison, source and
target private-file permissions and symlink targets match. No credential
value or private-file hash is exported. Operator `c745d63f…` is sealed as one
root-owned `0444` file under a root-owned `0555` directory. Actor `0e80c8b9…`
is created with eleven mounts, PID zero and no start time. The private RAM
environment comparison passes. Receipt SHA-256:
`9a02ccebe1d8863a77a65628fc51a1eceadcaab0283073a9340c1e1e02a2804b`.
Protected services, guardian, source identity and retained databases remain
unchanged. No database copy or diagnostic execution has occurred here.

Separate execution plan `7acf34a6…` binds that actual cold actor, completed
preparation, old qualified producer and corrected standalone operator.
Execution review is pending. No launch follows from preparation approval.

## Export digest measurement result

Aggregate observer source review `93dd6489…` permits its first normal run.
Job `cb369fbf…`, source `78c2b7bd…`, binding `e9ff072a…`, passes all 3,482
cases, Ruff and mypy but fails timing at 187 seconds. Summary SHA-256:
`2788424c1dd93b3426e471f55f96d385ddcc37598c4e46b91fe565f925d766fd`.
Every phase observer reports quality true. Only client 1/worker `gw2`
captures the ordered pair; all five snapshots report one completed export,
282 normal digest returns, 141 pairs and no nested, foreign or unexpected
caller. Independent measurement audit remains pending.

The changed-source body takes 32.642 seconds. Its three immutable scans
total only 0.244 seconds, 0.75% of the body; both scan roles total 0.546
seconds. Reject scan fusion for this core bottleneck. The first input capture
takes 5.421 seconds with 0.069 owner CPU seconds; the later calls take under
0.090 seconds each. This locates elapsed cost, not its filesystem or flush
cause. Read-only analysis SHA-256:
`7722716ec7b9a2d4b4f9836e755865a98aa90ecf581368a0998f5fbd1e1a93d0`.
No production optimization or unchanged timing retry is selected.

## Routine hold review at 03:13 UTC

The maintained review succeeds at November 12 03:13:21 UTC, with next review
04:13:21 UTC. The original owner, reason, creation time and all twelve
protected services remain unchanged. Hold SHA-256:
`90757c4505768bc62f30fd06c35f1f9524089fa40408ca16a175c8ea35dc4a2a`.

## Corrected unit diagnostic started

Independent execution verdict `cd6c07f3…` accepts the exact plan
`7acf34a6…` and cold actor `0e80c8b9…`. The single foreground launch starts
November 12 at 03:49 UTC. Independent startup review `5862e319…` confirms
the actual process incarnation, private environment equality, all eleven
mounts, network controls and protected boundary. Owner
`046da103604a45e9931a5d58` may allocate only one copy. This is the old
qualified producer with standalone operator `c745d63f…`, not qualification
of the current checkout.

At 04:04:54 UTC the actor is still running, with three databases observed
against two retained private databases. All twelve protected services
remain unchanged; observed OOM counters remain zero. Private PostgreSQL
memory-limit events increase, so no absence-of-pressure claim follows.
No final unit result or graph acceptance exists yet. Keep the 45-minute
freeze, 60-second atomic write and one-hour job bounds unchanged.

## Input capture measurement result

Independent measurement audit `441a2c16…` accepts the preceding export
digest observations and normal context while retaining its 187-second
timing failure. The measured second scans are too small to select fusion.
Source review `53759da0…` instead accepts three bounded owner aggregates
inside existing input-capture records: runtime capture, future-result wait
and path-existence probes. Original caller identity and owner thread bound
the observations; arguments, results and paths are not retained.

The first changed-observer normal job `a6884ec2…` passes all 3,489 cases,
Ruff and mypy but fails timing at 204.16 seconds. Independent measurement
verdict `35c40a30…` confirms source binding, normal membership, natural
weights, caps, observer quality and owned cleanup. Two resource samples
are unknown. The first captures take 1.932 and 1.783 seconds, including
1.849 and 1.679 seconds waiting for futures. Runtime capture takes about
0.049 seconds and path probes under 0.005 seconds each. This does not
identify helper CPU, filesystem or flush cause.

Analysis `d6eec94a…` finds that the observed pair runs on the shortest
client, 190.258 seconds including cleanup, against 196.352 seconds on the
slowest. Removing these waits would not move that slowest client. Stop
this branch: no writer, runtime or path optimization, new diagnostic or
unchanged timing retry is selected. After the run, local `df` reports
about 65 GiB free, below the 80 GiB floor; pause local runs. No retained
packet or unrelated cache is deleted.

At 04:10 UTC the same filesystem reports 109,670,660 KiB available, about
105 GiB, without a deletion by this session. The transient cause is unknown.
The 80 GiB floor is restored; the 110 GiB full-suite admission is not met.

## Production watch at 03:33 UTC

Seven acquire ticks through 03:31 make zero requests and six derive ticks
through 03:28 make zero progress. The latest known request-bearing tick
ends 02:05. The cycles report no failure, but the elapsed idle period
fires the watch rule. The latest alert text remains the 01:35 expired
archive-host warning. No production repair is made in this rehearsal.
The compact [receipt](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-0333.json)
binds the journal captures.

## Delayed hold review and supervisor stop

The 04:00 and fresh 04:06 hold renewals both time out on their installer
transport. Each exact installer later exits naturally with zero; neither
has activated its candidate. Audit `01cdca8c…` confirms the old active
`90757c45…`, new `1cbbf0c4…` temporary file, full installer profile and
protected boundary. Independent review `dc8124c3…` permits only the still
unattempted rename and original receipt finalization. No installer is replayed.

That one rename is launched at 04:12:29 UTC under the pointer lock and an
exclusive fence, with the original 30-second transport bound. Its transport
also times out. The queued exact helper later starts at 04:14:17.769 and
exits naturally with zero at 04:14:18.150. Original expiry is 04:13:21.558;
continuous review validity is therefore not proved, with a gap bounded
between 56.211 and 56.591 seconds. Independent full-profile witness
`26289dcd…` confirms active `1cbbf0c4…`, root `0444`, one link and twelve
unchanged peers. The queued CLI is gone. No rename is replayed.

Receipts-only finalizer `53de96ae…`, reviewed in `2195da42…`, succeeds once.
Both transport timeouts remain explicit and distinct from witnessed Docker
terminal zero. Original postchecks, durable receipts, standard SFTP and
remote hash equality precede atomic pointer completion. The same owner,
reason and creation time remain; next review is 05:06:12 UTC. No scheduler
is started.

The compact [hold receipt](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-040612.json)
retains exact commands, observed helper exits, transport outcomes, hashes
and the expiry gap. The evidence-size check after hold closure passes.

The diagnostic supervisor records a `ValueError` and its generic supervisor,
peer or disk failure reason. Its last sample is 04:13:42, after the old hold
expires and before delayed activation. It records one confirmed controlled
stop, exit 137 with no OOM, finished at 04:15:43 UTC. Supervisor receipt
SHA-256 `222c30d03a11c45558a4471415996ac1fa488079e97caf9ba052716a43fcc32f`
is not an application failure or unit success. The hold-expiry chronology
supports investigation; the generic reason alone does not identify the
failed check. Owned-copy cleanup and final boundary review remain pending.

The collected public operator receipt still names
`freeze_one_new_owned_database`. Capture completes in 4.316 seconds and
the pre-freeze source check in 262.584 seconds. No completed freeze,
destination prelude, acceptance or inventory trial is recorded. This is an
interrupted freeze, not a unit-result witness.

Independent terminal observation at 04:21 confirms actor, supervisor,
process groups and CLI gone, cgroup empty and authorization retired.
Source C696 native-38 UUID/OID/checksum, retained 69, native-import actor,
guardian, policy and twelve protected services match. The exact owner
`046da103604a45e9931a5d58` registry and database OID 5861832 remain; no
client backend is present, only two autovacuum workers. This boundary is
not clean. Review cleanup of only that owned database and registry,
preserving all failed proofs, volumes and retained source databases.

Terminal verdict `cce06103…` accepts those observations while explicitly
failing the diagnostic and owned-cleanup boundary. The compact
[interruption receipt](../../evidence/runtime/postgres-stage2-2026-11-11/unit-diagnostic-99ffb3d1370e-interrupted.json)
binds the actual launch, controlled stop, producer, operator and remaining
owned database. It does not claim a recomputed full source checkpoint hash.

## Production watch through 04:26 UTC

Twelve acquire ticks make three requests in two ticks; ten make none.
The request-bearing ticks end at 03:36 and 04:11. Eleven derive ticks
through 04:23 make no progress. An acquire-idle alert fires at 03:35,
before the next request-bearing tick; the archive-host warning also
recurs then. These observations show limited fetching, not sustained
collection or a repair. The [receipt](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-0429.json)
binds three read-only commands. No production mutation is made.

## Existing phase-cost analysis

Read-only analysis of the same `a6884ec2…` job sums parallel call, setup
and teardown occupancy to 185.844 seconds per sixteen workers. Prologue
and other overhead are additional. Reordering that fixed measured work
cannot meet the 180-second budget. This is elapsed occupancy, not CPU.

The slowest client runs the changed-artifact publication case with 14.860
seconds of fixture setup and 1.990 seconds of owner CPU. Keep the complete
`release_state` fixture and assertions. Select a bounded owner-psycopg-wait
measurement of that setup through the existing diagnostic lifecycle, with
its original 30-second ceiling and unchanged resources. This names another
measured cost, not a filesystem cause, speedup or optimization. Analysis
SHA-256: `5f99b9151c1d30f135d451223778e2312d2b1858e575fedf06caa293a7439ea1`.
Source extension and review precede any diagnostic operation.

## Owned046 cleanup complete

Plan `10c59554…`, SQL `04be81cf…` and independent execution verdict
`6d1fcc0d…` limit cleanup to the exact owned046 database OID 5861832 and
one ownership record. Fresh identity checks precede the ordinary guarded
DROP, which exits zero without FORCE or client signals. Separate reads
confirm database and activity absent, retained databases unchanged and
the ownership record intact.

The existing removal helper's cold comparison refuses before start: Docker
omits a false `ReadOnly` value in `HostConfig.Mounts`. Independent proof
finds that sole representation difference, with public `RW=true` and the
full expected profile otherwise equal. Preserve the refusal. Continuation
`8b6544ac…` permits only fresh guards and the existing never-started helper.
It exits naturally with zero; the ownership record is absent. No earlier
step is replayed.

Independent postcheck `47aeeda9…` verifies the actual 137 record files and
fresh protected boundary. The private catalog returns to its five retained
databases. Actor, groups, cgroup and authorization are gone. All three
volumes, 24 preparation helpers, two cleanup helpers and failed operator
receipt remain. Server free space is 579,559,403,520 bytes. The compact
[cleanup receipt](../../evidence/runtime/postgres-stage2-2026-11-11/owned046-cleanup-20261112.json)
closes only scratch cleanup; the interrupted diagnostic remains failed.

## Maintained hold recovery source and first renewal

The maintained command now records a private per-pointer operation fence,
each dispatch intent and each transport outcome durably. Status reconciles
only exact owned helper termination and active-file state. A hash-bound,
consumed-once review can continue only unattempted steps. Attempted unknown
operations remain blocked. Receipt-copy completion needs exact remote
regular-file metadata and hashes before pointer advancement, including when
transport itself timed out. Closed legacy packets remain readable.

All 45 focused tests pass, plus Ruff and mypy. Central formatting preserves
the syntax trees. Independent source verdict `461504a5…` accepts source
`6993365f…`, its tests and public CLI wiring. This does not authorize an
unbound continuation or qualify the whole current checkout.

The first routine use succeeds from 04:55:53 UTC, with next review
05:55:53 UTC. Both helpers finish naturally with zero; all twelve protected
services remain unchanged. Receipt readback precedes pointer completion.
This renewal proves continuous validity and does not erase the earlier
gap. The compact [receipt](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-045553.json)
binds the actual command journal and source verdict. No scheduler starts.

## Publication-fixture setup waits

Independent source verdict `c0824915…` accepts the fixed setup-phase
extension. Execution verdict `78df49a2…` binds fresh packet `4d4e0782…`,
source `c2cb36ba…` and binding `64693233…`. The single native diagnostic
passes its full case, ordinary call assertions, phase quality and owned
cleanup. Result SHA-256:
`0e3271ab883d29d8f6c01e0469fdc5a6ee21d511953677f6ad58f6dee68e4d69`.

Setup measures 5.117606 seconds and 1.696755 owner CPU seconds. Its 6,187
outer owner-driver waits total 0.452471 elapsed and 0.092864 CPU seconds;
the wrapper is restored. This leaves 1.603891 owner CPU seconds outside
those waits. It does not explain the parallel run's 14.860-second setup or
identify filesystem, helper or server work. Select one existing CPU-mode
profile of the same setup, with a new packet and unchanged ceilings.

Root's first review-hash command names a nonexistent receipt filename.
The correct written execution approval already precedes launch; afterward
its actual `independent-execution-review.json` hashes to `78df49a2…`.
The diagnostic runs once. Preserve that verification-order exception;
it supplies no authority for another execution.

## Year 2026 failure bounds

Held-source analysis `a79657be…` links the failed graph's nonempty discovered
dependencies to the completion-dispatch boundary. Transform, hint work and
the process pending-work deletion returned; the wrapper appended the
dependency digest immediately before calling the original completion.
Its original body entry and return remain unproved. Group certification,
final labeling and deadline handling during deliberate rollback also
remain possible boundaries. The failed-result serializer omits all labels,
so a missing `found` field cannot distinguish those paths.

The retained baseline's 15,140 year rows are not a trial count or timing
cause. This verification route is inline inside one absolute 60-second
outer transaction, unlike staged `derive_one` work. No cause, optimization
or year execution is selected. Inventory remains first. Analysis SHA-256:
`a79657be83703f8319a47e32a2804072445edc420b3a3cc53679103e004e0c1e`.

## Publication-fixture setup CPU

Fresh CPU packet `21d5a546…` and execution review `95e88aea…` produce one
passing case with correct setup binding and owned cleanup. Result
`b4ff9c83…` and terminal verdict `b0f93ab4…` record 2,498 profile entries,
including 281 suspension exclusions, with no omissions. Setup measures
7.025578 elapsed and 3.659540 owner CPU seconds, including instrumentation.
Ranked Python-frame self times are not cumulative or a speed comparison.

Flat `durable_write` totals 578 calls and 0.440413 self CPU seconds, but
includes archive and manifest callers. The serial `_published_fixture`
loop exists; its exclusive elapsed cost remains unmeasured. Import parsing
is already cached and production capture already has eight writers.
Analysis `26a94309…` selects no optimization. Add one fixed-callsite owner
aggregate in the unchanged normal setup context before choosing a change.
One resource sample is unknown; no wall-cause or global pressure claim follows.

## Production watch through 05:28 UTC

Thirteen acquire ticks make no requests and thirteen derive ticks make no
progress. The latest known request-bearing tick ends 04:11, over an hour
before this read. All observed cycles report no failure; that does not
establish progress. The latest alert text remains 03:35. The compact
[receipt](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-0529.json)
binds the three read-only commands. No cause or repair is established.

## Routine hold reviews at 05:39 and 06:07 UTC

The same maintained `6993365f…` hold protocol renews review to 06:39:33
and then 07:07:56 UTC. Both operations record six zero exits, exact owned
installer and rename terminal identities, unchanged twelve protected
services, and copied receipt metadata/hash verification before pointer
completion. Both activations finish before the prior expiry; no scheduler
starts. The [05:39 receipt](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-053933.json)
and [06:07 receipt](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-060756.json)
bind the raw plans, commands, results and step journals. The hold hash
chains `29f28eb8…` → `9748bd81…` → `5a99b43d…`.

## Fixture persistence and the next measured case

The eight-writer change affects only `_published_fixture` member
persistence in `tests/test_correction_only.py`; source `c0306bf5…` is
accepted by review `9f1e5f3c…`. Each original durable write and file/directory
fsync remains, every submitted worker joins before the owner manifest and
DB acceptance, and failure cannot publish early. No production persistence
API, case selection, caps, placement or shared mutable state changes.

The next normal check passes all 3,556 cases and Ruff/mypy but takes
187.74 seconds, failing the 180-second gate. Application source remains
`b98363ef…`; test binding is `4e1052dc…`, summary `4bfaa182…`, independent
measurement audit `67e0b930…`. Target setup measures 6.487234 wall and
1.361802 owner CPU seconds; its owner-only write observation now sees the
manifest and excludes 572 worker calls. This is not a controlled speed
comparison. Raw source and audit records remain under
`/tmp/swingset-stage2-fixture-pool-source-review-20261112/`.

The existing CPU/waits command gains only the fixed `failed-build` CALL
case, preserving its two real preparation attempts and assertions. Source
`a8a445cd…` keeps its AST through formatting; 133 focused cases pass. Packet
`688158be…` is separately reviewed under the unchanged 30-second measurement
and 120-second cleanup ceilings. No optimization is selected from admission
of that case, and current normal timing/full qualification remains open.

## B818 cold preparation and actual startup

The [cold preparation receipt](../../evidence/runtime/postgres-stage2-2026-11-11/cold-unit-preparation-b8180e227f16.json),
SHA `79dc8126…`, binds all 147 command indices. Three false observer halts
(preallocation, row 33 and row 64) remain preserved; reviewed continuations
execute only remaining commands, without replay. Preparation creates no
copy and performs no trial.

At 06:21:41 UTC, actor `cc3bc375…` starts with PID 1287617 and birth ticks 986094050. Startup review `3726b094…` accepts that boundary and the sole
allocated owner `f6c944fa…`, database OID 6309804. It proves neither freeze
completion nor a trial result. The old qualified image and LINKER 9 context
remain separate from current source qualification. Raw startup observation
and review remain under
`/tmp/swingset-stage2-unit-diagnostic-cold-draft-b8180e227f16-v1/execution-v1/`.

## Production watch through 06:33 UTC

The [06:34 receipt](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-0634.json),
SHA `ee535538…`, records fourteen acquire ticks through 06:31 with zero
requests and fourteen derive ticks through 06:33 with zero progress. The
last known request-bearing tick still ends 04:11. New alerts report loop
idleness at 05:35 and a failing host at 06:05. Successful tick completion
establishes no fetching progress, cause or repair.

## Hold witness ordering refusal at 06:39 UTC

Renewal packet `20261112-063454` records a step-two transport timeout.
The exact installer `3fac8730…` later exits zero. Maintained status first
records unknown, then reconciles that terminal witness. Review `bc6d0ef5…`
permits only remaining steps 3–6. The attempted continuation refuses before
its dispatch fence or any new step intent; the active hold remains `5a99b43d…`,
valid until 07:07:56 UTC. No installer or activation is replayed.

Independent finding `1957c59e…` proves the refusal comes from witness
hashing: Docker reverses the same two complete mount objects. Sorted
objects reproduce the pinned terminal profile hash exactly. The checker
already validates sorted mounts but hashes their original array order.
Select only canonical ordering before hashing, retain the old proof and
timeout, and require source review plus a fresh remaining-only verdict.
Raw findings remain in `/tmp/swingset-stage2-hold-review-20261112-063454/`.

## Reviewed continuation of the 06:34 hold renewal

The [06:34 renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-063454.json)
records installer and rename SSH timeouts as unknown transport exits;
each exact owned helper later proves natural exit zero. The first review
`bc6d0ef5…` refuses before dispatch because Docker reverses the two mounts.
Finding `1957c59e…` proves every other projected field unchanged. Corrected
source `81772a44…`, accepted by `8eb92a74…`, sorts only validated mount
order before hashing and preserves the original `fa0d627d…` terminal proof.

Fresh review `8aa77f05…` permits only steps 3–6; after the rename timeout,
review `d6cc4684…` permits only step 6. Neither helper is replayed. Actual
activation finishes 06:49:07.452 UTC, before the old 07:07:56 expiry.
Receipt-copy metadata/hash readback then precedes pointer completion at
`68166df8…`, with next review 07:34:54 UTC. All twelve protected services
remain unchanged. No scheduler starts or other operational authority follows.

## Failed-build CPU attribution

Packet `688158be…` completes once with one passing full case and accepted
result `b924053b…`; independent terminal audit `25a201ff…` verifies source,
quality, ceilings and owned cleanup. Instrumented CALL body measures
5.023349 wall and 4.990216 owner CPU seconds. All 882 entries fit the bound:
768 regular entries are ranked, 114 suspension entries excluded, none
omitted. This isolated profile is separate from normal 4.726900 wall and
4.446125 owner CPU seconds; it establishes no normal timing pass.

Analysis `de0f851e…` finds 2.972297 exclusive CPU seconds in import lookup
machinery, including 250,548 `find_spec` calls. The flat report cannot name
the requesting module or caller, so a DuckDB optional-import cause remains
unproven. Inspect the existing retained `/tmp/diagnostic-cpu.pstats` caller
edges under a separate reviewed read before selecting a new measurement
or optimization. Raw result, terminal audit and analysis remain in their
`/tmp/swingset-stage2-failed-build-cpu-20261112-0626/` and
`/tmp/swingset-stage2-failed-build-cpu-analysis-20261112/` packets.

## Routine hold renewal at 07:14 UTC

The [renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-071455.json),
SHA `1b74adce…`, records seven successful steps under reviewed source
`81772a44…`. Both helpers finish naturally with exit zero. Activation at
07:15:06 UTC precedes the old 07:34:54 expiry; verified receipt copies
precede the pointer update to `d06b03e4…`, due 08:14:55 UTC. All twelve
protected services remain unchanged. No scheduler starts.

## B818 inventory diagnostic terminal outcome

The [operation receipt](../../evidence/runtime/postgres-stage2-2026-11-11/inventory-unit-diagnostic-b8180e227f16.json),
SHA `85f1d606…`, records natural exit zero at 07:10:45 UTC. The corrected
prelude and ordinary input acceptance complete with the original strong
108,390-unit binding. The inventory trial still exceeds its unchanged
60-second atomic deadline; rollback preserves its output and run rows.
Final source identity remains `00c831a4…` and automatic cleanup removes
only owned database `f6c944fa…`. Sole terminal review `445304ea…` confirms
its absence, empty registry, retired authorization and unchanged retained
databases, guardian, native importer and twelve protected services.
Diagnostic completion establishes no inventory match or graph proof.

## Existing CPU caller edges and the pandas lookup

The fixed read of the existing pstats file passes terminal review
`604af7de…`: 101,616 bytes, SHA `2bcb6800…`, one read-only helper, no new
profile. Its local cold comparator first rejects LogConfig key order;
that false observation remains preserved, no start had occurred, and a
complete unordered-map comparison permits the helper's sole start.
Caller analysis `258fce7c…` traces 22,776 of 22,808 import calls to scratch
DuckDB reconstruction sites. Generated-code identity collisions explain
the 799 pstats keys versus 882 getstats entries; no unique key is missing.

The reviewed synthetic probe runs once in the same image, with one
in-memory database and ten SQL calls. Output `97852b3c…`, terminal review
`52d7da06…`, records 40 failed pandas requests with the module absent
before and after. Hooks restore, counters fit, and no ceiling fires.
This names the repeated request; it establishes no normal-core speedup.
DuckDB 1.5.5's [scalar conversion](https://github.com/duckdb/duckdb-python/blob/v1.5.5/src/duckdb_py/native/python_conversion.cpp)
checks pandas missing-value objects before primitive scalar types. Its
[import cache](https://github.com/duckdb/duckdb-python/blob/v1.5.5/src/duckdb_py/python_import_cache.cpp)
leaves a failed optional module null, permitting another attempt. Pinned
source hashes and the dev-only dependency proposal remain in scratch
analysis `a872c1c1…`; no package change is selected yet.

## Production watch through 07:28 UTC

The [07:30 watch](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-0730.json)
records thirteen acquire ticks through 07:26 with zero requests and
thirteen derive ticks through 07:28 with zero progress. The latest known
fetch still ends 04:11. Alert text remains at 06:05; this establishes no
repair or liveness. All three read-only commands exit zero.

## Routine hold renewal at 07:57 UTC

The [renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-075706.json),
SHA `2b3fcb98…`, records seven successful steps under reviewed source
`81772a44…`, with no timeout. Both helpers finish naturally with exit zero.
Activation at 07:57:16 UTC precedes the old 08:14:55 expiry; receipt
metadata and hashes verify before the pointer moves from `d06b03e4…` to
`14f13e6c…`, due 08:57:06 UTC. Hold identity and all twelve protected
services remain unchanged. No scheduler starts.

## Dev dependency trial and the two normal failures

Direction review `32f34185…` accepts a real dev-only pandas trial, without
fake imports, preloading or a timing waiver. Resolver review `c1b79f6d…`
verifies pandas 3.0.6 is the only added package; all old package records
remain identical. The lock's nine marker regions refine its original two
without changing their coverage. Production installation uses `--no-dev`,
but the lock and dependency-image identity still change context. Both
affected full build cases pass. This remains an unqualified trial.

Job `08851ca6…` selects 3,574 unique cases: 3,573 pass, one architecture
guard fails. Its 215.62-second check also fails timing. Audit `a42b2a19…`
confirms native image `c92bfcd1…`, unchanged natural scheduling and actual
cleanup. Cold image setup remains charged; no residual is relabeled as a
measured build duration. The failed guard finds two observer registration
references, not an extra execution route. Its source-only review had
missed that constraint; the actual failure supersedes that conclusion.

The correction recognizes only the two exact passive patch-registration
shapes and counts all references before collapsing edges. Four direct-call
and escape mutations remain forbidden. A real process/read-recording and
rollback witness passes. Both affected files pass 79 tests; review
`1fdcfe70…` accepts this correction and one changed-test normal check.

Job `bc4fe1fb…` passes all 3,579 cases, Ruff and mypy. It still fails at
193.55 seconds (runner 193.451623); audit `a30bab9e…` confirms five genuine
additions, no removed cases, all ordinary limits and complete owned cleanup.
Application source is `73fa0633…`, test binding `186ea5e7…`; image `c92bfcd1…`
and dependency key `3ed9213e…` are unchanged. No timing retry is selected.

## Actual critical-worker checkpoint target

Analysis `2f2793d8…` identifies client 0/gw1: 178.288829 phase seconds,
43.688674 owner CPU seconds and 0.255829 between-phase gaps. Its 27 checkpoint
calls account for 85.742701 wall and 18.728754 CPU seconds. The previously
observed large empty-restore cases are both on gw2 and finish well before
the tail; analysis `1680a3e4…` does not justify optimizing them. Cohort,
digest and input-cache branches remain closed.

The selected checkpoint public-verification case costs 6.159240 wall and
1.136980 owner CPU seconds on gw1. It lacks major-phase slices. Extend only
the existing fixed-case observers for its real state/create/restore/verify/
activate aliases, with driver waits and full assertions. A source-reviewed,
bounded isolated attribution will establish those costs; it cannot claim
normal timing or select an optimization without further analysis.

## Routine hold renewal at 08:44 UTC

The [renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-084401.json),
SHA `fa6e0013…`, records seven successful steps under reviewed source
`81772a44…`, with no timeout. Both helpers finish naturally with exit zero.
Activation at 08:44:11 UTC precedes the old 08:57:06 expiry; receipt
metadata and hashes verify before the pointer moves from `14f13e6c…` to
`9edb0725…`, due 09:44:01 UTC. Hold identity and all twelve protected
services remain unchanged. No scheduler starts.

## Checkpoint attribution and closed cache branches

The fixed public-verification case passes the waits attribution and owned
cleanup (`27325d51…`, review `1cc76515…`). Its call takes 2.136577 wall
and 1.180854 owner CPU seconds. Creation dominates at 1.269900 wall seconds;
the observer adds no skipped assertions or safety-pass changes.

The child attribution also passes (`64a6e9dd…`, review `6e2156ee…`).
Snapshot capture takes 0.660973 wall and 0.536558 owner CPU seconds;
backup checking takes 0.608988 wall and 0.189581 CPU. Only 0.007050 wall
seconds remain in creation outside those children. Parent spans include
their children. Driver wait calls are not SQL statement counts. Neither
measurement establishes normal-core speed or an optimization.

The existing CPU mode now accepts this exact case while requiring its
complete v5 phase payload. All 142 changed-file tests, Ruff and mypy pass.
The full-case attribution passes accounting, payload and cleanup
(`da8fabbf…`, review `960cab71…`). Its instrumented call takes 4.506177
wall and 3.600069 owner CPU seconds. Of 2,949 profile entries, 189
suspension frames are excluded; their active CPU is unknown. The remaining
self times total 2.517767 seconds. These are whole-call observations, with
profiling overhead, not snapshot-specific or normal-run costs.

One reviewed read captures the existing raw profile as 289,516 bytes,
SHA `cb165a15…`. Terminal verdict `013e8ca8…` accepts it with an ordering
deviation: complete volume identity was captured before reading, but its
equality assertion ran afterward. All expected fields match. Future reads
still require that assertion before execution; this is no replay waiver.

Caller analysis `b16ed43e…` finds only 29 ms of inclusive instrumented CPU
in 82 dispatch digests. Close dispatch caching. Existing runtime and import
content caches work. A larger lexical edge is verification's 2,880
`relative_to` calls at 0.251605 instrumented CPU seconds. Select one bounded
unprofiled pure-path comparison before any repository rewrite. Preserve
traversal, physical resolution, containment and all digest checks; do not
hoist root resolution. Synthetic timing can reject a candidate but cannot
qualify the normal core gate.

## Disk admission remains blocked

Read-only cache audits find no suitable inactive 8 GiB cleanup candidate.
BuildKit has about 1.73 GiB of nonshared reclaimable entries, and the
one-level installer inventory totals about 1.88 GiB. Their safety is not
established and neither is proposed for deletion. Existing active caches
and the previously consumed deletion approval remain excluded. No new
deletion occurs. About 104 GiB free remains below 110 GiB full admission.

## Routine hold renewal at 09:42 UTC

The [renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-094238.json),
SHA `bc246b78…`, records seven successful steps under reviewed source
`81772a44…`, with no timeout. Both helpers finish naturally with exit zero.
Activation at 09:42:48 UTC precedes the old 09:44:01 expiry. Receipt hashes
verify before the pointer moves to `5716b08f…`, due 10:42:38 UTC. Hold
identity and all twelve protected services remain unchanged. No scheduler
starts.

## Production watch through 09:43 UTC

The [09:45 watch](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-0945.json)
records one request and one acquire progress at 09:41 after nine observed
idle acquire ticks. Ten derive ticks through 09:43 make no progress. The
latest alert is an 08:35 host failure. Both read-only commands exit zero.
This updates the earlier idle finding without establishing pages fetched,
sustained recovery, a cause or a deployed repair.

## Pure lexical comparison closes without a rewrite

The reviewed probe runs once in image `c92bfcd1…` at 09:51 UTC. Output
`88e77ca0…`, 1,064 bytes, records all 783 equivalence cases and 631
refusals passing in 0.150280 seconds. Review `3b1687bd…` verifies the
actual bounded actor, natural exit zero, no OOM, exact read-only script
mount and private outputs. The stopped helper and scratch outputs remain.

Three alternating rounds save 8.55–8.81 microseconds per warm synthetic
call. This does not establish a material contribution to the 13.55-second
normal-core gap; no suite-count extrapolation is justified. Close the path
branch without a repository rewrite, another profile or a normal timing
retry. The original lexical operation and every physical check remain.
Stage 2 still needs the two failed units resolved and current qualification.

## Authorized Gradle cache cleanup at 15:56 UTC

The owner asks to delete the Gradle caches. A fresh independent check first
holds deletion for an open Gradle daemon; Gradle reports it idle and its
ordinary stop succeeds. Fresh no-use checks and review `55b56e57…` accept
only the exact cache directory. The command runner rejects the force form
before dispatch; the non-force removal exits zero. The 6.39 GiB cache is
absent and every sibling identity remains unchanged. The [receipt](../../evidence/runtime/postgres-stage2-2026-11-11/gradle-cache-cleanup-20261112.json),
SHA `e5d3958f…`, separates cache allocation from concurrent filesystem space
changes. About 109.4 GiB is free, still 0.55 GiB below full admission.

## Retired October rollback copies removed at 17:19 UTC

The owner explicitly retires both old database copies. Fresh metadata,
no-use checks and independent review `46168e03…` confirm their exact scope.
The newer schema-37 source on Rhuidean still hashes to `ae3bbcd1…`, matching
both original full-validation receipts; all newer source files and transport
remain. The exact non-force removal exits zero. Both October files are absent,
all three offload siblings retain their identities, and about 208 GiB is free.
The [receipt](../../evidence/runtime/postgres-stage2-2026-11-11/old-database-copy-cleanup-20261112.json),
SHA `91de35d2…`, records 106.21 GiB allocated and the separate observed
free-space change. One current-source full qualification is now admitted;
the historical core timing failure remains open.

## Rehearsal review expired during stopped work

The last review expired at 10:42:38 UTC. The ordinary renewal refuses before
opening an operation. Maintained status at 17:15 checks the unchanged protected
services and exact old hold, then reports `active_fresh=false` and
`continuation_allowed=false`. No server actor starts. Preserve this expiry gap;
reopening requires its own explicit, reviewed protocol. Local source-bound full
qualification does not need a server actor and can proceed independently.

## Current-source full qualification starts at 17:22 UTC

The documented native full command starts once with four workers and all core
and extended cases. Source `732a0b96…`, input binding `35a49cdf…` and all
956 inputs are captured before launch. PID and process group 9813 own the
run; JUnit output and raw logs stay in private scratch. About 208 GiB free
passes admission, with the 80 GiB floor still required. The result remains
pending. No source changes are permitted during the run; docs and receipts
are outside its bound input map. The earlier normal core still fails timing.

## Production watch through 17:23 UTC

The [bounded watch](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-1725.json),
SHA `e3f32abb…`, finds ten acquire ticks through 17:21 with zero requests and
ten derive ticks through 17:23 with zero progress. Both read commands exit
zero, but that establishes no liveness. At 16:35 the watchdog reports 79.5 GB
of counted state over its 50 GB cap, disabled remote backup upload, local set
`set_20261112T040049Z` and 632 withheld partitions. The independently retained
November 5 source still hashes correctly; no current-set integrity or new
restore drill is established. No production state or configuration changes.

## Full qualification passes all 5,172 cases

The [full receipt](../../evidence/runtime/postgres-stage2-2026-11-11/current-source-full-20261112.json)
records native CLI exit zero and 5,172 unique JUnit case identities, all
passing, with no errors or skips. Pytest reports 1,273.33 seconds. Source
`732a0b96…`, binding `35a49cdf…` and all 956 inputs remain exact before and
after. The owned group is absent; no case/copy databases or clients remain.
All preexisting ledger identities are preserved, including unknown-endpoint
`9e6…`; six new empty lock files are harmless. Independent terminal review
`ceeeb76b…` accepts those facts. Four saved monitoring samples stay above the
80 GiB floor; they do not establish a continuous minimum. About 177 GiB is
free afterward. Raw reports and temporary artifacts stay in scratch.

This completes full qualification for that source, not the separate core
timing gate. The two failed graph units and expired server hold still block
stage 2. The explicit hold-reopen draft remains unexecuted in scratch;
applying it will create a different source binding requiring its own checks.

## Explicit expired-hold source checked

The reviewed draft is applied after the full run's post-binding. Only the
hold helper, CLI forwarding and existing hold tests change. Public status
regressions preserve exit 1 while the installed hold is expired, even when
the reviewed administrative continuation is eligible; verified activation
changes that status honestly. A local annotation and import order are fixed.
All 64 focused tests, Ruff, mypy across 376 files and formatting pass.

The [new-source core receipt](../../evidence/runtime/postgres-stage2-2026-11-11/expired-hold-source-core-20261112.json)
records 3,608 passing cases on source `5a2a3814…`, binding `cee1d102…`, but
207.68 seconds still fails 180. Review `979a612e…` verifies exact assignment,
caps, source binding, actual cleanup and the preserved unknown ledger.
Weights `2ce4c6a2…` are the ordinary runner's automatic update from the previous
normal result, not a manual timing change. No unchanged retry is selected.

Actual-source review `d448f6f1…` accepts the control protocol, not a runtime
operation. At 18:01 the tested command prepares a fresh local reopen packet
after exact closed-prior and protected-service checks. It mutates nothing on
the server. Its fixed interval ends 19:01:40 UTC; the old 10:42 expiry remains
explicit. The separate administrative operation still needs concrete review.

## Reviewed reopening and routine renewal complete

The [explicit reopening](../../evidence/runtime/postgres-stage2-2026-11-11/expired-hold-reopen-20261112-180140.json)
finishes activation at 18:07:58 UTC. Independent terminal review `fc3c9b80…`
accepts actual CLI exit zero, seven zero transport exits, both exact owned
helpers naturally exited and the three copied receipts verified before the
pointer advances. The unchanged owner, reason, creation time and protected
12 services remain bound. The old 10:42:38 review expired 26,720.629642
seconds before activation; continuous validity is explicitly false. The
prepared interval is not redated and ends at 19:01:40 UTC.

The separate [18:32 routine renewal](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-183202.json),
SHA `b0d0a3d9…`, activates at 18:32:13 UTC, before that interval ends.
Independent terminal review `388a4b…` confirms seven zero transport exits,
no timeout or retry, exact helper exits zero with PID zero and no OOM,
unchanged protected services and copied receipt hashes. Hold `4b0264bc…`
now has review at 19:32:02 UTC. This routine interval is continuous; it
does not erase the earlier expired gap. Neither administrative receipt
qualifies a worker, cold preparation, pipeline, migration or publication.
The current 3,608-case core remains a 207.68-second timing failure; the
5,172-case full pass qualifies only source `732a0b96…` and binding `35a49cdf…`.

## Bounded production watch at 19:07 UTC

The [read-only watch](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-1907.json),
SHA `ebacb8a7…`, records eight acquire summaries through 19:01 with zero
requests and eight derive summaries through 19:03 with zero progress.
All report `failed=false` and no error; both read commands exit zero with
empty stderr. The 17:35 idle alert reports no acquire progress since 16:21;
the 18:05 Wayback alert repeats seven server errors with an old November 6
deadline. The prior 79.5 GB size alert, paused upload and 632 withheld
partitions remain. A partial 100-line journal cannot prove a continuous
fetch gap or its cause. No repair or new backup-integrity claim follows.

## Routine review renewed and inventory diagnostic started

The [19:48 routine renewal](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-194848.json)
finishes naturally with all seven transport exits zero. Independent terminal
review `feb21c18…` verifies both exact helpers exited zero with PID zero,
no OOM or restart, unchanged protected services and all three receipt hashes
and metadata before pointer finalization. Activation at 19:48:59 UTC precedes
the prior 20:09 review. Hold `9a25583d…` ends at 20:48:48 UTC, preserving
the old expired gap and unchanged owner, reason and creation time. This
administrative receipt grants no pipeline qualification.

The [cold preparation](../../evidence/runtime/postgres-stage2-2026-11-11/cold-unit-preparation-d21bfe137a3d.json)
completes all 147 stock rows, with 24 helpers naturally exited and a fresh
unstarted actor. Root launches the separately reviewed foreground diagnostic
once at 19:56:53 UTC. Independent [startup review](../../evidence/runtime/postgres-stage2-2026-11-11/inventory-unit-diagnostic-d21-startup.json)
`c45be3ad…` confirms actor `1bd4ed0b…` running from 19:56:54 UTC, its exact
authorization and egress limits, the protected boundary and native-38 source.
The actual supervisor is PID/group 2438424; root's SSH tool session 27467
is a different identity. New owner `ab603d2197fd4982ba1c4f23` has an empty
registry at observation, and the source has one legitimate prefreeze read
client. Producer `8aae03bf`/image `346f30a4` is unchanged; observer `70f85704`
measures capture, desired state, inventory preparation and post-link hints.
No freeze completion, inventory result or final cleanup is claimed yet.

## Bounded production watch at 20:11 UTC

The [read-only watch](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-2007.json)
is recorded at the actual clock time, 20:11:16 UTC. Both completed SSH reads
exit zero with empty stderr. The partial 100-line journal contains eight
acquire ticks through 20:06:02 with zero requests and nine derive ticks
through 20:08:25 with zero progress; all report no failure or error. The
eight-line alert tail has exactly the same hash as the 19:07 watch. Its
latest entry remains the 18:05 Wayback failure; the 17:35 idle alert and
16:35 state-size, disabled upload and withheld-partition alerts persist.
This establishes no continuous fetch gap, cause, recovery, current backup
integrity or restore result. No production repair follows. Maintenance
item 44 keeps the accounting investigation separate from this observation.

## Hold renewal reconciled without replay

The [20:19 renewal](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-201956.json)
keeps its original step-two `TimeoutExpired` transport with unknown exit.
Status `fc43273c…` proves exact installer `112bcf76…` naturally exited zero;
the remaining-only review `b1ec6502…` permits steps 3 to 6. Their maintained
resume exits zero, with the installer never replayed. Both exact helpers
have terminal exit zero, PID zero and no OOM or restart; only the other six
transports are zero. Independent terminal review `a26b7c64…` accepts those
distinctions, the protected services and the copied receipt readback.
Activation at 20:25:26 UTC precedes the old 20:48 review. Hold `4196d386…`
ends at 21:19:56 UTC, with unchanged owner, reason and creation time. Runtime
chain `a34cf1ea…` binds the renewal to the existing diagnostic; neither this
administrative result nor its continuous interval proves a unit result.

## Output-pack SQL guard owner measurement

The [isolated measurement](../../evidence/runtime/postgres-stage2-2026-11-11/sql-guard-measurement-f541cb413f6243daa1fa9d2d.json)
passes the whole fixed SQL-guard case on source `fc7f5f8c…`, with 956 stable
bound paths. Its body uses 6.620495509 wall and 6.619374767 owner-CPU seconds.
The repeated function-naming child runs 11,214 times, using 5.353911613 wall
and 5.349946095 owner-CPU seconds. Global parse and naming each run 376 times
(0.467937517 and 0.646363007 wall seconds); partition parsing runs once
(0.006126324 wall seconds). These inclusive children overlap their parents;
they are not pure membership costs and cannot predict normal-core savings.
Quality, owner and hook-restoration checks pass with no errors. Independent
terminal review `ae7285d4…` confirms actor absence, owned-profile cleanup,
retained volume and preservation of unknown inode 303784487 without reading
its ledger body. The 30-second measurement and 120-second cleanup bounds
are unchanged. No optimization is applied; the normal 207.68-second timing
failure remains.

## D21 acceptance failed before the inventory trial

The [failed diagnostic](../../evidence/runtime/postgres-stage2-2026-11-11/inventory-unit-diagnostic-d21-failed.json)
ends with natural actor exit one at 20:37:10 UTC, PID zero and no OOM.
Root's foreground session 27467 exits one; the supervisor finishes after
2,421.82 seconds. Capture, the exact source check, fresh freeze and full
108,390-unit/D58 prelude return; destination checkpoint `5039effb…` and
input binding `1ffa96ed…` match. Normal acceptance then raises
`WriteDeadlineExceeded` after 60.593217 wall / 12.689127 owner-CPU seconds.
No inventory trial or new inventory spans run. The 177 coarse pending-work
calls observe 28.514006 wall / 1.643885 owner-CPU seconds; watches and reads
are also coarse outermost connection families, not precise SQL attribution.
They omit raw transaction/commit calls and establish no server-CPU,
pressure or coordinator cause.

Independent terminal review `6831c3a3…` confirms owner
`ab603d2197fd4982ba1c4f23` has no database/comment, registry, sessions,
process groups, cgroup or active authorization left. The retained five-DB
catalog, C696/69 native-38 identity/checksum metadata, guard, protected
services and current `4196d386…` hold remain bound. Full source post-hashing
does not run after the acceptance exception; small metadata and the source
read-only protocol cannot be presented as that full hash. The independent
cleanup observation also does not turn the operator's incomplete receipt
into a completed diagnostic. Input acceptance must finish within its normal
bound before a new inventory trial is possible. No unchanged replay or
actor restart is selected.

## Core 695 prologue failed before main

The [latest attempt](../../evidence/runtime/postgres-stage2-2026-11-11/core-695dcf689a704248bb886fb1-prologue-failed.json)
on source `4413d9df…`/binding `e0bc634e…` fails one prologue case in 22.93
seconds. The healthy control still runs under the test's injected 30 ms
deadline after its deliberately slow call. No main client or main case is
selected; `within180=true` on this aborted summary is not qualification.
Ruff and mypy over 376 files pass. Independent terminal review `e27eb4f0…`
confirms owned cleanup, no remaining case/copy clients or locks and the
unchanged unknown-ledger inode. The correction is confined to the injected
timer's test scope; production 60-second writes and the 180-second core
gate remain unchanged. The last complete 3,608-case run still fails timing
at 207.68 seconds. No timing saving follows from the aborted attempt.

## D3 core correctness passes; timing still fails

The [complete normal check](../../evidence/runtime/postgres-stage2-2026-11-11/sql-guard-core-d3a1116374484245a71cc59f.json)
passes all 3,666 cases on source `4413d9df…`, binding `493d0de6…`, with 956
stable bound paths. Its prologue passes one case; the four clients pass
855, 801, 918 and 1,091. No case fails, errors or skips, and all five phase
observers have quality true. Ruff and mypy over 376 files pass. The runner
takes 250.587448500 seconds and the wrapper prints 250.69; default check
exit one is the unchanged 180-second timing failure, not a correctness pass
turned into timing qualification. Independent terminal review `8d1d0fea…`
confirms exact natural measured weights `18e94246…` from the earlier `0def`
result, stable source, owned cleanup and the preserved unknown-ledger inode.
Raw reports and samples stay in scratch. No normal-core recovery or saving
is inferred, and the failed `695` prologue is earlier history.

## Routine hold review extended to 22:02 UTC

The [21:02 routine renewal](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-210236.json)
completes with CLI exit zero, all seven transports zero and both exact owned
helpers naturally exited with PID zero, no OOM and no restart. Independent
terminal review `f4e31f9d…` confirms root-owned mode-0444 single-link hold
bytes, copied receipt readback, unchanged protected services and unchanged
owner, reason and creation time. Activation at 21:02:48 UTC precedes the
prior 21:19 review. New hold `959fb11e…` ends at 22:02:36 UTC; continuous
validity preserves, rather than erases, the historical expired gap. D21
remains terminal and failed. No new actor, SQL, cleanup or pipeline authority
follows from this administrative renewal.

## Production watch adds budget and stale-fetch alerts

The [21:25 read-only watch](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-2125.json)
is recorded at 21:25:51 UTC. Both completed stock SSH reads exit zero with
empty stderr. Its bounded 100-line journal contains eight acquire ticks
through 21:21:02 with zero requests and nine derive ticks through 21:23:27
with zero progress; all report no failure or error. Compared with the
earlier watch, the alert tail adds a 20:35:54 Wayback failure and one
request-budget guard, then a 21:05:53 stale-fetch alert. The latter reports
two hours without requests and a newest stored page five hours old. Those
are alert statements, not independently established continuous coverage.
The older size, paused-upload and withheld-partition notices persist. No
cause, recovery, integrity result or repair is claimed; hold `959fb11e…`
is untouched.

## Isolated publication-safety CPU diagnostic completes

The [fixed local diagnostic](../../evidence/runtime/postgres-stage2-2026-11-11/unlanded-publication-cpu-07f6e8cf72104a079713ca01.json)
passes the complete unlanded-intent publication-safety case on source
`89ba7730…`, binding `1d2aad5e…`, with all 956 bound paths unchanged. Its
instrumented call takes 13.683050972 wall and 10.494631632 owner-CPU seconds.
Client time including cleanup is 18.256417 seconds; host time including
cleanup and report reads is 19.110954 seconds. These are separate clocks.
The profile sees 3,312 entries, ranks 3,028, excludes 284 suspension entries
and omits none. Quality, owner and hook-restoration checks pass with no
observer errors; ranked self time totals 9.82244659 CPU seconds. Pathlib
leads the self-time ranks. Native work is charged to its containing Python
frame; this flat ranking supplies no precise caller or normal-core saving.
Independent terminal review `38e1417a…` verifies actor absence, clean owned
native profiles, the retained volume and unchanged unknown inode 303784487.
The existing 30/120-second bounds and 2 GiB/six-CPU limits remain; no ceiling
is reached. This local test publishes no production dataset and changes no
normal timing qualification. Raw results and samples remain in scratch.

## Hold reopened after a separate 268-second expiry gap

The [explicit administrative reopening](../../evidence/runtime/postgres-stage2-2026-11-11/expired-hold-reopen-20261112-220454.json)
completes with CLI exit zero. Sole terminal review `d09f73c3…` verifies all
seven transports exited zero, both exact helpers naturally exited zero with
PID zero and no OOM or restart, and all 12 protected services are unchanged.
The installed hold is root-owned mode 0444 with one link; three copied
receipt hashes and metadata match before pointer finalization. This consumes
the sole verdict and adds no independent review or server operation.

The old 22:02:36.819952 review expired before activation completed at
22:07:04.990803698 UTC: the recorded gap is 268.170851 seconds, explicitly
not continuous validity. It is separate from the earlier 26,720.629642-second
gap. Owner, reason and creation time remain unchanged. New hold `846b3782…`
retains its prepared 22:04:54.469325 review time and fixed 23:04:54.469325 UTC
deadline (6:04 p.m. ET); it is not redated from activation. No worker, new
run, SQL, cleanup, migration or publication authority follows.

## Bounded watch records one request

The [22:27 read-only watch](../../evidence/runtime/postgres-stage2-2026-11-11/production-watch-20261112-2225.json)
records both completed stock SSH reads exiting zero with empty stderr.
The journal query starts at 21:15 UTC but retains only 100 lines. Those
lines contain eight acquire ticks through 22:20:25.816665, with one request
and total progress of one, and eight derive ticks through 22:24:26.533027
with zero progress. All report no failure or error. The alert tail is
byte-identical to the previous watch, including its host-failure, budget
guard and stale-fetch notices. One observed request is not evidence of
continuous recovery, and this bounded window establishes no cause or full
coverage. No repair or new server action follows from this receipt.

## Core 508 interrupted after sixteen failures

The [latest check](../../evidence/runtime/postgres-stage2-2026-11-11/core-508762f3a0b94f039d0f5619-interrupted.json)
on source `c9f7bb57…`, binding `dad1c576…`, remains failed and incomplete.
Of 3,693 selected cases, 543 pass, 16 fail and 3,134 are unfinished. Fifteen
failures are crash-helper 15-second timeouts; one is a publication
`ControlTimeout`. Cause remains unlocated. Root sends one SIGINT at
22:31:04.307520 UTC to exact PID 46512, bound by its recorded birth and job
marker. The resulting 412.235024916-second incomplete run exits one; it
does not qualify timing or correctness. Ruff and mypy over 376 files pass,
and the observer's 95 focused passes remain a separate, narrower result.

Sole terminal review `25850292…` accepts owned cleanup and the native
boundaries, unchanged unknown-ledger inode and exact natural weight cache
`7b215846…`. No manual weight, scheduling or limit change follows. The
last complete run is still the historical 3,666-case pass at 250.69 seconds,
over the unchanged 180-second gate. The existing plan selects a bounded
reproduction of two failed native cases; it selects no unchanged normal
retry, full replay or server operation. Raw reports remain in scratch.

## Routine hold interval extended to 23:40 UTC

The [22:40 routine renewal](../../evidence/runtime/postgres-stage2-2026-11-11/hold-review-20261112-224015.json)
exits zero with all seven transports zero and both exact helpers naturally
exited zero without OOM. Sole terminal review `44b7fd83…` verifies their
complete profiles, unchanged protected services, root-owned mode-0444
single-link hold, three copied receipt hashes and metadata, and final
pointer equality. Source `3a4845a2…` remains unchanged. Activation at
22:40:31.180631426 UTC precedes the old 23:04:54.469325 deadline, so this
interval is continuous. New hold `61c2af69…` ends at 23:40:15.510999 UTC
(6:40 p.m. ET). The earlier expiry gaps remain historical; no worker or
new operation authority follows. The compact receipt retains only hashes,
counts and flags; raw records remain in scratch.

## Native full suite passes 5,273; core gate remains open

The [final native full run](../../evidence/runtime/postgres-stage2-2026-11-11/callee-observer-full-20261112.json)
exits zero with all 5,273 unique cases passing and no failure, error or skip.
Four native workers run the unchanged full command recorded in the sole
review. Pytest reports 1,528.25 seconds; measured phase wall time is
1,528.4692 seconds. All 956 before-snapshot inputs still match source
`c9f7bb57…` and binding `dad1c576…` after completion. The sealed scratch
proposal is preserved; the compact retained receipt adds only the command
descriptor and actual exit zero, with all raw artifact hashes bound through
terminal review `8fca9712…`.

That sole review verifies the fresh native catalogs and profiles, no
remaining clients or case-copy databases, no TestRun JSON records, released
locks and the unchanged unknown-ledger inode. Available space is
154,399,883,264 bytes, above the 80 GiB floor. No new test, source edit,
server query or review is performed to record this outcome. The earlier
5,172-case pass is historical; core `508762` is still interrupted, incomplete
and failed. Full-suite correctness does not qualify the normal 180-second
core gate or erase those 16 failures.

## Fresh read-only preparation observations and checkpoint review

The new callee-observer packet remains scratch only at
`/tmp/swingset-stage2-callee-observer-cold-draft-bcb430f5961a44e49daa11a7-v2`,
manifest `cb75b1c3…`. Its 26 selected public read-only checks finish with exit
zero and empty stderr. Collection `0f8a4209…` and sole semantic review
`c0aa2ce5…` preserve the private catalog, source identity, protected services,
guardian and current hold. The known held inventory limitation remains; exact
role counts and healthy monitoring are not established. No preparation effect,
new actor or new database has occurred. Fresh actual preparation admission is
still pending. The source-bound native full check passes 5,273 cases, while the
concurrent core's incomplete failure remains open. The reviewed revision
description is `/tmp/swingset-stage2-reviewed-revision-description-20261112.txt`;
permission to close the unfinished checkpoint is pending because only 101 bytes
remain in this change's evidence budget.

## Seventy-five-second policy: local validation

On November 13 the owner selects one 75-second absolute worker write deadline,
including nested writes and commit acknowledgement ([D-0294](../../decisions/0294-use-a-seventy-five-second-worker-write-deadline.md)).
Cancellation, rollback and retry rules remain in force. Future rehearsal
operators must record the frozen producer's installed limit and the applied
75-second limit on their owned copy, then restore the installed setting. No
server run, deployment, commit or push is authorized by this request. The last
recorded server review expired November 12 at 23:40 UTC.

The initial focused selection reports 434 passes and one failure from 435
cases in 229.97 seconds: a synthetic 30-millisecond admission alarm also covered
retry bookkeeping. The correction scopes that test's real SIGALRM to its
intended capture, without changing production behavior. The complete
write-deadline test file then passes all 27 cases in 12.96 seconds. Sole source
review `/tmp/swingset-75s-review-20261113/independent-source-review.json`, SHA-256
`c9b26556430800e3e956bee29608072e6e5a73ed4524712f638872ba82907700`,
finds no material source issue; core and full validation were pending at that
review.

Core job `5ff7c22a19744c1a967230fe` subsequently passes all 3,702 cases with zero
failures, errors or skips in 236.89 seconds. Its checks pass with no reported
errors; all five nested client cleanups and physical cleanup report complete.
These are the runner's cleanup reports; the later independent terminal review
below confirms the owned boundary.
Ruff, mypy across 376 files and formatting exit zero. The unchanged 180-second
core gate still fails. Summary SHA-256 is
`b726a1eef6408c85954c2b93ced6df34891a926062d15b27a60bbeda3de62036`;
source is `f3e415d06469f23dadc13d696d8218eadf7be46bdfba471e84a99c4b5b793fc7`,
with 956-input binding
`269814347ccd1aa2ba5b553fd722f75b3e43dbc9860b8d645fc6cc20d0d94c86`.

The final native full run completes with all 5,282 tests passing, zero failures,
errors or skips, in 1,458.12 seconds (24 minutes 18 seconds). Tool session 2025
exits zero after using four workers on those frozen inputs. Its scratch packet
is `/tmp/swingset-75s-full-20261113-1632`. `after.json`, observed November 13
at 16:57:47.898054 UTC, confirms the same source and exact 956-input binding
as `before.json`. Available space falls from 150,945,427,456 bytes to
118,472,949,760 bytes, still above the 80 GiB floor; initial admission exceeded
110 GiB. Log SHA-256 is
`123eba447ef2e714d5064cce4c38c51e8717be855002499405d7359a8452978c`;
JUnit SHA-256 is
`cf3909487491ec766a5867818ce57468e84a6980975c278366d9d04dcbc52dfa`.
The sole independent combined terminal review accepts correctness and owned
cleanup, while retaining the core timing failure. Its scratch verdict is
`/tmp/swingset-75s-full-20261113-1632/independent-combined-terminal-review.json`,
SHA-256 `8ec195a6ba2b65441e994a942ff609a21e55f17b3d9f14e3e4fd26526f79b56d`.
It independently checks all 5,282 unique full-suite cases, the 3,702-case core
membership and natural assignments, and all 956 unchanged source inputs.
Both native profiles, catalogs and six public roles match the prior baseline;
owned clients and case-copy databases are absent, locks are free, and the
unknown ledger's metadata is preserved without reading its body. Fresh free
space is 118,211,850,240 bytes, above the 80 GiB floor. CPU observations for
clients 2 and 3 remain rejected-quality. No material correction or further
runtime action follows from this review.
The core's 236.89-second result still fails the 180-second gate. The prior
5,273-case full pass on `c9f7bb57…` predates the new deadline and remains
historical. No new server run, deployment, commit or push occurs. No new
retained receipt is written: only 101 bytes remain in the change's evidence
budget, so this validation's raw artifacts stay in scratch.

## Getting the current core below three minutes

The owner's latest question is how to bring the 3,702-case core from 236.89
seconds below the unchanged 180-second gate. That requires 56.89 seconds,
about 24% of elapsed time. Existing observations do not identify a safe change
that delivers that saving. The 16 workers finish within 6.286 seconds of one
another. Dividing their observed case-phase work equally gives an ideal
216.16 seconds before launch, collection and cleanup overhead; scheduling
alone cannot close the gap at those observed costs. Setup totals 574.20
worker-seconds, or 35.89 seconds divided by 16. Even eliminating all setup
would not alone bridge the elapsed gap, and eliminating coverage is not an
option. Worker occupancy is not directly additive elapsed savings.

The useful next measurements are bounded caller attribution inside these
existing operations, preserving all cases, assertions, resource caps and the
180-second gate:

- Crash recovery occupies 190.38 aggregate worker-seconds across 19 cases,
  with only 1.355 seconds of accepted parent owner CPU. Split actual child
  lifetimes into startup/imports, state opening, pipeline work and recovery.
  Parent CPU does not measure the children. The passed source flow implies
  39 child launches; that is a static count, not a measured launch count or
  child-cost attribution.
- On actual critical worker client 0 / `gw1`, assembly and closure-validation
  blocks occupy 85.36 wall seconds, including 46.07 seconds of setup. Their
  accepted owner CPU totals 47.63 seconds. Attribute the shared real release
  fixture's bootstrap, admission, publication fixture, input acceptance and
  materialization callers before proposing reuse or removal. Existing
  records do not establish redundancy.
- The 33-page completion-cohort case on that same worker occupies 79.47 wall
  seconds and 24.14 accepted owner-CPU seconds. Its source permits at most
  600 choices; the receipt does not establish 600 executed choices. Attribute
  readiness preparation, watch updates and actual fetch selection without
  reducing the test's scope or reopening earlier closed cache proposals.

Clients 2 and 3 fail CPU-observer quality, so their CPU values cannot support
attribution; their monotonic wall observations remain useful. Memory samples
show no sampled cap event or OOM event. They neither prove an absence of all
pressure nor justify a RAM change. No particular optimization, normal-core
saving or new execution is selected by this analysis.

The scratch analysis is
`/tmp/swingset-stage2-core-5ff7-existing-analysis-20261113/analysis.json`, SHA-256
`bd47581620db334328d7a0ce333b3639ac98c102806e964388bf3dd999a053a2`.
The resource analysis is
`/tmp/swingset-core-5ff7-resource-analysis-20261113.json`, SHA-256
`52da6ff958ea132f946512f02b1c6155dfeec7a458463b5d24021a409b97d9b1`.
Both describe existing job `5ff7c22a19744c1a967230fe`; no new profile,
repository implementation or retained evidence is created for this answer.

## Five-minute core gate

On November 13 the owner raises the complete local core budget from 180 to
300 seconds. This replaces the three-minute target in the migration plan and
development instructions. It changes the feedback policy; no performance
optimization, test selection, resource cap, worker count or 75-second worker
write bound changes. Historical failures, including job `5ff7c22a…`, retain
their original 180-second outcome.

The runner reports `swingset-core-run-v2`, with explicit `budget_seconds` and
`within_budget` fields in place of the old fixed-duration field. The error
message uses the same budget constant as the final exit check. All 67 runner
cases pass in 61.25 seconds. The complete default check runs as job
`98bb4ce6d3bd4ffca4abc8e3`, on source
`862bbd75c9a7496e0d297147c8a9192b1acd759917a90ac6d16f04d331118e3c`,
with 956-input binding
`8e83714209c94b9b4353cb21c3afcbf31c23529dba739744314d1d9a2b1ce367`.
The raw packet stays in `/tmp/swingset-core-300-20261113-1849`; no retained
receipt or server operation is added. The earlier full-suite pass remains on
its original source. This budget-only runner change needs focused and core
checks, not a repeated extended recovery run.

The check exits zero: all 3,702 cases pass, with zero failures, errors, skips,
xfails or xpasses. The printed core duration is 267.29 seconds; its summary
records 267.193431 seconds, `budget_seconds=300` and `within_budget=true`.
Ruff and mypy across 376 source files pass. All five client cleanup reports
and physical cleanup report complete. These are runner reports, without a
new independent native cleanup audit. All 956 bound inputs match before and
after the run; only `src/swingset/dev/core_runner.py` differs from the earlier
full-suite input set. Free space after the run is 142,837,051,392 bytes.
Summary SHA-256:
`ced8c7a02815c36c9c6c4d57ec61d25400eecd04842f9fbc6b4b8bacddeb85b5`.
Check-log SHA-256:
`eb91be5a8ce6a4cb9a696934f7530f7f6117a5ed970e48daf4fe1b5b5641025c`.
This passes the newly selected budget; it establishes no performance gain.
The 75-second write policy and failed server graph units remain unchanged.

## November 16 Stage 2 resume

The owner asks to proceed. All 956 source inputs still match the tested
`862bbd75…` source and `8e837142…` binding. No performance work or source edit
is selected. The revision still contains 204,699 bytes of added evidence;
only 101 bytes remain. Explicit approval to close `rnmoupss` and continue in
a fresh revision is requested before server mutations and new receipts.

Read-only SSH checks preserve the exact old hold hash and all twelve
protected services against the prior baseline. The server filesystem reports
579,082,010,624 bytes free. Raw resume artifacts stay in
`/tmp/swingset-stage2-resume-20261116`. No guarded job or server mutation runs.

The maintained hold command prepares the unapplied reopening draft
`/tmp/swingset-stage2-hold-review-20261116-153214`, changing proposed hold
`61c2af69…` to `2b66cc2c…`. Its admission hash is
`fcb4de2932a70cff4b53e9c96d4a465490379816d1e5e034055b656db10353cc`.
The sole independent administrative review is
`/tmp/swingset-stage2-hold-reopen-independent-20261116-153214/execution-review.json`,
SHA-256 `030c07fadf6eba5dcdc638731b76677c15de4d9dafe5f09c4185d41d4cbbfb0d`.
It accepts only the seven-index reopening protocol, conditional on the pending
revision approval. The fixed proposed review expires at
2026-11-16 16:32:14.220269 UTC. It supplies no worker authority and must not be
retimed or used after expiry. The installed hold remains expired.

A new local 75-second candidate is sealed at
`/tmp/swingset-stage2-75s-callee-cold-draft-92900be19500452c971e1292-20261116-v1`.
Its manifest SHA-256 is
`bb9d89aaa1fbc7b3062619f0ac1861389667d6142bfea43775dee819c0cf1086`;
147-command SHA-256 is
`0846f968d4b7fb923d81a14c9334fa48ee6ba6b3bbd53dcfe6e489909380553c`.
It stages both current diagnostic modules, operator `614d3bd1…` and supervisor
`89cd6ff2…`, with new resource names. Frozen producer `8aae03bf…`, image
`346f30a4…`, its 560-file proof and linker 9 remain fixed. Historical D21 and
bcb resources are denied targets. Both preparation and execution authority
remain false; runtime container and review fields remain placeholders.

After revision approval, apply and independently confirm a fresh hold, collect
fresh read-only admission, obtain the actual preparation review and root go,
then run and audit all 147 preparation outcomes and cold-copy memory proof.
A separate execution review and root go must bind the actual cold container
before the foreground diagnostic. These local drafts establish no admission,
acceptance, graph success, migration or deployment. No new retained evidence
is written while the current change has no receipt space.

The sole static reviewer accepts the new template and proposed read-only
boundary without material findings. Verdict
`independent-static-template-review.json` in the new candidate directory has
SHA-256 `09282651b780db69012c2805fe9c1181094a86daa3d43a42bed1609df2665d57`.
It checks all 24 member identities, all 147 command rows, nine uploads,
24 helper templates, three proposed volumes and current module bytes. The
proposed 26-record admission scope and stat ordering remain bounded; no
collection, preparation or worker authority follows. Fresh actual hold and
admission, conditional filesystem guards, the later recheck, cold-copy memory
proof and a separate execution review remain required. No historical helper
identity may substitute for actual proof. Local preparation is complete;
revision-close approval remains pending.

## November 16 administrative reopening

The owner explicitly approves closing the tested checkpoint. Revision
`rnmoupss` closes as `ec2e7f13`; new revision `ouvlzsyr` starts with a fresh
200 KiB evidence budget. The preceding approval request is resolved. All 956
source inputs still match the qualified `862bbd75…` source: 3,702 core cases
pass in 267.29 seconds under the approved 300-second gate. The 5,282-case full
pass on `f3e415d0…` predates the runner-only budget change. Neither result
replaces fresh server admission or a reviewed runtime operation.

The maintained administrative reopen completes with exit zero. Actual rename
activation starts at 2026-11-16 16:05:08.166341224 UTC and finishes at
16:05:08.333362139 UTC. The hold's prepared `reviewed_at` of 15:32:14 is not
execution time. Hold `2b66cc2c…` has a fixed review deadline of
16:32:14.220269 UTC, not one hour after activation. The preceding review
expired November 12 at 23:40:15.510999 UTC; its 318,292.822363-second gap is
preserved and continuous validity is false.

Sole independent terminal review
`/tmp/swingset-stage2-hold-review-20261116-153214/independent-terminal-review.json`,
SHA-256 `f0c8721a9d01d95523b9b8fd03dfb51be6374d5513e4e8bcd01fc5ebaed8c3cd`,
accepts the observed administrative reopening. All seven protocol transports
exit zero; both exact helpers exit naturally with PID zero, no OOM and no
restart. Fresh checks preserve all 12 protected services, the root-owned
0444 single-link hold, three receipt readbacks and the current pointer/fence.
The source command remains `3a4845a2…`; the exact command manifest, result,
admission, authorization and terminal hashes are bound in the one compact
[reopen receipt](../../evidence/runtime/postgres-stage2-2026-11-16/expired-hold-reopen-20261116-153214.json).
Raw commands, logs and helper profiles remain in scratch.

Candidate `92900be1…` remains locally static-reviewed only. No 75-second cold
preparation or worker run has launched. Complete fresh read-only admission,
then an ordinary same-identity hold renewal before preparation; actual
preparation and execution still require separate reviews and root approval.
This reopening establishes no acceptance, graph success, migration or deployment.

## November 16 scoped read-only collection

The reviewed 26-read collection completes at 16:15:35.652741 UTC with exit
zero, all 26 transports zero, empty stderr and no unknown outcomes. Exact
order, command and raw-output bindings match; the scoped source, retained
catalog, guardian and protected profiles pass. Available space is
579,074,048,000 bytes against the conservative 508,224,930,112-byte requirement.
Hold `2b66cc2c…` and pointer `d24741fe…` remain exact throughout the capture,
with 998.568 seconds remaining at completion. No later renewal is substituted
into this completed observation.

The held status reports `complete_inventory=false`, count 36, resource exit
zero and no observation error. This remains a limited held observation: it
does not identify all missing or duplicate roles, prove healthy monitoring,
or establish full admission. A first local invocation refuses the `/tmp`
alias before output creation, intent or SSH; the canonical `/private/tmp`
invocation consumes one actual collection fence. The initial refusal remains
in scratch provenance and is not a second executed collection.

Sole terminal review SHA-256
`ed7fed29e13fc0c0ce817e77cfd2c95c38ac87f24600faaa2577a59d333c4941`
accepts only the scoped observations. Admission SHA-256 is
`3418b38e057ca0a5b828ee8f496dff48d855db06f9061a32ba9a881ab80bf57d`;
the raw packet is
`/tmp/swingset-stage2-readonly26-collector-20261116-v1/attempt-001`.
The [compact collection receipt](../../evidence/runtime/postgres-stage2-2026-11-16/scoped-readonly26-20261116-161535.json)
binds the 26 commands and review; raw outputs stay private. Fresh new-name,
root, IP and host-timeout proofs remain pending, as do conditional source-FS
helpers and numeric metadata. The host's `private.env` is not a dependency:
there is no `--env-file`; only the source volume's row-42 numeric DSN guard
and the eventual actor's final Env RAM checks are required. No preparation,
copy, worker or database mutation is admitted by this collection.

## November 16 routine renewal after collection

The subsequent maintained renewal completes with exit zero. Hold
`91e5ded7…` activates at 16:16:30.188202885 UTC, before `2b66cc2c…` expires
at 16:32:14.220269, and sets review due at 17:16:18.942671 UTC. Owner,
reason and creation time remain unchanged. This interval is continuous;
the preceding 318,292.822363-second expired reopening gap remains historical.
All seven transports exit zero, both exact helpers exit naturally with PID
zero and no OOM, and fresh independent checks preserve all 12 protected
services, root-owned 0444 single-link hold, three receipt readbacks and pointer.

Sole terminal verdict
`/tmp/swingset-stage2-hold-review-20261116-161618/independent-terminal-review.json`,
SHA-256 `7570cc01a2220e269ece8267064fa47c5ed7fd2790331c50bfc87a75c2823984`,
accepts the ordinary renewal. The [renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-161618.json)
binds its actual command manifest, source, result and terminal hashes. No cold
preparation or worker has launched. Fresh preparation admission, immediate
boundary checks and separate actual cold/execution review remain required.

## November 16 missing metadata and conditional prefix

The separate missing-metadata collection completes at 16:31:01.617224 UTC
with all 35 declared outcomes validated and no unknowns. Ten positive checks
exit zero; the 25 exact named Docker absence probes correctly exit one.
The parent-directory metadata, remote UID/GID, GNU timeout 9.4, new-root
nonexistence including broken links, three empty volume-name lists and free
verifier IP match the reviewed scope. These are observations, not resource
reservations or permission to adopt a later collision. No preparation row runs.

Sole terminal review SHA-256
`163d87b5f6c87a0d795693fdc5fb179a967fc9746d6957da27d0baf9bcb4ec15`
accepts only this metadata scope. Actual result SHA-256 is
`eb49999df0db3a5f7ce98cadf5e86a4d895f46333cfc381711c7568873e1e5ae`;
raw commands and outputs remain in `/tmp/swingset-stage2-missing35-20261116`.
The [compact receipt](../../evidence/runtime/postgres-stage2-2026-11-16/scoped-missing35-20261116-163101.json)
binds the exact 35 command hashes, collection authorization and terminal review.
The earlier 26-read status remains limited: incomplete inventory, count 36.
The hold remains `91e5ded7…`, due at 17:16:18.942671 UTC; the earlier expired
gap is unchanged. Source `862bbd75…` and all 956 tested inputs remain fixed.

Independent preparation-prefix review
`/tmp/swingset-stage2-75s-preparation-gates-20261116-v1/independent-preparation-0-78-review.json`,
SHA-256 `5a37fde772a012d479e55b1e38fffdf1d325a95ffcb3cc8870441dc123d64dcd`,
conditionally admits only rows 0–78. An immediate 27-read pulse and exclusive
root go must precede effects. The reviewed prefix includes the seven
conditional source-FS checks and public operator sealing, then a hard stop
before row 79 and any allocator, copy or cold-actor phase. The runner is still
being finalized; the pulse has not run and no new root or helper is created.
This is no worker, database-copy, acceptance, graph or migration authority.

## November 16 preparation prefix completes

The rows 0–78 runner exits zero at 16:52:06.113799 UTC. It reports all 79
contracts validated, no error, last attempted index 78 and next unattempted
index 79. The seven source-read-only helpers and four public-seal helpers
all exit naturally with full profiles passing. The new public operator is
sealed; no row 79, target volume, copy, cold actor or worker exists. Sole
independent terminal review accepts all 79 exact intents, raw results and
contracts, 11 fresh complete helper profiles, seven source checks, private
numeric metadata and sealing. Its file is `independent-prefix-terminal-review.json`
in the raw attempt directory, SHA-256
`5fecfb9e1b42e9999a97557172201d63f084a6642a13589c794c7fba3731e056`.
This is progress within the same unfinished
147-row cold-preparation operation, so no separate prefix receipt is retained.

The raw summary is
`/tmp/swingset-stage2-prefix0-78-runner-20261116-v1/attempt-001/summary.json`,
SHA-256 `06466f6a810e606ce56ed96b526ac8d0b678af80535af06ab6ee5af498a1cf31`.
It binds root go
`eccdc3f4df5fc8d16a3dea9ca26e233445256e969b4c6c46a96e526ea91499b9`
and fresh pulse
`e960ebc76411c95579514d78bd36bbf953c16e1a7f5bd761c19bd47e54c3b9e8`.
Source `862bbd75…` remains unchanged. Rows 79–145 are only a proposed next
phase: actual allocation-boundary proof, independent review and root go remain
pending, followed by a stop before row 146 and the cold/RAM checks. No automatic
continuation or worker authority follows from the successful prefix.

## November 16 renewal while preparation is paused

The ordinary renewal completes with exit zero while paused before allocation.
Hold `18af4cfe…` activates at 16:54:14.006267876 UTC, before the previous
17:16:18.942671 expiry, and fixes the next review at 17:54:02.798985 UTC.
Only this interval is continuous; the earlier 318,292.822363-second expired
gap and older gaps remain historical. All seven transports exit zero and both
exact helpers exit naturally with PID zero and no OOM. Independent fresh
checks preserve all 12 protected services, root-owned 0444 single-link hold,
three receipt readbacks and exact pointer.

The sole renewal terminal verdict is
`/tmp/swingset-stage2-hold-review-20261116-165402/independent-terminal-review.json`,
SHA-256 `0057c82d80fcba9d064a2d1310487f5a132efca54b6af9ef8df1635bd67cd8c1`.
The [compact renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-165402.json)
binds source, command manifest, actual helper identities, result and review.
The renewal supplies no remaining-preparation, copy, worker or deployment
authority. No new bound-source edit, test, formatter or remote action is
performed to record these outcomes.

## November 16 before-allocation boundary refusal

The proposed 54-read allocation boundary stops at 17:14:45.381377 UTC after
the first 16 transports (indices 0–15) all exit zero with empty stderr and no
unknown outcome. The local validator incorrectly expects nine hash lines from
stock base command 20, which lists seven Python files. All seven captured
hashes match the exact argv and upload manifest. The two new JSON files,
plan and preparation commands, were outside that command; their absence from
its output is not server-code drift.

Sole partial review
`/tmp/swingset-stage2-before79-boundary-20261116/independent-partial-boundary-review.json`,
SHA-256 `d3f534300e75b7cc9a039b31fee7836a903511dacb698e7c8e47e67866d6358e`,
confirms the local predicate bug. Preserve the original incomplete result,
once fence and collector source `cde277…`. Result SHA-256 is
`4a1e093a1971f52778fcd353b148d41344d825bd2f998b25351155df54765c3e`;
the raw packet is `/tmp/swingset-stage2-before79-boundary-20261116`.
No phase-two go or phase-two operation fence exists. No allocation, copy, cold
actor or worker runs, and no separate preparation receipt is retained yet.

The new unexecuted remaining-only collector is
`/tmp/swingset-stage2-before79-continuation-20261116-v2/pulse.py`, SHA-256
`b6bed8fac33654b2a0530d0af464a8c00ca3e4b2d69a7182902156b8cb9248a8`.
Its proposed 40-read scope `24dda879…` adds the two exact stock JSON hash
reads (preparation rows 6 and 9), then original unattempted boundary entries
16–53. It replays none of the completed 16 reads. Future stderr is bounded at
16 KiB; the first attempt's actual stderr is zero, so no prior output breach
is inferred. Partial facts stay separate from final completion flags.

The corrected pulse still needs review and complete actual results before
exclusive go for rows 79–145; the proposed runner `b832…` adds complete
HostConfig and time-bound checks and must stop after row 145. Hold `18af4cfe…`
remains due at 17:54:02.798985 UTC. Source `862bbd75…` and all 956 tested
inputs remain unchanged. No source edit, remote action, test or formatter is
performed to record this refusal.

## November 16 preparation rows 79 to 145 complete

The reviewed remaining-only boundary combines the original 16 successful
reads, 38 previously unattempted reads and two additional JSON hashes, without
replaying completed reads. It preserves the original false-predicate result
`4a1e093a…`. Rows 79–145 then complete at 17:27:32.311502 UTC with all 67
outcomes validated. Summary SHA-256 is
`d4d6c3d6e17ddf862b077a1e374770c51cb8ae1cf5a2070bf1218d8131fdcf44` in
`/tmp/swingset-stage2-phase2-79-145-runner-20261116-v2/attempt-001`.
Sole terminal review in that directory, SHA-256
`9081f4a15b4431fab5c57f8a1253841347f3ce1b29dd2b1791d397abc97c2862`,
accepts the 67 exact results, complete HostConfigs and nanosecond time proofs.

All 24 actual helper profiles and natural exits, three fresh owned volumes,
settings-copy numeric metadata and the 1,689-file/69,548,306-byte input closure
pass. Cold CID
`24154fe5cb93df1dc4c4f64232b6f7d73c440c106179f4908781066c8f6d2311`
has only the create-return proof. It is not yet proved Created by full profile
inspection: row 146, final Config/HostConfig and RAM comparisons are pending.
The three proposed post-cold reads are under review and have not run.
No SQL freeze, source post-hash or worker has run. The 147-row preparation
operation remains incomplete; its raw progress stays in scratch without a
separate phase-two or row-146 receipt.

## November 16 renewal after phase two

The next ordinary hold renewal exits zero. Hold `e989e58a…` activates at
17:30:14.087718240 UTC, before the previous 17:54:02.798985 expiry; next
review is fixed at 18:30:02.917141 UTC. This interval is continuous; the
earlier 318,292.822363-second expired gap remains historical. All seven
transports exit zero and both exact helpers exit naturally with PID zero and
no OOM. Independent fresh public checks preserve all 12 protected services,
root-owned 0444 single-link hold, three receipt metadata/hash readbacks and
pointer `b4bd22fc…`.

The sole renewal terminal review is
`/tmp/swingset-stage2-hold-review-20261116-173002/independent-terminal-review.json`,
SHA-256 `ddc5c14242e9d57501fcff084411c9f0759ecd0a45d0fa5965c7e576db74caa2`.
The [compact renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-173002.json)
binds its actual source, command manifest, result, helpers and review. It adds
no preparation, database or runtime authority. Source `862bbd75…` and all 956
tested inputs remain unchanged; no source edit or remote action is performed
to record these outcomes.

## November 16 all 147 cold-preparation rows accepted

The final three post-cold vectors complete at 17:42:47.230932 UTC, with
public row 146 and six RAM-only boolean comparisons passing. All 147
preparation rows are complete. Actual cold CID
`24154fe5cb93df1dc4c4f64232b6f7d73c440c106179f4908781066c8f6d2311`
is Created, PID zero and never started, without OOM or restart. Complete public
Config/HostConfig and all 11 mount objects match. Raw Config and Env remain
in remote pipeline RAM; only the public projection and six booleans persist.

Sole terminal review in `/tmp/swingset-stage2-postcold-actual-20261116`,
SHA-256 `3c35d31c9a24aa7e181c041eed43e6b1601c316de91127ec8817a4fac32d8d2e`,
accepts the whole preparation, binding prior prefix review `5fecfb9e…` and
phase-two review `9081f4a1…`. Final result SHA-256 is
`ea059c8aac92a0834a14cdb835aa243dadb6e9f602bf7b8d9a93542b75077b2b`.
The one [cold-preparation receipt](../../evidence/runtime/postgres-stage2-2026-11-16/cold-unit-preparation-92900be19500.json)
binds all three phases, 24 actual helpers, three fresh owned volumes and the
1,689-file/69,548,306-byte settings input closure. It preserves the earlier
false boundary predicate and remaining-only continuation without replay.
Raw commands, profiles, copies and reports stay in scratch.

No SQL freeze, input acceptance, trial, source post-hash, worker, cleanup or
deployment is performed or authorized by preparation. Source and protected
proofs use the accepted pre-allocation boundary, exact helper/copy proofs
and renewal checks; the reviewer performs no new post-copy SQL or full source
scan. A separately sealed actual 75-second execution plan and review, fresh
complete boundary and root go remain required before starting the actor.

## November 16 renewal after cold preparation

The ordinary renewal completes with exit zero. Hold `3ce1251b…` activates
at 17:50:41.426419401 UTC, before the previous 18:30:02.917141 expiry, with
review fixed at 18:50:30.227899 UTC. This interval is continuous; the earlier
318,292.822363-second expired gap remains historical. All seven transports
exit zero, both exact helpers exit naturally, and independent checks preserve
all 12 protected services, root-owned 0444 single-link hold, three receipt
metadata/hash readbacks and pointer `c6c141d3…`.

Sole renewal terminal review
`/tmp/swingset-stage2-hold-review-20261116-175030/independent-terminal-review.json`,
SHA-256 `10e3966f281c4ddf543268fd1dd5a78a86fbdfe6a711048c5741ffa43b022c2e`,
accepts this interval. Its [compact receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-175030.json)
binds actual source, command manifest, result, helpers and verdict. The actor
remains unstarted; renewal supplies no runtime or database authority.

## November 16 75-second diagnostic starts

The actual execution plan is sealed in
`/tmp/swingset-stage2-unit75-execution-final-92900be19500-20261116-v1`,
plan SHA-256 `8ea259…`. Sole action review SHA-256 is
`17873f6090bd33f903690332796e278eb9eae06e0011dddc01a0dae645ba23fc`.
The actual 37-read result `899d97…` and semantic result `9e935…` pass; the
separate seven staging and 13 final-pulse outcomes pass in
`/tmp/swingset-stage2-stage-and-pulse-20261116-v1/result.json`. Root records
exclusive `worker-launch.once` go before the one foreground launch. Local
SSH process 46604 belongs to tool session 13584; neither identifies the
remote supervisor.

Actor `24154fe5…` actually starts at 18:33:10.216488566 UTC, host PID 3116755,
start ticks 1025042875 and network namespace 4026533551. The 18:37 live sample
still reports running without error. Independent startup review is active and
not yet accepted here. No acceptance, inventory trial, cleanup, graph or
migration success is claimed. Frozen producer `8aae03bf…`, image `346f30a4…`
and linker 9 remain fixed; the reviewed operator separately applies the
75-second policy on its one owned copy. It must not be confused with a
deployment of the current port.

Maintained ordinary renewal packet
`/tmp/swingset-stage2-hold-review-20261116-183551` exits zero and installs
hold `41f2b246…`, with review fixed at 19:35:51.076328 UTC. Actual activation
finishes at 18:36:01.241083997 UTC, before accepted predecessor `3ce1251b…`
expires at 18:50:30.227899. Independent renewal terminal audit is pending;
no compact renewal receipt is sealed yet. The earlier expired gaps remain
historical. Neither a preparation timestamp nor activation grants extra time
beyond the fixed review.

### Next qualified runtime direction

Select already-qualified source `862bbd75…` for the next actual runtime and
whole-graph proof after this old-image diagnostic reaches terminal. Prepare
the ordinary dependency and runtime AMD64 builds locally in parallel, preserving
the running server's image and operator bindings. At selection time the build
had not started; its subsequent actual qualification is recorded below.
The existing builder `7f473…` / `swingset-stage1-final` is bounded to 2 CPUs,
2 GiB RAM, 4 GiB swap and 256 PIDs. Observed host free space is 143,641,060
KiB, above the 80 GiB floor; no full-suite scratch is active. Qualify the
569 installed files, source/dependency identity, Docker CONFIG and OCI artifact
separately. Local build work is not a server image transition or runtime proof.
Do not replace the old diagnostic image before terminal or start a graph from
these local prerequisites alone. Fresh final-runtime admission and actual
whole-graph review remain required; no source edit or server action is
performed to record this direction.

## November 16 local runtime qualification completes

Ordinary local dependency/runtime AMD64 build and qualification complete at
18:45:11.899651 UTC. Source `862bbd75…` and all 956 inputs remain unchanged,
with binding `8e837142…`. All 569 public installed files and 376 Python files
match exactly, including duplicate app source. OCI manifest, CONFIG and every
layer hash pass, as does the dependency root-filesystem prefix. Build,
archive, proof-container creation and four copy readbacks exit zero. The
network-none byte-proof container `a5b32724…` is never started.

Qualified artifact is
`swingset-runtime@sha256:08df0ebcf24c3def6233bfb297cb8378412cb982129f9ced6d359d4fe5ea4859`;
portable CONFIG is
`sha256:e68aa1274c16eb2a445f7ebec921ca175cb57dc0bf627fab6b00bb30695aa276`.
The 248,929,280-byte archive has SHA-256
`8c2947f47ce3cd1f3a97396cd57d9ca8b3f0ee926650570fdbc449d7832bd532`.
Qualification file `/tmp/swingset-stage2-runtime862-amd64-build-20261116-v1/qualification.json`
has SHA-256 `13271be51fe40acc365c946f324f28f2a3ffcd5d8500d565512c57fc94176776`.
Available Mac space is 147,551,715,328 bytes. The [compact local receipt](../../evidence/runtime/postgres-stage2-2026-11-16/runtime862-amd64-qualification-20261116.json)
binds the source snapshot, build metadata and actual exits. No server image
load, activation or graph proof occurs. The old `346f30a4…` diagnostic
continues unchanged.

Its current observed operator phase is `freeze_one_new_owned_database`, owner
`1d527dbfd220474abcd80cec`, owned database `_0` OID 7209713. That observation
is not a completed freeze, copy readiness, acceptance or trial result. Startup
independent review is still pending here; no startup receipt is invented.

## November 16 renewal during diagnostic

Sole terminal review accepts routine renewal `41f2b246…`, with exact pointer
`08259365…` and fixed review due at 19:35:51.076328 UTC. Actual activation
finishes at 18:36:01.241083997 UTC before predecessor `3ce1251b…` expires at
18:50:30.227899. This interval is continuous; all historical expired gaps
remain recorded. All seven transports exit zero; two exact helpers exit
naturally with PID zero and no OOM. Fresh independent checks preserve all 12
protected services, root-owned 0444 single-link hold, three receipt readbacks
and the pointer/fence.

Terminal review in `/tmp/swingset-stage2-hold-review-20261116-183551` has
SHA-256 `448803349ce2bf815b1937bc370fdf6c08bedc8ef2a99360e6def885b830e1ee`.
Its [compact renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-183551.json)
binds actual source, command manifest, helper identities, result and verdict.
The diagnostic continues under its separate action review; renewal adds no
worker start, database or runtime authority.

## November 16 independent startup accepted

Sole startup review
`/tmp/swingset-stage2-unit75-independent-startup-20261116/independent-startup-review.json`,
SHA-256 `eb0d64628b4ef93d2f34b5983c2e1892df380cf2ad73b4cac3dba9e3afb15307`,
accepts the actual guarded one-owned-freeze startup. Its independent sample
runs from 18:39:26.386486 to 18:39:28.757558 UTC, confirming the actor's
18:33:10 start, PID 3116755, birth 1025042875, boot, cgroup and network
namespace 4026533551. Remote supervisor PID/group 3116243 has exact argv and
birth 1025042784. Local SSH PID 46604 / tool session 13584 is distinct.

Full public Config/HostConfig and 11 mounts, UID 10001, zero capabilities,
no-new-privileges, seccomp, kernel 8 GiB/no-swap/6 CPU/256 PID limits,
authorization and IPv4/IPv6 firewall match. Source schema 38, retained catalog,
UUID/checksum/size, guardian, native proof and protected services pass the
startup checks. Only complete unique-target mount ordering and the known
unconfigured `OomKillDisable` false/null representation are normalized; no
flag or resource bound is relaxed. OOM-free sampled counters do not prove
absence of pressure. The maintained startup gate and persisted early identity
remain distinct from this later independent sample.

Exact owned database `sst_copy_1d527dbfd220474abcd80cec_0`, OID 7209713,
has the matching ownership comment and registry token. Actual phase remains
`freeze_one_new_owned_database`: copy readiness is false. The installed
producer limit is 60 seconds and authorized owned-copy limit 75 seconds;
applied and restored values are still null, as expected before post-freeze
acceptance. The [compact startup receipt](../../evidence/runtime/postgres-stage2-2026-11-16/inventory75-diagnostic-92900be19500-startup.json)
binds the exact final plan, action review, stage/pulse, go, incarnations and
sole startup review. It claims no completed copy, acceptance, trial, final
source post-hash, cleanup or graph success. Final application/restoration of
the limit and terminal owned cleanup remain required.

The root starts a separate cache-only transfer/load at 18:50:48 UTC, tool
session 91256, using
`/tmp/swingset-stage2-runtime862-server-cache-20261116-v1/cache.py` and fresh
server root `/var/tmp/swingset-stage2-runtime862-20261116-1849`. The exact
248,929,280-byte archive and CONFIG `e68aa127…` are already qualified locally;
server success is not yet established. Its guards cover the 14 protected
containers including the old actor, hold `41f2b246…` and capacity. This is
cache work only, without guard changes, worker start or database writes.
The running `346f30a4…` diagnostic remains unchanged through terminal; no
server activation or final-runtime graph admission follows from loading.

## November 16 qualified runtime cached inactive on server

The one cache operation completes at 18:52:12.001772 UTC with all 15
transports exiting zero, empty stderr and no unknown outcomes. The exact
248,929,280-byte archive's server SHA-256 matches `8c2947f4…`. Docker load
produces portable CONFIG `e68aa127…`; linux/amd64 architecture, source and
dependency labels and root-filesystem layers match the qualified local OCI
artifact `08df0ebc…`. The fresh private cache root is
`/var/tmp/swingset-stage2-runtime862-20261116-1849`.

Pre/post checks preserve all 14 protected containers, actual old actor
`24154fe5…` / image `346f30a4…` / PID 3116755 / start 18:33:10.216488566,
and hold `41f2b246…`. Observed post-load available space is
546,091,073,536 bytes, above the conservative 508,224,930,112-byte bound.
The concurrent diagnostic remains unchanged; no allocation cause is inferred
from the capacity difference. No guard changes, database writes or worker
starts occur in this cache operation.

Raw result `/tmp/swingset-stage2-runtime862-server-cache-20261116-v1/result.json`
has SHA-256 `c7f818b4f26be2c42e42ba1b367a477c1f5bf3392e1bf4d2e12495e87c6021a6`.
The [compact cache receipt](../../evidence/runtime/postgres-stage2-2026-11-16/runtime862-server-cache-20261116.json)
binds the once-fenced exact commands, raw-output hashes, actual exits and local
qualification. The loaded image is inactive, not deployed. All 569 installed
files are proved only locally; no server installed-file proof, activation,
new role/network qualification or graph success is claimed. Raw archive,
commands and outputs stay in scratch; the old diagnostic still runs its freeze.

## November 16 owned freeze returns

At 19:05 UTC the safe public operator projection reports phase
`destination_graph_prelude`. The one owned freeze has returned; the
installed producer limit remains 60 seconds and the authorized owned-copy
limit is now applied at 75 seconds. No error is reported. Acceptance and
the inventory trial have not yet produced results; final restoration of the
installed limit, source post-binding and owned cleanup remain pending.
The original producer and guard remain unchanged. The qualified current
runtime stays cached inactive until this operation is terminal.

## November 16 diagnostic terminal: unit still fails

Actor `24154fe5…` ends naturally at 19:19:01.872041756 UTC, exit zero;
foreground session 13584 also exits zero. The diagnostic is complete, not
the inventory unit: ordinary input acceptance returns in 59.692072568 seconds
under the applied 75-second policy, then the instrumented inventory trial
fails with `WriteDeadlineExceeded` at 75.003533663 seconds. Observer quality,
rollback output/pointer fingerprint and run-row equality pass. The installed
60-second setting is restored. Source post-binding equals original source
`00c831a4…`, checked by the pinned operator; the reviewer does not repeat that
full fingerprint.

Capture occupies 40.238912268 wall seconds, including desired selection at
39.496054295; inventory preparation occupies 31.484422436. These are inclusive,
overlapping instrumented spans with unknown observer overhead. Do not sum
them, infer precise SQL causes or predict savings or normal runtime timing.
The strong prelude binds 108,390 units / D58 and candidate `5039effb…`;
that does not mean those units were verified by this one-unit diagnostic.

Sole terminal review
`/tmp/swingset-stage2-unit75-independent-terminal-20261116/independent-terminal-review.json`,
SHA-256 `6363fbc6e2f4a1fca6dfcd6455c61a20c94ea5d2c597a1400a9c503445677344`,
accepts diagnostic completion and exact owned cleanup while retaining the
failed trial. Owner `1d527dbfd220474abcd80cec` database `_0`, OID 7209713,
and copy backends are absent. Registry is root-only without files; actor,
supervisor, cgroup and authorization are retired. The five retained databases,
C696/69, schema-38 source UUID/checksum, native87, guardian and 12 applications
remain protected. The nonzero private pressure counter is retained without
attributing cause; no OOM is observed.

The stable root operator receipt has SHA-256
`3503ed70aec1313ec4f5f52c4a02c77a5d7a2e1f417fe969d699e13dd5120c3a`;
supervisor receipt SHA-256 is
`e08b3c832ac97dda4af4b4a15c8cf0585ddbf9e4bdab6065270109bc0ea3f52d`.
The [compact terminal receipt](../../evidence/runtime/postgres-stage2-2026-11-16/inventory75-diagnostic-92900be19500-terminal.json)
binds commands, source/producer, eight phase timings, raw receipt hashes,
counts and sole verdict. Raw reports stay in scratch. No unit, whole-graph,
performance, migration or publication success is claimed.

Keep 75 seconds and proceed to the qualified current runtime's first normal,
uninstrumented whole-graph proof through the existing two-process supervisor,
after separate actual admission and root go. Do not repeat this instrumented
trial or select an optimization from these coarse timings. A normal current
75-second failure would trigger bounded current-runtime attribution before a
source correction. Linker 10 exclusions use the existing automatic bump and
dependency-closure checks. The image-only guard proposal `88b52…` is local
and static; no guard action has occurred.

The later authoritative migration, re-key, drain and B target is shared
`swingset_dryrun`, OID 16398, UUID
`9bccc2d4-73b4-4ef3-894d-861213ef9831`, not private C696 or 69.
The route correction is
`/tmp/swingset-stage2-runtime862-wholegraph-route-20261116-v1/authoritative-rekey-target-addendum.json`,
SHA-256 `ce8a38c5ebd4dfe771929c5907cb9dabde378f29a2b219807fb2786ec2884bbe`.
Equal imported history does not substitute database identity or authorize
adoption; the shared target needs its own fresh role/source checks later.

## November 16 renewal before diagnostic terminal

Routine hold `30d84bfe…` activates at 19:11:41.221852049 UTC, before
`41f2b246…` expires, and fixes review at 20:11:29.608151 UTC. The current
interval is continuous; prior expired gaps remain historical. All seven
transports exit zero; both exact helpers exit naturally. Independent checks
preserve all 12 protected services, the root-owned 0444 single-link hold,
three receipt readbacks and pointer `9d19aaf2…`.

Sole renewal terminal review in
`/tmp/swingset-stage2-hold-review-20261116-191129` has SHA-256
`ae10a286e37a7fd2655864e789c6712cadcf01fbbf450391b135a72641917fe7`.
The [compact renewal receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-191129.json)
binds its actual source, commands, result, helper identities and review.
No extra runtime, database, guard-transition or cleanup authority follows.

## November 16 image-only guardian transition completes

The reviewed transition completes at 20:00:22.690076 UTC. All 49 exact
stock vectors run once with known outcomes: 46 exit zero and three exact
named-absence probes exit one. Four fresh pre-go reads and 14 bounded status
poll reads also pass. Both exact seal helpers exit naturally with complete
profiles and intent bounds. Final candidate plan `d790a672…`, unchanged
49-command vector hash `c25cbdf7…`, review `aa2b6a72…` and executor `2bb3558e…`
remain bound in the [one compact guard receipt](../../evidence/runtime/postgres-stage2-2026-11-16/runtime862-guard-transition-20261116.json).
Raw summary is
`/tmp/swingset-stage2-guard862-executor-20261116-v3/attempt-001/summary.json`,
SHA-256 `8d7c3c7eee7673aa9b6904539fca5e7dfab86b7f9ba2da23e81d9729c21f153e`.

New guardian
`a30fa1168013f8967cddd756f11a84086fdfa8fca9ddf03c717141d3b0b3e646`
starts at 19:59:50.047393731 UTC, PID 3777145, birth 1025562858,
network inode 4026533272, boot `0bc23f5d…`, with its exact container cgroup
and unchanged allowed kernel capabilities `0000000000281000`. Old guardian
CB is intentionally stopped at exit 143, PID zero, without OOM or restart,
and retained. New policy raw SHA-256 is
`cb6d548c295b28fc9bc738c0308e179aa2b92a3cd0372f36bb53e8e1e227bb96`;
canonical SHA-256 is
`49f56776a4ff76384ba3eaf00bfbc96bad19fa612f5ff9362c96c22aa0da07cc`.
Only image `346f30a4…` changes to `e68aa127…`. The staged-to-installed policy
retains device 2050 / inode 17862046 and root-owned 0444 single-link metadata;
the old B4 sealed copy remains retained.

Sole terminal review in the executor directory, SHA-256
`e5899f0895f48021f231c12493c700c7a98d0c1e9386db4b27e5d430b8338b9d`,
accepts the image-only transition. Source `862bbd75…` and all 956 inputs,
schema/UUID/checksum, five-database catalog, private C696/69, 12 protected
applications and native87 remain unchanged. No guarded ordinary worker runs.
Available space is 578,141,212,672 bytes. Status preserves
`complete_inventory=false`, count 37, resource exit zero and no observation
error; this is limited held observation, not normal application health.

The earlier v2 pre-go refusal is preserved at
`/tmp/swingset-stage2-guard862-executor-20261116-v2/pre-effect-refusal.json`,
SHA-256 `d9d86425dc2bcc0b6ca8e7dda3908f981b57e348211c8367049e48da5ec0d583`.
It is a local exact-newline/named-`NoSuchObject` predicate refusal, before root
go and all 49 effects. Fresh v3 pre-go reads use the explicitly bound successor
hold and corrected bounded parser; no completed effect is replayed. No graph,
ordinary worker start, SQL write, migration, adoption or cleanup occurs. The
new platform identity must bind separate next cold admission and normal graph
review. This transition is not Gate 2 success.

## November 16 hold renewal before guardian change

Root separately retains the accepted routine renewal as
[hold-review-20261116-195323.json](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-195323.json),
3,552 bytes, SHA-256
`b030379fac7a68da781169169f27f6eca4cdaf64131c796febb2b5accb482fbe`.
Sole terminal verdict SHA-256 is
`a46ac1c714c169a0dc553274dae35505ff5033752004b3314e2265d40e399d6c`.
Hold `e7cc00da…` activates at 19:53:35.192882774 UTC before the prior 20:11
expiry; review is fixed at 20:53:23.777023 UTC. This interval is continuous,
with earlier gaps preserved. The seven transports, two exact helpers,
protected services, hold metadata, three receipt readbacks and pointer pass.
No duplicate renewal receipt or additional runtime authority is created here.

## November 16 current-runtime normal whole graph starts

The first normal, uninstrumented whole graph on source `862bbd75…`, portable
CONFIG `e68aa127…`, Linker 10 and 75-second unit writes starts at 20:34:37 UTC
with two verification processes. The [machine-generated startup receipt](../../evidence/runtime/postgres-stage2-2026-11-16/normal75-graph-d1f0059d2cc2-startup.json)
is 8,709 bytes, SHA-256
`2a441b47272e536d1a3f6f5d4f74e3560a1eb084ce233eacbc963509577f7134`.
It binds actual 108-row cold preparation, 16 helper outcomes, four fresh
volumes, six RAM-only booleans and sole preparation/startup reviews `491b…`
and `8b395f28…`. Scratch predicate refusals and exact local reconciliations
remain preserved without effect replay.

Actual actor `c962…` has PID 4038335, birth 1025771591 and network namespace
4026533552; its supervisor is PID 4037849. Owner token
`2340bacbea074f148234acf0` names one owned `_0` database, OID 7659662.
The accepted startup observes freeze in progress, not copy readiness. No
schema-40 completion, input acceptance, unit result, graph pass, re-key or
cleanup is claimed. The protected C696/69 bases are not migration targets.

The read-only strong input-acceptance projection does not support the deliberate
Linker 9→10 change, so a null predicted binding is expected. Verification must
record its actual post-acceptance checkpoint, selected generations, unit set
and audit/version/input context. Later shared re-key must match those exact
values, not the old diagnostic's `5039…` checkpoint or a guessed transition.
The 500 explained differences and two failures in the older graph, and the
failed instrumented 75-second inventory trial, retain their historical outcomes;
they neither establish nor predict this run's result.

The [20:39 renewal](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-203956.json)
advances the accepted hold to `75de783d…`, due at 21:39:56.272175 UTC.
Installer and rename transport timeouts retain their unknown transport exits;
exact helper terminal checks independently establish natural exit zero.
Maintained remaining-only continuations perform steps 3–6, then receipt step 6,
without repeating either helper. Rename finishes at 20:44:36.188 UTC before
the prior 20:53:23 expiry. Sole terminal review `a0c03e8b…` accepts the two
helpers, installed root-owned hold, three receipt readbacks, pointer and 12
protected services. This preserves the current valid interval; historical gaps
remain. Graph authority and limits are unchanged.

## November 16 current graph context and ARM artifact

The initial current graph receipt is projected at 21:21:28 UTC, after prelude
and ordinary input acceptance. Source `00c831a4…` now binds actual checkpoint
`5039effb…` and Linker 10. This numeric checkpoint matches older observations,
but the current run recomputes it; no old receipt or unsupported prediction is
substituted. Audit has 52 entries, versions 98 and inputs 113, each with its
captured digest. The expected 108,390-unit set recomputes D58 exactly. Results
are zero, errors zero and completion false. The later observer's database
count rising from three to four proves only that count, not two ready shards
or completed results. No graph, migration or re-key success follows.

The [compact initial projection](../../evidence/runtime/postgres-stage2-2026-11-16/normal75-graph-d1f0059d2cc2-initial-projection.json)
binds scratch wrapper SHA-256
`689ef17ca37c68952471d58eed9e9206a7c0e465860f427977928345b092a12f`
and the public 4,357,055-byte receipt hash
`6dcbd149de8bfaf5bffc968961d4b78c23e0ee5d47ff533ddca26dfcb5af008e`.
The full receipt and projection code remain scratch. The strong predicted
binding is unavailable because the projector supports code-input changes,
not this Linker 9→10 transition. Final acceptance must use the real completed
receipt and context. Older graph failures and the failed instrumented
75-second inventory trial retain their outcomes.

The [ARM qualification](../../evidence/runtime/postgres-stage2-2026-11-16/runtime862-arm64-qualification-20261116.json)
completes at 21:15:11 UTC on source `862bbd75…` and unchanged 956 inputs.
The 569 public files and 376 Python files, duplicate app source, OCI hashes
and dependency prefix match. Artifact `dd7ff201…`, CONFIG `f620b010…` and
ARM dependency `63354d08…` are distinct from the running AMD64 artifact.
Archive size is 241,715,200 bytes, SHA-256
`8cb5cf743e1c590f91f2c1c2e53bad4a11d7e7353ffb35b7e9447d802682611c`.
The proof container remains Created and never started; builder limits stay
2 CPUs, 2 GiB RAM, 4 GiB memory plus swap and 256 PIDs. Minimum observed free
space is 143,979,429,888 bytes. This proves local artifact bytes, not ARM
business equivalence, reference parity or activation. The fresh R and clock
route remains a proposal; no VM or R operation is performed here.

## November 16 ordinary renewal at 21:15

The [routine renewal](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-211540.json)
activates at 21:16:30.026680042 UTC before predecessor expiry 21:39:56.272175.
Hold `f88d3447…` is due at 22:15:40.821765 UTC. All seven transports and both
exact helpers exit zero; protected services, installed root-owned hold,
three receipt readbacks and pointer pass sole terminal review
`46a67fb63c91251ddde9d9727781494787f76e2f3814e314a7f0194b59a59a5c`.
This renewal has no transport timeout. Only the current interval is continuous;
the historical 318,292.822363-second expired gap remains. Existing graph
scope and limits are unchanged, with no new runtime authority.

## November 16 frozen R0 control eligibility

A fresh read-only worker code hash matches R0's frozen production source
`b6cc9c28f977502686e63b9dfdaf57ab7106904b72d3ac67e54816fea8ec70b4`
across 290 Python files. Five preserved public controls also match their
original hashes: export verification, BUILT, release manifest, full source
result and build policy. The [compact recheck receipt](../../evidence/runtime/postgres-stage2-2026-11-16/reference-R0-source-control-recheck-20261116.json)
binds exact read intents/results and payload hashes; public source and full
control bodies stay in scratch. No graph or production state changes.

The original Stage2 contract freezes production code, names C's recovery as
one full re-derivation, then compares B/C/R published tables. R0 is therefore
eligible as the unchanged legacy production-code oracle, conditional on the
actual strict published-table comparison and its named operational-column
exceptions. It does not need to be relabeled current862. The stronger current
ARM full replay proposal remains unselected; ARM qualification is preparatory
evidence, not an extra recovery gate.

R0's manifest has ordinary built_at `2026-11-05T23:52:36.243361+00:00`,
evidence cutoff `2026-11-05T03:53:55.691210+00:00` and partitioned release
path, with no sealed render field. These two timestamps remain distinct;
neither is an inferred rendering instant. The original rule requires strict
published bytes, not an additional identical-clock or identical-port-code
prerequisite. Existing R0 may pass actual B/C/R comparison directly. If a
business-column mismatch exists, even a known clock or float explanation
blocks acceptance; a necessary fresh legacy rerender needs separate reviewed
support, never a comparison waiver. The maintained reference command has no
fixed-cutoff option today.

This first operation refreshes source and control hashes only. A subsequent
[read-only payload check](../../evidence/runtime/postgres-stage2-2026-11-16/reference-R0-payload-freshness-20261116.json)
at 22:36 UTC freshly hashes all 59 known exported files, including every Parquet
file: all 381,686,344 bytes match the original export hashes. Exact root
identity, no-symlink path traversal and unchanged file metadata bound the reads.
The transport exits zero without stderr. Actual strict B/C/R comparison remains
pending; no R0 parity pass, new reference job or graph completion is claimed.
Local closure checking also matches all 38 manifest paths and hashes. The
candidate has 40 files including BUILT and its manifest, with 35 Parquet files.
The earlier 53-file payload count includes 13 controls; six receipts bring
the full export to 59 files. It does not mean 53 candidate files.

## November 16 ordinary renewal at 21:50

The [routine renewal](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-215026.json)
activates at 21:50:37.341265562 UTC before predecessor expiry
22:15:40.821765 UTC. Current hold `2f654459…` is due at
22:50:26.732798 UTC. All seven transports exit zero without unknown outcomes;
both helpers exit naturally with code zero. The 12 protected services,
root-owned installed hold, three receipt readbacks and pointer pass sole
terminal review `b422302f…`. Only this interval is continuous; earlier expired
gaps remain historical. Renewal changes no graph scope, limits or runtime
authority.

## November 16 shared read-only audit is cold-prepared

At 22:13:35 UTC, the [cold preparation](../../evidence/runtime/postgres-stage2-2026-11-16/shared-readonly-audit-b96f3fe7b551-cold-preparation.json)
completes all 65 bounded transport outcomes with exit zero and no unknown
result. Six helpers exit naturally with code zero; their complete profiles
and individual intent-time brackets pass. Actor `8e89e54f…` remains Created,
PID0, never started, on current AMD64 CONFIG `e68aa127…`. Original shared
state is read-only, only the original host worker DSN is mounted, and the
fresh output volume has UID10001/mode0700. The two public files are sealed
root0/mode0444/hardlink1 with unchanged file identities; their directory is
root0/mode0555. Three RAM-only environment booleans pass without exporting
raw Env or credential bodies/digests. Worker-DSN proof is numeric metadata
only; no endpoint, role or typed-data result follows.

V3 audit script SHA-256
`0cbeacdaa194c98ec0b7527f82faf88ec8c109c5266bf876e9d5e807bd8b659d`
differs from unexecuted V2 only in distinct `owner_usage`, `worker_usage`
and `control_usage` SQL aliases. The exact original 47,608-byte import receipt
`56aa88e8…` is sealed beside it. Its 141-table matrix will supply the actual
typed comparison. The new audit's selected 7,200-second total wall ceiling
includes namespace authorization wait; reader, statement, lock and 75-second
write policies remain unchanged. This is not a raised 300-second typed-audit
gate: the older 300-second inspection checked metadata and counts only.

The actual summary hash is
`6a89bccd7564e8fe505d9a29bc25e00c08b978897ccfcea9437491ca5b278d94`;
manifest `221fa5a2…` and vectors `2f4902b3…` pin the scratch records.
The actor is parked until the normal graph terminates, owned cleanup passes
and fresh ordinary admission is complete. It has run no audit scan, migration,
acceptance, re-key or deletion. Other graph/database activity continued:
the 22:16 observer records actor and supervisor alive, five databases and
incomplete status, not a terminal graph result. ARM remains inactive and the
frozen R0 reference contract is unchanged; no additional replay is selected.

## November 16 routine renewal at 22:27

The [renewal11 receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-222708.json)
binds all seven successful transports and two exact helpers ending naturally
at exit zero. Activation `2026-11-16T22:27:19.087513776Z` precedes the prior
22:50:26.732798 expiry. New hold `ba19b2bf…` is due at 23:27:08.902352 UTC;
only this interval is continuous, with historical expired gaps preserved.
Sole terminal review SHA-256
`d6b50f4c21e85916a2bc74725bf7adbe408b61d66cecfc4b347efc0e2e12522a`
accepts installed root-owned 0444/single-link hold, protected12, three receipt
readbacks and current pointer. Graph scope is unchanged and completion is
still pending; this administrative renewal supplies no new worker, database,
guard or cleanup authority. R0 remains eligible only subject to actual strict
B/C/R published-table comparison; no parity pass is added.

## November 16 routine renewal at 22:55

The [renewal12 receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-225458.json)
records all seven transports and both exact helpers ending naturally at zero.
Activation `2026-11-16T22:55:09.763303361Z` precedes the prior 23:27:08 expiry.
Hold `759cc94b…` is due at 23:54:58 UTC. Independent terminal verdict
`9df6892f33340d956915cbab0cd6394dbbe100782fd68240f87a77be21b9eff0`
accepts protected12, installed hold, three remote receipts and pointer.
Only this interval is continuous; historical expired gaps remain. The graph
continues without a terminal result. No new actor, database, guard or cleanup
authority follows from this administrative renewal.

## November 16 routine renewal at 23:18

The [renewal13 receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-231839.json)
records seven successful transports and two exact helpers ending naturally at
zero. Activation `2026-11-16T23:18:49.322030183Z` precedes the prior 23:54:58
expiry. Hold `d4a95834…` is due November 17 00:18:39 UTC. Independent terminal
verdict `100e54080b25cfb5a3a1d113add664210d6cdea897d16de9bec259943411d706`
accepts protected12, installed hold, three receipts and pointer. Historical
expired gaps remain; only this interval is continuous. This renewal adds no
runtime authority. The original 143-sample observer ends naturally at its
23:17:17 bound with no alert; a separate authorized read-only window continues
through November 17 01:17:17 UTC. The graph remains running and incomplete.

## November 16 current normal graph fails

The normal current862/e68/Linker10 graph ends naturally at
`2026-11-16T23:34:02.500419185Z`, exit1/PID0, no OOM/restart, after about
three hours. All 108,390 expected unit identities return uniquely: 38,070
matches, 107 undeclared mismatches and 70,213 failures. Failures comprise
67 WriteDeadlineExceeded75 and 70,146 ConnectionLost. Shard errors are empty,
but complete/clean remain false. Exact coverage proves identities, not passing
pipeline outputs. The [compact terminal receipt](../../evidence/runtime/postgres-stage2-2026-11-16/normal75-graph-d1f0059d2cc2-terminal.json)
binds full scratch receipt SHA-256
`4641500c6e332b32559fa63c5f7524ac737cfa11be3e3abd9f38e2d800ec94f4`
(31,657,966 bytes) and sole terminal verdict
`d37198578ab7b9993d5c205f8d8df6eb167cdbcd7b3ecc8ecfe016ea7c3c8ec7`.

Each shard holds one Database. Its final deadline leaves the session closed;
roughly 35,000 following units per shard then report identical ConnectionLost.
The propagation is established, while the initial closure cause remains
unproved. All 67 actual overruns remain failed. Generic supervisor ValueError
and peer/disk stop wording do not identify the cause; no supervisor stop is
attempted. The normal75 failure triggers bounded current-runtime attribution;
no raised limit, optimization, unchanged replay or shared migration/accept/
re-key follows. Earlier failed instrumented trials retain their outcomes.

Independent terminal review accepts only protected-source preservation and
automatic cleanup: all three owned2340 databases, backends, empty ledger,
processes/cgroup and authorization are retired. C696/69/native38, original
five-database catalog, protected14 and guard remain exact. Minimum observed
free space is 458,054,397,952 bytes; sampled verifier charge peaks at
2,904,936,448 bytes, private charge8,592,605,184. No OOM is observed, without
claiming absence of memory pressure. Full source checkpoint is not redundantly
rehashed; the audited identity/protocol boundary is the proof scope.

Recovery C preparation completes only rows0–9 (sealed files/environment/new
bridge), pauses before10 and retains all partials. No PG volume, PG, SQL,
restore or publication has run. Original deadline stays23:59:34 UTC; scratch
summary is `ecefe9fd267d4c8ada95a2bfc8202abb7d36ca06630fb525152c829f7afd91d0`.
Current d4a9 hold remains due November17 00:18:39UTC. R0 eligibility remains
conditional on actual strict B/C/R published comparison; no parity or recovery
pass is claimed.

## November 16 routine renewal at 23:54

The [renewal14 receipt](../../evidence/runtime/postgres-stage2-2026-11-16/hold-review-20261116-235353.json)
records seven successful transports and two exact natural-zero helpers.
Activation `2026-11-16T23:54:04.223417782Z` precedes the previous 00:18:39
expiry. Hold `7f59242e…` is due November 17 00:53:53 UTC. Sole terminal review
`4b9a4b977de8f8a7602a7cc9d1b92655174b159742fd0895ea7b84969591e16f`
accepts protected12, installed hold, readbacks and pointer. Historical gaps
remain. Only the unchanged hold protocol source is qualified by this renewal;
it does not claim the edited working copy still matches source862/956.

## Closed-session correctness fixes under development

The shard fix preserves completed results and the real failure, stops after
session loss and lists the remaining units as unattempted. Aborted receipts
remain rejected; no reconnect or retry is added. All 32 verifier tests pass,
including actual owned-backend termination and recoverable timeout coverage.
The deadline fix reports ConnectionLost with rollback unconfirmed when an
expired write has a closed connection. Commit uncertainty and external
interruptions retain priority. All 35 deadline tests pass. Focused Ruff and
mypy checks pass; the complete core check is running. No limit or linker
computation changes, deployment or deadline-performance claim follows.

A separate read-only 956-file, 37,262,735-byte source tree retains the exact
qualified862 input binding `8e837142…` for diagnosis. Its manifest SHA-256 is
`9825ebf3713c934e5f7a4873bfa467f8added238db191aa3cc57f31b1984c86f`.
The new fixes do not alter that frozen producer or the failed graph receipt.

## November 17 local closed-session core qualification

The [complete core check](../../evidence/runtime/postgres-stage2-2026-11-16/closed-session-correctness-core.json)
passes all 3,713 selected cases in 249.48 seconds under the unchanged
300-second gate. Ruff and mypy pass for 376 source files. Source `b06416a2…`
and binding `32203bc6…` qualify the two local correctness fixes; the extended
suite and any new runtime deployment remain pending. This neither repairs the
67 measured link-unit overruns nor accepts the failed graph. The original862
producer remains unchanged for attribution. Core raw reports stay outside the
repository. The earlier C preparation window expires at 23:59:34 UTC with only
its known files, terminal sealing helpers and isolated bridge retained; no
remaining effect, PG daemon or SQL is started.

## November 17 local extended qualification and hold renewal

The [extended-suite receipt](../../evidence/runtime/postgres-stage2-2026-11-17/closed-session-correctness-full.json)
records all 5,293 cases passing in 1,516 seconds with four workers and zero
failures, errors or skips. Source `b06416a2…` and the 956-file binding
`32203bc6…` remain unchanged. The isolated ownership registry is empty;
observed disk free stays above 80 GiB. This qualifies the local correctness
fixes without deploying them or resolving the 67 measured overruns.

The [renewal15 receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-003513.json)
records activation at 00:35:23 UTC, before the previous expiry. Hold
`17bd0c09…` is due at 01:35:13 UTC. Sole terminal review accepts seven
transports, both natural-zero helpers, protected12, three readbacks and
pointer. Only this interval is continuous; historical gaps remain.

## November 17 renewal at 00:58

The [renewal16 receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-005839.json)
records activation at 00:58:50 UTC before the prior expiry. Hold `285686f1…`
is due at 01:58:39 UTC. Sole terminal review accepts seven transports, both
natural-zero helpers, protected12, three receipt readbacks and pointer.
Historical gaps remain; this grants no additional runtime authority.

## November 17 one-link diagnostic starts

The [startup receipt](../../evidence/runtime/postgres-stage2-2026-11-17/one-link-b2819cfa7563-startup.json)
records one foreground launch at 01:33:52 UTC. Corrected operator `c3506191…`
uses frozen862/e68/Linker10, native75, report90 and one fresh owned copy.
The full-source pre-binding passes before freeze. Independent startup review
accepts actor PID2471234/birth1027567070, supervisor2470961/birth1027566973,
authorization/firewalls/resource limits and unchanged protected sources.
Known owner `2db67a13…`, OID8109605, is still freezing at observation; no
copy-ready, accepted5039, trial, rollback or cleanup is claimed. Earlier cold
actors remain never started. Raw refusal and correction records stay in
scratch; no deadline or producer-source change follows.

## November 17 renewal with two reconciled transport timeouts

The [renewal17 receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-013846.json)
retains TimeoutExpired/null transport outcomes for installer2 and rename5.
Exact helper terminal checks establish natural exit zero. Maintained status
and sole remaining-only reviews allow3–6, then6; neither helper is replayed.
Activation at 01:44:03 UTC precedes the prior01:58:39 expiry. Hold `c0448dfa…`
is due at02:38:46 UTC. Final independent review accepts both full helper
profiles, five other transport exits zero, protected12, three receipt
readbacks and pointer. Only this interval is continuous; historical gaps
remain. No new C or runtime authority is added.

## November 17 renewal at 02:03

The [renewal18 receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-015916.json)
retains the installer transport timeout/null exit, with exact natural-zero
helper outcome established separately. Sole remaining3–6 review permits
continuation without replay. Rename and finalization transports exit zero.
Activation02:03:41 UTC precedes the prior02:38:46 expiry; `243dc796…` is due
at02:59:16 UTC. Both full helper profiles, six other transports, protected12,
three receipt readbacks and pointer pass final independent review. Historical
gaps remain. C still needs its separate remaining-only admission and root GO.

## November 17 current qualification and C partial

Native41 passes all 47 schema cases and the complete3,716-case core in
198.79 seconds, including worker/history/rollback and unknown-future
checkpoint refusal. Source BE567/957 inputs remains bound; extended tests
are running. No server switch or normal75 speedup is claimed
([core](../../evidence/runtime/postgres-stage2-2026-11-17/native41-core-validation.json)).

C provisioning fails at scoped authentication after roles-v2 succeeds.
The exact owned PG stops cleanly and all partials remain; committed roles
and a C database may exist and require reconciliation. No regeneration,
reprovision or restore is authorized
([partial](../../evidence/runtime/postgres-stage2-2026-11-17/recovery-C-bootstrap-partial.json)).

Renewals19/20 both pass seven transports and both natural-zero helpers.
Latest248f activates02:59:08 before prior03:27:30 expiry, due03:58:57.
Readbacks, pointer and protected12 pass sole review. Historical gaps remain
([19](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-022730.json),
[20](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-025857.json)).

## November 17 native41 qualification and empty C preparation

Current source BE567 passes all 5,296 extended cases in 1,272.28 seconds,
with its 957 inputs unchanged. Corrected public build-context modes preserve
the old dependency artifact B706. Runtime449, portable CONFIG2eb, passes
all OCI hashes and all 570 installed public-file hashes. Its 248,965,632-byte
archive is cached on the server and loaded inactive. The superseded V1
candidate remains inactive. No R2 source artifacts have been uploaded;
previous provider probes were only 36-byte credential tests, then removed
([full](../../evidence/runtime/postgres-stage2-2026-11-17/native41-full-validation.json),
[runtime](../../evidence/runtime/postgres-stage2-2026-11-17/native41-runtime-qualification-cache.json)).

C's read-only facts establish its actual database and roles. A separately
reviewed remaining-only completion authenticates all three existing scoped
logins, revokes public CREATE and prepares initial empty tables in the same
OID 16394. All 132 phase-two transports and all three helpers exit zero;
the exact PG is cleanly stopped. Failed partials and credentials remain.
Its fsync/full_page_writes settings remain off: this is empty preparation,
not durability, restore or recovery proof
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/recovery-C-empty-preparation.json)).

Hold21 activates at 03:38:40 before the prior 03:58:57 expiry and is due
04:38:30 UTC. Seven transports, two helpers, protected services, receipt
readbacks and pointer pass independent review. Only this interval is
continuous; historical gaps remain
([renewal](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-033830.json)).

Hold22 activates at 04:06:09 before the prior 04:38:30 expiry, due
05:05:58 UTC. The same seven-step administrative protocol passes all
transport, helper, readback and protected-service checks. It is the explicit
accepted successor for the separately reviewed image-only guard transition;
no transition effect preceded this renewal
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-040558.json)).

The current-source recovery route has one additional compatibility boundary:
C's empty prepared comment binds native40's full migration checksum. Native41
correctly refuses it. The table-phase and role checksums are unchanged;
current migration checksum is `766df1cc…`. A local, unadmitted helper draft
checks the exact old marker, catalog, roles, zero rows and durable PG settings
before changing only that prepared comment. Normal strict adoption remains
unchanged. No helper, PG replacement, rebind or restore has run.

## November 17 native41 image authorization

All 49 reviewed stock commands pass once: 46 exit zero and three return
exact expected absence. Two policy sealers finish naturally. A30 stops
intentionally with exit143 and remains retained. New guardian `0d9661f9…`,
PID3893757/birth1028554607, authorizes CONFIG2eb through image-only policy
`95f296c6…` / canonical `5877adfa…`. The first bounded status sample succeeds;
its held inventory remains a limited observation. Source, protected14,
original catalog and Shared identity remain unchanged. Independent review
accepts this guard transition, supplying no worker or graph authority.
The actual final raw validation finishes 79.186 seconds after start intent;
the summary's later monotonic sample is assembly-time provenance, not the
operation's finish. Raw records remain unchanged
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/native41-guard-image-transition.json)).

Hold23 activates at 04:27:59 before prior expiry05:05:58 and is due
05:27:48 UTC. All seven transports, both exact helpers, receipt readbacks,
pointer and protected services pass independent review. The guard operation
remains bound to historical22; its final independent capture preceded this
routine progression. No graph or C authority follows from the renewal
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-042748.json)).

Independent review finds that the first durable-PG draft omitted the flush
required when changing fsync from off to on. The corrected local route adds
scoped offline `initdb --sync-only` on the owned C volume, using the same
pinned PG image and no normal initialization, before starting durable PG.
The settings readback remains necessary; it alone proves no flush of prior
kernel buffers. Nothing has executed. The empty-marker rebind remains a
compatibility step, not native41 migration completion
([PG17 fsync](https://www.postgresql.org/docs/17/runtime-config-wal.html#GUC-FSYNC),
[sync-only](https://www.postgresql.org/docs/17/app-initdb.html)).

## November 17 normal native41 verifier prepared

All 108 stock preparation rows pass once: 91 exit zero and 17 return exact
expected named absence. All 16 helpers finish naturally, four new volumes
pass, and the copied inputs contain 1,689 files and 69,548,306 bytes. Quiet
diff and all eight private numeric metadata rows match. Six remote-RAM
booleans pass without exporting credential bodies or Config.Env. Actor
`4f4c958d…` remains Created/PID0 and never started. Independent postcheck
accepts preparation, supplying no graph or SQL-cleanup authority.
The GNU timeout covers each docker-inspect stage, not its downstream jq;
both actual pipelines complete naturally in under one second. No repeat is
needed, and no whole-pipeline deadline is claimed. The initial wrong
network-ID read and corrected name lookup remain preserved; no mutation
was replayed. SourceFS provenance is the accepted historical witness plus
fresh identity/no-RW continuity, not new source-helper execution
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/native41-normal-graph-cold-preparation.json)).

Hold24 activates at 05:00:05 before prior expiry05:27:48, due05:59:55 UTC.
All seven transports, both helpers, three readbacks, pointer and protected
services pass independent review. Cold preparation keeps historical23;
later launch must bind actual24 and its fresh admission. The renewal
supplies no launch authority
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-045955.json)).

## November 17 native41 normal graph starts

Root dispatches the unchanged maintained supervisor once at 05:20:44 UTC.
Actor `4f4c958d…` starts at 05:20:45, PID204672/birth1028928416; supervisor
PID204393/birth1028928325 runs as customer1000, with the exact planned argv.
The local foreground SSH is PID2847/birthNovember17 00:20:44 ET/PGID2847,
tool session29157. Independent startup accepts full profiles, kernel limits,
authorization and IPv4/IPv6 first-output chains, source identity and all
protected services. Registry owner `35f6faf8…` owns only new `_0`, OID8559526.
Freeze is active; COPY IS NOT READY. No accepted checkpoint, schema41-ready
copy or graph result is claimed. Runtime75/Linker10/two processes/all108390
remain unchanged. No provider traffic or original-source write is performed
([startup](../../evidence/runtime/postgres-stage2-2026-11-17/native41-normal-graph-startup.json)).

Hold25's installer transport times out at step2; the exact helper later
finishes naturally with exit0. Maintained read-only status establishes that
outcome, and the same reviewer admits only unattempted3–6. Resume succeeds
without installer replay. Activation05:42:19 precedes old expiry05:59:55;
current review is due06:35:20 UTC. Six transport exits zero and one timeout/null
remain distinct from two natural-zero helpers. Readbacks, protected services
and pointer pass terminal review. No graph or source authority is added
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-053520.json)).

## November 17 current initial binding and real native41 indexes

At 06:06 UTC, the actual initial header emits checkpoint `5039effb…` and
source `00c831a4…`, with business clock05:20:58.846978 UTC. This is a fresh
read, not substitution of the earlier producer's same checkpoint. Binding
is null because checkpoint projection supports only code-input changes.
Audit52/versions98/inputs113 have combined canonical SHA `2dd0e113…`.
All108390 expected identities recompute D58; initial results0/errors0 and
complete/cleanfalse supply no graph completion. Raw4,357,075-byte initial
receipt stays in scratch, SHA `a8461d0d…`; its compact header SHA is
`ad9e62fd…` in `/tmp/swingset-stage2-graph41-initial-public-header-20261117-v1`.

FILE_COPY was observed directly at06:02, begun05:55. Both worker databases
are active by06:15. A later bounded catalog observation proves owned0/1/2
OIDs8559526/9009488/9009489 and exact comments. Only after no active clone,
a single read-only frozen0 connection confirms native41/source37/checksum
`766df1cc…`, DEV UUID `dc8422ac…` and both valid, ready, nonunique subject-ID
indexes. No worker1/2/base/Shared connections, scans, fingerprints, explicit
locks or writes occur. No graph outcome is inferred
([metadata](../../evidence/runtime/postgres-stage2-2026-11-17/native41-normal-graph-midrun-metadata.json)).

Hold26 activates06:12:15 before old expiry06:35:20 and is due07:11:30 UTC.
All seven transports, both helpers, protected services, readbacks and pointer
pass. The previous installer timeout remains historical. Session ownership
renews06:11; finite awake helper9/PID225 expires07:00:29 UTC, after exact
owned helper8 retirement. No extra graph authority follows from renewal
([hold](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-061130.json)).

Hold27 activates06:51:03 before expiry07:11:30 and is due07:50:49 UTC.
All seven transports, both helpers, receipt readbacks, pointer and protected
services pass independent review. The graph remains under its existing
review754a; no extra runtime/source/provider authority is added
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-065049.json)).

Hold28 activates07:30:39 before expiry07:50:49 and is due08:30:23 UTC.
All seven transports, both helpers, readbacks, pointer and protected services
pass independent review. Existing graph authority stays unchanged
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-073023.json)).

Hold29 activates08:14:23 before expiry08:30:23 and is due09:14:13 UTC.
All seven transports, both helpers, readbacks, pointer and protected services
pass independent review. This renews only the existing administrative hold
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-081413.json)).

Hold30 activates08:52:39 before expiry09:14:13 and is due09:52:29 UTC.
All seven transports, both helpers, readbacks, pointer and protected services
pass independent review. Existing graph authority remains unchanged
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-085229.json)).

### Hold31 renewal, 2026-11-17

Hold31 activates09:30:55 before expiry09:52:29 and is due10:30:45 UTC.
All seven transports exit0; both exact helpers end naturally, all twelve
protected services match, and three receipt readbacks and the pointer pass
independent review. Current hold hash is `67c5c0e3…`; graph authority stays
under754a. Earlier expired gaps remain historical.

The JSON receipt and independent verdict stay in scratch, as required for
rehearsal output by the [evidence rules](../../evidence/README.md).
Packet: `/tmp/swingset-stage2-hold-review-20261117-093045`.
Independent verdict `independent-terminal-review.json` has SHA-256
`4d0a77686093dc64407d1e57a41bbfcd8df7be8f0a29c7dc9be28ae726dcb342`.
The existing change retains 198 of its 200 KiB evidence budget; none of that
sealed evidence is rewritten or removed.

Session ownership renews09:39:17 UTC under the same label. Finite awake
helper12/PID33851 starts09:52:58 and expires11:52:58 UTC; its exact UID501,
birth and command match. The new private pointer is published before owned
helper11/PID23460 receives TERM; its foreground returns143 and its PID is
absent. The graph's original foreground29157 remains running.

### Hold32 renewal, 2026-11-17

Hold32 activates10:08:47 before expiry10:30:45 and is due11:08:37 UTC.
All seven transports, both exact helpers, twelve protected services, three
receipt readbacks and the pointer pass independent review. The scratch
packet is `/tmp/swingset-stage2-hold-review-20261117-100837`; its
`independent-terminal-review.json` has SHA-256
`44cb6b526cf306d2ae0b7d57dbdf7552f870b5801b35bc194b66fd37e140bba0`.
Current hold hash is `4f974bf9…`; existing graph authority stays under754a.

### Hold33 renewal, 2026-11-17

Hold33 activates10:46:22 before expiry11:08:37 and is due11:46:13 UTC.
All seven transports, both exact helpers, twelve protected services, three
receipt readbacks and the pointer pass independent review. The scratch
packet is `/tmp/swingset-stage2-hold-review-20261117-104613`; its
`independent-terminal-review.json` has SHA-256
`66a7a6834b97a3ee31177ce8feeca62005b65a7bab7085830f5dbc3ef7569d66`.
Current hold hash is `7c1f4414…`; existing graph authority stays under754a.

### Native41 normal graph terminal, 2026-11-17

The original foreground29157 ends naturally with exit1. Actor4f4 ends at
10:56:37 UTC, PID0, no OOM or restart; no supervisor stop is attempted.
The graph reports20,121.958 seconds and2,317,701,120 peak worker RSS bytes.
Its108,390 unique expected identities retain D58. Results are107,674 unique
identities with no extras:107,007 match,653 mismatch and14 failed.
Thirteen link units exceed native75 with rollback messages. The remaining
failure is `project/inventory/all`, ConnectionLost, global index106957 /
shard1 / local53478. Its abort names exactly the716 missing identities.
The generic supervisor ValueError/peer-or-disk label is not a cause.

The actual source00c, checkpoint5039, clock05:20:58.846978 and context
hashes5cc5/86db/d66a/2dd0 match the fresh initial header. Predictive binding
remains unsupported/null. None of the653 mismatches has a declared bump:
402 link labels differ with matching dependencies;3 link labels and their
dependencies differ;246 resolution and2 year labels match but dependencies
differ. Every prior862 mismatch persists with identical compared fields;
the other546 were previously failed, so this establishes no new regression
or blanket explanation. Exact old500 overlap cannot be checked from missing
scratch witnesses. Every difference remains unaccepted.

The captured63,359,851-byte receipt stays at
`/tmp/swingset-stage2-graph41-terminal-root-20261117-v1/receipt.json`, SHA-256
`1368526ea8a0eb64540b8aa8994948059316a966d98a909e93c2970f6b1f7fe4`.
It matches the server copy. Independent terminal review in that directory
has SHA-256
`26ff27d79068271f02cd21f5c413c3eaea04d24b53d9cc2c6818d2a87e669534`.
The review rejects graph/Gate2/re-key and accepts only source/protected
preservation and automatic owned cleanup. All three owned35f6 databases,
their backends, registry files, processes, cgroup and authorization are gone;
the original five-database catalog, C696/69/Shared, protected services and
retained actors match. No manual cleanup or full source rehash is performed.
Minimum observed free space is455.666 GB, above200 GB. Private PG records
zero OOM events/kills despite memory pressure; no stronger cause follows.

Local classification is sealed at
`/var/folders/kc/94jncx6s6pqc8g2g4f2kyvtr0000gn/T/swingset-stage2-graph41-terminal-analysis-20261117-bv7l06sy/analysis.json`,
SHA-256 `8d32f310fa98f63c58d0fe72588c177939c2ae72b2467fe44a80e3920bd6e031`.
A bounded PG-log capture finds ten statement-timeout records and none of
the selected transaction/idle-timeout, OOM/restart or client-disconnect
phrases. It does not establish the inventory connection-loss cause. Raw
logs stay private/unhashed; the filtered class report stays in scratch.

The plan now selects one current Native41 inventory attribution through
the existing maintained operator and supervisor, using immutable BE567 and
actual accepted context. This is the measurement trigger, not a selected
performance fix or an unchanged graph retry. No R2 source-artifact upload,
Shared migration, re-key, publication or recovery has occurred.

### Hold34 renewal, 2026-11-17

Hold34 activates at11:25:30 UTC, before the previous review deadline at
11:46:13. Its next review is due12:25:20 UTC. All seven transports, both
exact helpers, twelve protected services, three readbacks and the pointer
pass independent review. The scratch packet is
`/tmp/swingset-stage2-hold-review-20261117-112520`; its
`independent-terminal-review.json` has SHA-256
`dc2690ec7999e4023658fb0fd052af426edbc36dca3d7612383cea88bd608320`.
Current hold hash is `de2e464b…`. The graph remains failed and cleaned up;
this renewal grants no measurement or other runtime authority.

Awake helper13/PID44579 starts11:31:11 UTC and expires13:31:11. Its exact
UID501, birth and command match; the new private pointer precedes TERM of
owned helper12/PID33851, whose foreground returns143 and PID is absent.
The first current-control core check passes3,742 cases in207.05 seconds,
plus Ruff and mypy376 files. A later16-frame truncation-reporting correction
passes132 focused cases and awaits final qualification. No business code
or75-second bound changes in this diagnostic update.

### Hold35 renewal, 2026-11-17

Hold35 activates at12:04:05 UTC, before the previous review deadline at
12:25:20. Its next review is due13:03:54 UTC. All seven transports, both
exact helpers, twelve protected services, three readbacks and the pointer
pass independent review. The scratch packet is
`/tmp/swingset-stage2-hold-review-20261117-120354`; its
`independent-terminal-review.json` has SHA-256
`7b04dceb65d1a034154f30537702da0c425269365e4dfe4eebd85fec0a591a96`.
Current hold hash is `0d7bffd8…`. No diagnostic or counterfactual witness
has started; this renewal grants no runtime authority.

The corrected diagnostic passes132 focused tests and the final core check:
3,744 cases in204.50 seconds, Ruff and mypy376 files pass. Its report is
`/Users/skeswa/.local/share/swingset/core-runs/c2311effabf541f3b80fb85e/summary.json`.
Operator7eae/job14531 stay separate from immutable business producerBE.
The first extended invocation omitted four workers. It is interrupted by
owned pytest SIGINT after1,200 passes/1,596.97 seconds, exit2, with no test
failure; its three exact processes are absent. The unfinished run stays in
`/tmp/swingset-stage2-native41-inventory-control-full-20261117-v1`.
Its replacement uses the normal four-worker qualification settings and
unchanged cases. No graph timing or write-limit retry follows from this.

### Hold36 renewal, 2026-11-17

Hold36 activates at12:41:49 UTC, before the previous review deadline at
13:03:54. Its next review is due13:41:39 UTC. All seven transports, both
exact helpers, twelve protected services, three readbacks and the pointer
pass independent review. The scratch packet is
`/tmp/swingset-stage2-hold-review-20261117-124139`; its
`independent-terminal-review.json` has SHA-256
`59f745a89da676f9bf04be1458d862c821e82d34a8e696f565a1733e95e07785`.
Current hold hash is `bedd7588…`; no diagnostic/witness has started.

### Current diagnostic qualification, 2026-11-17

The four-worker extended check ends with 5,323 passes and one failure in
1,247.41 seconds. The nested two-worker pytest process in
`test_phase_receipt_survives_actual_two_worker_serialization_with_execution_owners`
finishes its four cases but exceeds the 30-second subprocess timeout.
The complete `test_core_client.py` rerun reproduces that failure, with 45
passes in 58.36 seconds. This is not an accepted full-suite pass or a
proven load-only flake. Qualification remains open; no inventory diagnostic
has started. Raw results stay in
`/tmp/swingset-stage2-native41-inventory-control-full-20261117-v2`
and `/tmp/swingset-stage2-native41-inventory-control-core-client-rerun-20261117.stdout`.
The earlier final 3,744-case core and 132-case diagnostic checks passed.

Awake helper 14/PID 62266 starts 13:09:10 UTC and expires 15:09:10. UID 501,
birth and command match. The private pointer precedes TERM of exact owned
helper 13/PID 44579; its foreground returns 143 and PID is absent. The
checkout session refreshes at 13:11:03 UTC under the same owner label.

### Native link-version witness, 2026-11-17

The SQLite route stops before any source hash or reader execution because
host process visibility is insufficient. A passwordless sudo probe fails;
no privileged container or guardian is used to bypass that boundary. The
selected native route instead uses the existing private Postgres source
through a repeatable-read, read-only snapshot.

The independent reader/SQL source review has SHA-256
`519fcaa8278cfc9a6ea35434855d6ec6a36d756357524a0f58cdbf9997988d90`.
Actual action review has SHA-256
`0003d3da18fb0c07edcac0e138672a9a7f97cdad081f2c29abff119d899c94f5`.
The exact once-only V2 pipeline starts 13:16:30 UTC and ends naturally at
13:18:42, taking 132.406 seconds. Source, reader transport and retained
reader container all exit zero; PID 0, no OOM or restart. Both stderr
streams are empty. Original private source C696 remains native38; rows
travel only through the server-local pipe, never a raw export.

The reader reproduces all 405 original labels and pointers across 820,512
rows. Changing only `identity_links.linker_version` from 9 to 10 predicts 401
actual found labels. Four remain different: July 2025 Midwest Westie Fest,
February 2026 Rose City Swing, May 2026 SoSwing and July 2026 Midwest Westie
Fest. The first, second and fourth also change dependencies and lose one
output row. SoSwing keeps its row count and dependency digest. No
unsupported version values or unreproduced original labels occur. These
facts explain 401 link mismatches, not all 653 graph differences; no bump,
waiver, graph acceptance or re-key follows.

The safe 212,293-byte report has SHA-256
`80734e716d11249327f3ea035d81140453e3afe6d3036c5799b11ee2aa999de0`
at `/tmp/swingset-stage2-native405-action-ea5942b1be90-20261117-v1/reader-stdout.scratch`.
Its exact action plan, cold profile, source facts, root GO and terminal
record remain in that scratch directory. Independent terminal boundary
review has SHA-256
`638832d63b644e2a62758a96f9ace63f970fc5c71572c1fea8308b54769b76ba`.
It confirms C696's identity, native38/source37/checksum, the original five
databases, zero source clients, protected services and guardian. The
stopped reader's process and cgroup are absent. Only Docker's unconfigured
OOM boolean changes from false to null; all other terminal profile fields
match. This grants no graph, re-key or diagnostic-start authority. Actual
R2 artifact/backup uploads remain pending.

### Hold37 renewal, 2026-11-17

Hold 37 activates 13:18:57 UTC, before the previous 13:41:39 deadline. Its
next review is due 14:18:47 UTC. All seven transports, both exact helpers,
twelve protected services, three readbacks and the pointer pass independent
review. Scratch packet `/tmp/swingset-stage2-hold-review-20261117-131847`
contains the verdict, SHA-256
`8b9010f13510b9551f361298a018b65446a056f2f4be843593cf338e7c1ad855`.
Current hold hash is `34f053ef…`; this interval remains continuous. No
measurement or other runtime authority follows from the renewal.

The unmodified 46-case `test_core_client.py` later passes in 2.52 seconds,
without a timeout or source change. Separate narrow reproductions pass;
neither timeout's cause is established. One final standard four-worker
extended invocation starts 13:27:17 UTC under owned driver PID 64471. Its
exact nested controller 65542 appears 13:33:59 and disappears by 13:34:00,
before the five-second sampling threshold. No sample or process signal is
needed. The observer binds UID, birth, ancestry, arguments and working
directory; its summary has SHA-256
`ea37d170b6fb45b9a54dd78fdba390a0129ef16ab209be8ab1fc1c4856fcefd0`.
That full invocation ends zero: all 5,324 cases pass in 1,245.94 seconds.
No source, case, worker, timeout or limit changes separate it from the
failed invocation. The final qualification sidecar is
`/tmp/swingset-stage2-native41-inventory-control-full-20261117-v3/qualification-v2.json`,
SHA-256 `75f309325166bee33106681e9465391432693e565b40ef60aecb6a0e72a59fcd`.
It binds raw full output and exact operator/job/test hashes. The core
machine receipt reports 3,744 selected cases, 204.392727 wall seconds,
all checks passed and the unchanged 300-second budget. Its console
pytest duration is 204.50 seconds. The qualification proves no timeout
cause and grants no execution authority.

### Current inventory cold preparation, 2026-11-17

All 123 reviewed stock rows execute once: 105 return zero, and 18 return
the expected named-absence status. Seven public files match their hashes
and inode identities after sealing. Seventeen finite helpers match their
complete profiles and own intent-time brackets, each ending naturally
zero with no live PID, OOM or restart. Three new owned volumes pass copy,
quiet comparison, 1,689-file/69,548,306-byte closure and numeric metadata
continuity checks. No credential bodies or hashes are read or exported.

The first permission helper reports `CAP_CHOWN` where the prospective
profile expected `CHOWN`. Preparation stops at row 26. A new explicit
four-profile correction preserves every argument and other field; no
generic capability alias normalization is added. Sole remaining-only
review `cbb37242…` permits only unattempted rows 27 through 122. The
original refusal and all prior outcomes remain; no helper is replayed.
Row 52's metadata name typo is also recorded against its unchanged actual
source-stat argument vector.

Actor `c201264e…` remains Created with PID zero and zero start/finish
timestamps. Its full cold profile, all eleven mounts and six RAM-only
comparison booleans pass. Independent postcold review has SHA-256
`d92490efff154781064ad9623edfa2972f8d0fa14fe09d7f4578789f74aa8bcd`.
Actual preparation result has SHA-256
`e883258e48a5f08d9b21b39da10f4b77a396e69824f80f6404e0eb16068fe597`
at `/tmp/swingset-stage2-native41-inventory-cold-55701a9afde6-DRAFT-v1/execution-x6hpacxv/preparation-result.json`.
Cold acceptance grants no start, database, freeze, cleanup or graph
authority. The actual execution plan `a4324bee…` and qualification/cold
addendum `21a04d1a…` await the separate action verdict and root launch.

### Hold38 renewal, 2026-11-17

Hold38 activates at 13:52:26 UTC, before the prior 14:18:47 deadline.
Next review is due 14:52:16 UTC. The seven transports, two exact helpers,
protected services, three receipts and current pointer pass independent
terminal review. Its SHA-256 is
`7ef73b33ca9524472eb0cd1703445a2e5a61a0494ef2b20c402876d18b45c0c8`
in `/tmp/swingset-stage2-hold-review-20261117-135216`.
Current hold hash is `bc68ca32…`; no new runtime authority follows.

The separate inventory action verdict has SHA-256
`fe896087abd213295c1c3c395d5ab0941e1bafa35430059c3db67962d3290fa9`.
It binds plan `a4324bee…`, actual cold actor `c201264e…`, qualification
`75f30932…`, postcold acceptance `d92490ef…` and this hold chain. Its
scope is one ordinary-acceptance/native75 rollback diagnostic and one
owned-copy cleanup, with no base cleanup or graph/re-key authority. The
exact two-file staging completes at 13:58:04 UTC, all thirteen rows once
with zero exits and matching private-file modes, sizes and hashes.
Fresh launch checks and exclusive root GO still precede any start.

### Current inventory diagnostic start, 2026-11-17

The actual launch pulse passes all 54 read-only checks. Its source query
closes at 14:03:23 UTC, with original catalog/identity and zero other
source clients. Pulse SHA-256 is
`511b724cb4f5765200bde66f33bc6c1ba14c07646409ee6adfb6db16b4c954f1`.
Root refreshes authorization status, then writes exclusive launch GO at
14:06:23.800 UTC; status age is 79.7 seconds, below 90. The exact maintained
foreground starts once. Actor `c201264e…` starts at 14:06:25.767997415 UTC,
PID 601990, with no OOM or restart. Local SSH PID 72307, UID 501, birth
09:06:23 Eastern, remains attached through foreground session 80771.
No custom launcher or second start is used.

Independent startup review has SHA-256
`846e7be8ff81931e9beff33a4448c951ccd4c6dc99023ceb4e63f55117cf8c37`
at `execution-v1/independent-startup/independent-startup-review.json`
under the cold packet. It binds maintained supervisor PID 601715,
birth 1032082331, actor birth 1032082430, authorization, complete IPv4/IPv6
firewall chains and explicit target-network-namespace sysctls. Full
profiles and code match; kernel limits are 8 GiB memory, zero extra swap,
six CPUs and 256 PIDs, UID 10001, no capabilities, no-new-privileges and
seccomp. Protected services and guardian remain unchanged. Initial
observer cgroup/sysctl path mistakes are retained and corrected without
traversal, actor changes or a source connection.

New owner `4a1cfd1042de44aaa8d40f6d` owns only its `_0` database, OID
9009543; registry, comment and actor birth match. The operator still reports
`freeze_one_new_owned_database` at 14:27 UTC. This proves allocation, not
copy readiness, native41, input acceptance or the trial. Progress counters
advance; supervisor error, failed probe, OOM, restart and stop reason are
absent. All metadata checks use database `postgres`, not a competing C696
connection. The native75 bound and 2700/90/3600-second limits remain.

### Hold39 renewal, 2026-11-17

Hold39 activates at 14:31:17 UTC, before the prior 14:52:16 deadline.
Its next review is due 15:30:43 UTC. All seven transports, both exact
helpers, protected services, three receipts and pointer pass independent
terminal review. Verdict SHA-256 is
`f6cdbe475fd7ee693220544c649804a163b1a134deb8561491de5ca668635d85`
in `/tmp/swingset-stage2-hold-review-20261117-143043`.
Current hash is `3f3b361e…`; owner, reason, original creation and module
remain unchanged. This is the same-identity attended renewal allowed by
the running action. It adds no runtime authority.

A single metadata-only observation at 14:34 UTC connects to database
`postgres`, closes normally, and reads no query text or payload. The source
reader is idle in its transaction; the owned database has an active backend.
Neither has a blocker. This supports activity, not a cause or optimization
claim. The diagnostic remains within its reviewed bounds.

### Current inventory diagnostic outcome, 2026-11-17

Actor c201 ends naturally at 14:52:33.847701819 UTC, exit zero, PID zero,
no OOM or restart. The maintained supervisor completes in 2775.5 seconds;
its original foreground also exits zero. This is diagnostic success only:
`project/inventory/all` fails with `WriteDeadlineExceeded` at 75.038 seconds.
The installed, applied and restored write policy remains 75 seconds.

The source checkpoint is `00c831a4…` before and after. Ordinary committed
acceptance produces actual checkpoint `5039effb…`, the same 52/98/113-entry
audit/version/input hashes and combined `2dd0e113…` context as the failed
normal graph. All 108,390 units and D58 match. Predictive binding remains
null with its explicit unsupported-transition reason. Observer quality,
rollback fingerprints, pointer, live label and run rows all pass. The
writer is open and outside a transaction both before and after the trial.
No SQLSTATE or connection loss occurs; the earlier normal loss remains
unexplained.

The primary `_Expired` occurs in completion's `_check_capture`, during a
second `desired` call through `_selected_members` and the single-generation
pointer lookup. Capture takes 33.159 seconds, including desired 32.429;
preparing inventory takes 23.818. Completion is interrupted at 16.971,
including partial desired 16.327. These spans overlap; do not sum nested
values or infer a precise SQL statement's cost. Whole diagnostic phases
include source identity 263.976, freeze 1485.023, destination prelude 309.204,
acceptance 68.038, post-acceptance binding 281.092, trial 75.408 and final
source identity 261.729 seconds. Freezing stayed within its 2700-second cap.

One owned 4a1c_0 database/OID 9009543 and its clients are gone. The registry
is empty, authorization and cgroup are absent, and actor/supervisor
incarnations are retired. The original five-database catalog, C696 native38
UUID/checksum/base bytes, 69, Shared, protected services,guardian and retained
actors pass independent review. No manual cleanup, adoption, source hash
repeat or graph replay is performed.

The 11,049-byte diagnostic receipt has SHA-256
`8acf4c10d3aeb822fb86ba7d2d25d9a36f636201d38401615496f46f8cf8894b`;
remote and local hashes match. Sole terminal verdict has SHA-256
`27e9d81285449351f79765e3ee5aa78c39be14ec3c4bc7c07f31056060b869ef`
in `/tmp/swingset-stage2-native41-inventory-terminal-20261117-v1`.
It accepts diagnostic/rollback/source/protection/owned-cleanup only,
rejecting unit, graph, Gate 2 and re-key acceptance.

The plan selects existing bounded `selected_generations` reads for whole
dependency cohorts. Canonical entry construction stays in the dependency
module; no cache or freshness check changes. The unused single-entry
adapter is removed. Tests compare full manifests and digests against
individual reads, including missing and duplicate members, Unicode,
group/effective/null/foreign overrides, mutation rejection and rollback.
The first test expectation forgot the existing singleton tail optimization;
its two assertion failures are preserved. Corrected query accounting and
all 67 cases in the two affected files pass in 64.87 seconds. No runtime
speedup or link-unit repair is claimed before qualification and measurement.

### Hold40 renewal, 2026-11-17

Hold40 activates at 15:16:40 UTC, before the prior 15:30:43 deadline.
Its next review is due 16:16:29 UTC. All seven transports, both exact
helpers, protected services, three receipts and pointer pass independent
terminal review. Verdict SHA-256 is
`6c5df555dcd20220d65fe8d1158bf748ea591faf882f71e9107f673de0234528`
in `/tmp/swingset-stage2-hold-review-20261117-151629`.
Current hold hash is `bea0ef47…`; no added runtime authority follows.

Awake helper 15/PID 79251 starts 14:50:26 UTC and expires 16:50:26. UID 501,
birth and command match. Its private pointer precedes TERM of exact old
helper 14/PID 62266; that foreground exits 143. Neither helper changes the
server or database.

### Hash-only dependency worker, 2026-11-17

The existing metadata_capture module now has a fixed, request-hash-bound
stdin branch for the 248 dependency differences. It preserves the existing
raw-capture branch. Root bodies stay in RAM; output contains unit IDs,
hashes, counts, classifications and exact residuals. Bounds include 128
roots including the computed inventory group, 64KiB frames, 32KiB chunks,
256KiB per root and 262144 report bytes. Original source/pointer/label/root
checks precede the single inventory substitution and canonical outer sort.

All 81 focused cases pass; Ruff and module mypy pass. Formatting touches
only the two assigned files. The subsequent core check passes 3778 cases,
all checks true, wall 213.698807 seconds below 300, with whole-source mypy
clean on 376 files. Core report is
`/Users/skeswa/.local/share/swingset/core-runs/ebfdc0d5d68a463ea6ee2829/summary.json`.
Formatted source is `a09e43cb…`, test `abf405cb…`; source-only review is
`495d6c3e3cf32c233769d8901fb8604fbd5d2b62da49b4840ce91cd2c1ff9944`.
The internal 600-second alarm does not bound final error printing after
restoration. Every future helper must therefore run the independent
620-second timeout plus 10-second kill grace INSIDE its container, with
bounded host transport and both process statuses/COMMIT/footer/EOF checks.
No operation authority follows from source review or tests.

Two read-only prerequisite transactions under accepted hold40 commit and
close normally; final postgres-only metadata confirms the original five
catalog entries and zero source/copy clients. All 191 expected roots and
248 pointer/label/root bindings match. Only metadata and byte lengths are
read, never root or recipe bodies. Maximum root text is 1383 bytes. The
selected inventory generation is `dg_d7fe015d…`, explicit group digest
`7cc2faec…`. Actual requests and fixed SQL are generated locally, not run.
Final packet index has SHA-256
`6764aaf35f40f4b2f3781e1a603952ac8c145a8aa862d9ff2ea8f7c8369b7aec`
in `/tmp/swingset-stage2-dependency248-packets-853a617ea5d9-20261117-v2/prerequisite-bound40-cyqq610c`.
Two packets cover 127+64 distinct expected roots and 127+121 disjoint units;
the inventory group makes 128/65 roots. Actual cold/action review and root
GO remain pending; no model or hash-worker execution occurs in preparation.

### Hold41 renewal, 2026-11-17

Hold41 activates at 15:54:36 UTC, before the prior 16:16:29 deadline.
Its next review is due 16:54:25 UTC. All seven transports, both exact
helpers, protected services, three receipts and pointer pass independent
terminal review. Verdict SHA-256 is
`8ac2a7449c5a325763bf8a3fe02796a4c10c16d6597b04e7e708588054bbeb8e`
in `/tmp/swingset-stage2-hold-review-20261117-155425`.
Current hold hash is `cd7ef5dd…`. The verdict permits this successor hold
for the already reviewed two-reader preparation when fresh guards and root
GO also pass. It grants no source SQL or worker-start authority.

The two-reader preparation verdict is
`9484297126a442361bd5e5d75c1b42e18f12fdaf76e975b75a422abddb173d44`
in the packet's `cold-path-correction-v3` directory. It covers the corrected
local SCP sources, six bounded sealers, two Created readers and private RAM
checks. Original row tables remain unconsumed. Actual preparation and the
separate pipeline review remain pending.

### Combined pointer and diagnostic qualification, 2026-11-17

The pointer batching candidate and the restricted Budafest diagnostic pass
all 3,801 core cases in 219.870 seconds, below the 300-second gate. Ruff
and whole-source mypy pass. The complete core job is
`/Users/skeswa/.local/share/swingset/core-runs/f50c9ea289144fb381984f26/summary.json`,
source `6c7828d1…`, binding `77df7d53…`.

The first combined run had five architecture assertion failures. Its strict
observer guard expected two explicit registrations; the new diagnostic
used generic hook tuples. Restoring those two registrations and updating
only the expected wrapper name preserves exact AST and reference-count
checks. All 210 focused cases pass, including bypass mutants. All thirteen
changed Python ASTs remain identical through central formatting. The final
full suite is running; no current runtime timing result is claimed.

The final combined full suite passes all 5,387 cases in 1,340.11 seconds
(22 minutes 20 seconds), exit zero. All thirteen source/test hashes remain
unchanged. Scratch qualification is
`/tmp/swingset-stage2-combined-qualification-20261117-v1.json`.
It grants source qualification only; no runtime speedup, graph acceptance
or launch authority follows.

Awake helper 16/PID 97814 starts 16:29:18 UTC and expires 18:29:18.
UID 501, birth and finite command match. Its private record and atomic
pointer replace helper 15 before exact TERM of PID 79251; the old
foreground exits 143 and its process is absent.

### Hold42 renewal, 2026-11-17

Hold42 activates at 16:39:47 UTC, before the prior 16:54:25 deadline.
Its next review is due 17:39:35 UTC. The first dependency reader and
source process have already exited zero and closed their backends.
No second-reader pipeline or Budafest source operation has started.
All seven transports, both exact helpers, protected services, three
receipts and pointer pass independent terminal review. Verdict SHA-256 is
`ced8c74d8a155eabc2cf298c77d83556ee311cb71095a8324f8cb51770b5f167`
in `/tmp/swingset-stage2-hold-review-20261117-163935`.
Current hold hash is `2bee1fd3…`.

Explicit successor addendum SHA-256
`b4e42d6470fce111dad94a5c1cb81e273042175e986375c1db407cf7681f6429`
admits this same-identity renewal for only the existing second dependency
pipeline and paused Budafest preparation. Each still needs its own fresh
guards and root GO. Budafest remains paused before its source metadata
helper until both dependency pipelines are accepted with source clients
retired. The first pipeline remains historical and must not replay.

### First dependency packet witness, 2026-11-17

The first bounded read-only packet reproduces all 127 original roots and
consumer bindings. Replacing only the inventory selection predicts all
127 found dependency hashes, with zero residuals. Source, reader and
outer pipeline exit zero after COMMIT, footer and EOF. The reader exits
naturally with PID zero, no OOM or restart; its cgroup and all source
application backends are gone. C696, catalog, native38 identity, protected
services, guardian and settings volume remain unchanged.

The 59,824-byte report has SHA-256
`f6d1d521c54938613898ef97a241c07549439c9e0af280759f6780f772b53e4e`.
Terminal result has SHA-256
`e4c7e00fab45cec21bd02d47d732d5ed9ea9ac873f14094a9eec71d17fbb1ae0`;
sole independent verdict has SHA-256
`d16ba78b6735b0e07d6e556a45f7280c6f444925970a0ca4b805ba932f341928`,
under `actual-pipeline-action-v2/fresh-admission41-yozyngtk/p01-actual-t5k2i25a`.
Raw records remain in the reviewed scratch packet.

Execution and source retirement complete under hold41. The continuous
hold42 renewal crosses only later administrative postchecks. The failed
old-hold equality comparison is retained; actual postchecks bind accepted
hold42 without claiming unchanged hold41 bytes. No command replays.
The verdict accepts these first 127 units only, granting no graph, re-key
or Gate 2 acceptance. The second packet's 121 units still need their own
fresh admission, root GO and terminal review. Budafest remains paused
before its source metadata helper until that boundary is closed.

### Dependency witness complete, 2026-11-17

Independent terminal review accepts the second packet and the exact union:
64 additional original roots and 121 consumer bindings reproduce; all 121
inventory substitutions match, with zero residuals. Together the two
packets cover 191 disjoint roots and exactly the graph's 248 dependency-only
consumer mismatches. Every original binding reproduces and every complete
found hash matches after the single inventory-selection substitution.
No other field or ordering is changed.

Source, reader and outer pipeline exit zero after COMMIT, footer and EOF.
The second reader exits naturally with PID zero, no OOM or restart; its
cgroup and all source application backends are absent. Catalog, C696
native38 identity, protected services, guardian, settings volume and
current hold42 pass strict postchecks. Every source connection is closed.

Second report SHA-256 is
`3d7ecacb06d5ff2230cbe95552f3227137925de40acc2fbdaee1736c9de550f3`;
terminal result SHA-256 is
`95ac511ea60b260f9cbf45b4607531ec164f470a2c7c63262ce732f0fe1c2cf9`.
The strict union has SHA-256
`2fef8228c75943f39e58cc640ac51ccc9eb37455e652c8a6e3a0bbfe7d1890fc`;
sole terminal and union verdict has SHA-256
`68a043322d5f1c71b0a139f62c09b63c28d97f06088c53bb7947c4a177fad0dc`,
under `actual-pipeline-action-v2/p02-fresh42-28hm8hwa/p02-actual-0onyq74l`.
Raw records remain in scratch.

This closes attribution for those 248 mismatches. It grants no graph,
Gate 2 or re-key acceptance. Four changed link labels, thirteen link
overruns, the inventory failure and 716 unattempted units remain blocked.
Budafest preparation may resume only after a fresh boundary and its own
root GO under the existing preparation review.

### Owner accepts four link differences, 2026-11-17

The owner accepts the four residual event-specific link outputs and asks
to move on, leaving data QA for later. No causal reconstruction is now
required. The generated [approval receipt](../../evidence/runtime/postgres-stage2-2026-11-17/owner-accepted-four-link-differences.json)
binds the exact four units, expected and found labels, row counts,
dependency hashes and source graph. It makes no causal claim or blanket
floating-point exception. Preserve the original mismatch records and
leave changed outputs queued for ordinary re-derivation. Maintenance
item 46 holds later QA; the plan records the new direction.

The owner also authorizes closing the tested Native41 revision. Change
`ouvlzsyr`, commit `91d3fed0`, closes with all 3,801 core and 5,387 full-suite
cases passing, formatting complete and 198 KiB of evidence under the
200 KiB change budget. A fresh revision opens for continued Stage 2 work.
No push occurs. Code hashes remain unchanged; the prepared Budafest actor
is still unstarted and Gate 2 remains open.

### Budafest cold preparation, 2026-11-17

All 123 reviewed preparation rows run once: 105 exit zero and eighteen
exact named-absence probes return their expected result. All seventeen
helpers exit naturally with exact full profiles and paired-intent lifetime
bounds. Seven public source files keep their verified hashes and inodes
through sealing. The three new owned volumes have the correct identity.
Copy and quiet diff prove all 1,689 input files and 69,548,306 bytes.
Source private-file metadata remains identical; target metadata matches
with distinct inodes. No secret body or digest is read or exported.

Actor `00980e0e40ec5708475682c7df387eeb0616db063bd6ff2440776b4b5a41e805`
is Created with PID zero and no start time. Its full configuration and
eleven mounts match the fixed Budafest target. All six private RAM checks
pass. Postchecks preserve the five-database catalog, C696 identity, zero
source clients, protected services, guardian, source settings and hold42.
No actor start, database clone or SQL mutation occurs.

Preparation result SHA-256 is
`4ef9ae3dabde1591ffb8a4d2d11dfdf70d5c04a71049a1518ae567363c59ecc6`;
sole terminal verdict SHA-256 is
`fb3cd375e9c4139da7a17d13cb430838a7311338a18c7cfe79a0aae39e28f5d4`
under `fresh-continuation42-gfv6vnzo/remaining-cold-actual-fbycd75m`.
Raw records remain in the Budafest scratch packet. The two local
validation refusals are retained and reconciled without replay: Docker's
exact absence newline, and the approved terminal-readback timing bracket.
The verdict grants preparation only; launch needs its own review and GO.

### Hold43 renewal, 2026-11-17

After all cold postchecks and their independent acceptance, hold43
activates before hold42's 17:39:35 UTC expiry. Its next review is due
18:30:29 UTC. All seven transports, both exact natural-zero helpers,
protected services, three receipt readbacks and pointer pass independent
review. Current hold hash is `1e80a856…`; verdict SHA-256 is
`bba6154b0007a602a5648b8ba98a499f06e19df021d80a4079492260df569b6e`
in `/tmp/swingset-stage2-hold-review-20261117-173029`. This is a fresh pin
for the separate Budafest action review and grants no launch authority.

### Current Budafest diagnostic starts, 2026-11-17

The separately reviewed maintained diagnostic starts at 17:54:30 UTC on
actor `00980e0e…e805`. Independent startup review accepts exact actor and
supervisor identities, current hold43, authorization, IPv4/IPv6 rules,
resource limits, frozen host/operator code and protected services. Source
connections by the reviewer use the administrative postgres database only.
The actual phase is freezing one new owned database, token
`9f6be48794454f58b8dfcc8d`, OID 9459504. No copy-ready, native41,
acceptance, trial or cleanup-complete result is claimed.

Root owns foreground session 91043, local SSH PID 25694 and remote
supervisor PID 2539837. The previous same-size probe took about 46 minutes;
this is an estimate, not a new deadline. Limits remain 2,700 seconds for
freeze, 75 for the write, 90 for reporting and 3,600 overall. The exact
[compact startup receipt](../../evidence/runtime/postgres-stage2-2026-11-17/budafest-diagnostic-startup.json)
binds plan, action review, launch GO and independent verdict. Raw
observations and the corrected local display checks stay in scratch.

### Hold44 renewal, 2026-11-17

The ordinary renewal's installer and mover transports each time out.
Neither command replays. Maintained status reconciles each exact owned
helper as a natural-zero terminal, with full profiles and public-file
hashes. Independent reviews authorize only the unattempted remainder,
first steps 3–6, then step 6. Original timeout and null transport exits
remain in the record; no all-seven-zero-transport claim is made.

Hold44 activates at 18:16:15 UTC, before hold43's 18:30:29 expiry. Its
next review is due 19:09:18 UTC. Both helpers, protected services, all
three receipt readbacks and the final pointer pass independent review.
Current hold hash is `a1ff8940…`; sole verdict SHA-256 is
`76a1d0a602b34061c589065e11f9878dccf38602cab5277055296221ab63ac46`.
The [compact receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-180918.json)
binds the actual continuation and terminal outcomes. Budafest stays
within its existing attended scope; no new runtime action is authorized.

Awake helper 17/PID 20932 has actual birth 12:43:57 ET, starts17:43:57 UTC
and expires19:43:57 UTC. Its mode0600 record and atomic pointer precede
TERM of exact helper16/PID97814; the old foreground exits143 and its
process is absent. A local one-second guessed-birth assertion refused
before any record or process change; actual identity is used throughout.

### Current Budafest diagnostic terminal, 2026-11-17

The actor ends naturally at 18:40:22 UTC, exit zero, with PID zero, no
OOM and no restart. The diagnostic completes, but its fixed link unit
exceeds the unchanged 75-second write bound at 75.032 seconds. Snapshot
loading takes 9.315 seconds and resolution 11.637; the interrupted commit
span takes 52.716, including 27.831 in completion. These inclusive spans
overlap. The expiry stack reaches retained-output `_store` through
`retain_output` and `_fingerprint`; it proves no exact SQL cause.

The observer completes with valid coverage. The writer remains open and
idle before and after rollback. Fingerprints, run rows and the original
label/pointer at generation 5370 agree. The source checkpoint stays
`00c831a4…` before and after; native38 identity and the five-database catalog
remain unchanged. Owned copy `9f6be48794454f58b8dfcc8d`, OID 9459504, its
clients, authorization and actor/supervisor incarnations are gone. The
empty registry directory remains. Protected services, guardian, native87
and frozen host/operator files are preserved. No manual cleanup occurs.

Sole terminal verdict SHA-256 is
`54243444e7cad79810813fb607781e78d3133e9278cd644d50ddb3f808796ae3`
in `/tmp/swingset-stage2-budafest-terminal-20261117-v1`. It accepts only
measurement, rollback, source preservation and owned cleanup. The unit,
graph, Gate 2 and re-key remain unaccepted. The
[compact terminal receipt](../../evidence/runtime/postgres-stage2-2026-11-17/budafest-diagnostic-terminal.json)
binds the command, original output, exact producer and verdict.

The plan selects bounded retained-output writes from this measurement.
The candidate changes only the interned `_store` path: at most 256 rows or
1 MiB of canonical UTF-8 payloads per normal `execute`, with an oversized
row alone. The input is consumed once. Tests prove exact canonical tuples,
ordering, shared-payload deduplication, row and byte bounds, duplicate-key
rejection, deferred labels, iterator errors and complete rollback.
Legacy storage, restore, residency, schema, versions and deadlines stay
unchanged. All 39 focused cases and 3,807 core cases pass; the core gate
takes 198.57 seconds under 300. Formatting leaves both code ASTs unchanged.
The final full suite is running. Candidate source review and fresh runtime
qualification precede a separately admitted server action; no savings or
new graph acceptance is claimed.

### Hold45 renewal, 2026-11-17

After the Budafest terminal review, ordinary hold45 renewal passes all
seven once-only transports without timeout. Both complete helper profiles
end naturally with exit zero. Activation at 18:54:06 UTC precedes hold44's
19:09:18 expiry. Hold `8a31a1a4…` is due at 19:53:55 UTC. Independent public
readbacks accept root0444/h1 installation, all twelve protected services,
three receipts and the atomic pointer. Sole verdict SHA-256 is
`7d69958c0a96f48849dd10d15ed3bc90c0565833cde79024c0a7908ef4741a83`.
The [compact receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-185355.json)
adds no runtime authority. Earlier expired gaps remain historical.

Retention source review accepts the two released files conditionally on
final qualification, with no material blocker. Verdict SHA-256 is
`66dd8028571bdab0b1440e052a424c24f8a0af3636a3c6f7edf23e9095cfc0dc`
in `/tmp/swingset-stage2-retention-batch-source-review-20261117-v1`.
The batch limit permits one pending encoded lookahead and one oversized
row alone; it is not a total-memory cap. No runtime savings or authority
follows from this source review.

### Hold46 renewal and retention qualification, 2026-11-17

Hold46 activates at 19:13:28 UTC, before the prior expiry, and is due at
20:13:17 UTC. Seven transports and both exact helpers finish zero; all
protected services, receipt metadata/hashes and pointer pass sole review.
Verdict `1755e8b6…` explicitly accepts this same-identity successor for the
separate conditional guardian action. The
[unaltered CLI result](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-191317.json)
retains the operation. Awake helper18/PID54481, UID501, actual birth
14:13:18 ET, expires21:13:18 UTC. Its private record and atomic pointer
precede TERM of exact helper17; the old foreground exits143 and is absent.

The retention candidate passes all 5,393 full-suite cases in1,358.33
seconds, with all957 inputs unchanged. Core3,807 takes198.57 seconds under
300; Ruff and mypy376 pass. The
[final QA receipt](../../evidence/runtime/postgres-stage2-2026-11-17/retention-batch-qualification.json)
binds source194f95d5, bindinga7ff, exact code hashes and the sole source
review. Runtime OCI1209bf1e/portableCONFIG61a23921 preserves exact dependency
B706 and its eight-layer prefix. All570 installed files/376Python files,
the duplicate app source, all15 OCI blob hashes and archive248,948,224
bytes/SHA1d7bb27d pass independent byte review24f71b8c. The proof container
remains Created and never starts. Its exact AMD64-on-ARM warning is
reconciled without replaying creation. Build concurrency with the final
native full suite is recorded; the existing builder stays at2CPU/2GiB.

The server cache transfer is underway. Guardian action reviewc44edaaa is
conditional on final QA, actual cache proof, fresh admission and RootGO.
Only its remote argv may execute into fresh exclusive captures; legacy
local redirection strings and stale A30/e68 prose are not executable
predicates. Correct actual identities are current0d966/raw95f/image2eb and
newrawfb308/canonical2a019/image61a. No guardian or worker change has run,
and no timing savings or graph acceptance is claimed.

### Retention runtime image authorization, 2026-11-17

The exact49 reviewed commands run once:46 exit zero and three return their
exact named-absence result. Both metadata sealers finish naturally after
complete-profile and own-intent lifetime checks. Only old guardian0d966
stops, deliberately exit143, and remains retained. The next policy moves
atomically with inode18769908 unchanged, root0444/h1/884bytes. Only its
image changes from2eb to61a; old raw95f bytes remain sealed separately.

New guardianc153a4bc, PID3405064/birth1034094195, starts19:41:43 UTC using
the same operator824/securityAbb. Full configuration, kernel UID0,
capabilities281000, no-new-privileges, seccomp2, namespace and cgroup pass.
The first new-policy status sample succeeds in2.548 seconds. Inventory
remains false/count41, a limited observation. The conservative postcheck
clock starts before the start intent and finishes in132.779 seconds under
180; the actual final validation, not later summary assembly, establishes
completion. The entire operation takes844.886 seconds under1,800.

Local validation corrects two declared-shape assumptions from original
captures: row24 is a six-field terminal projection; unconfigured
OomKillDisable permits false/null. The one-use final validator also reads
the actual numeric UTC epoch. It runs once, exit zero. No remote command
replays and no deadline or runtime code changes. Original captures and the
historical validator hash remain unchanged, with the actual executed hash
recorded separately.

Independent terminal reviewd49a1f82 checks all49 intents and fresh source,
catalog, protected14, hold46, policy and capacity. It accepts this image-only
transition. The [compact receipt](../../evidence/runtime/postgres-stage2-2026-11-17/retention-guardian-transition.json)
binds actual output, final QA/cache proof and the verdict. No worker or graph
starts, and no performance, Gate2 or re-key acceptance follows. Hold47
renewal and conditional preparation review for the fresh normal graph are
underway.

### Hold47 renewal, 2026-11-17

After accepted guardian transitiond49a, hold47 activates19:50:44 UTC,
before the previous20:13:17 expiry, and is due20:50:32 UTC. Seven transports,
both exact natural-zero helpers, protected12, all three receipt readbacks
and pointer pass sole terminal reviewf7448f05. The
[unaltered CLI result](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-195032.json)
retains the operation. That review explicitly accepts47 as successor for
conditional cold preparation71f65f14, plan759502f1, preserving the fixed
normal75/86400/two-worker limits. Fresh admission and exclusive RootGO
remain required. The scope ends at Created/PID0 and six RAM checks; it
supplies no verifier start, clone, SQL or cleanup authority.

### Normal68dd cold preparation and hold48, 2026-11-17

All108 preparation commands run once:91 exit zero and17 return exact
named-absence results. All16 helpers finish naturally after complete
profiles and exact nanosecond intent brackets pass. Four new volumes have
correct identities and empty roots. Host4 files retain their hashes and
inodes through root0444 seals; package directories become root0555.
Copy and quiet diff pass. All1,689 input files total69,548,306 bytes.
Source4 first-six numeric fields match the accepted Buda baseline;
target4 match them. Historical device/inode fields are not claimed fresh.
No private values or digests are exported.

Actor270af083 remains Created/PID0/zero start and finish times; its full
configuration and eleven mounts match. Both bounded server-RAM pipelines
exit zero with all six expected booleans true. Independent terminal
review8a9d46fa accepts preparation and fresh source/catalog/protected/guard
preservation, granting no start, SQL, clone or graph authority
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/normal68dd-cold-preparation.json)).

Hold48 activates20:46:02 UTC before47 expires, due21:45:50 UTC. Seven
transports, both full-profile helpers, protected12 and all receipt
readbacks pass sole terminal review16e18a7e. It is the explicit successor
for the separately reviewed exact16 helper retirement
([CLI result](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-204550.json)).

Awake19/PID69892, UID501, actual birth16:00:40 ET, expires23:00:40 UTC.
The private record and atomic pointer precede TERM of exact helper18;
its foreground exits143 and the old process is absent. Session lock
`codex-stage2-approved-20261116` renews at20:07:30 UTC.

### Completed metadata helpers retired, 2026-11-17

The aggregate status inventory reaches260,913 bytes under its fixed
262,144-byte bound. Its exporter lists all retained Docker objects. The
measured plan selects only the sixteen successful helpers from cold68dd,
after independent terminal acceptance; no observation or runtime limit
increases. Action reviewfec74dd6 binds their exact IDs and full profiles.

One `docker container rm` exits zero and returns exactly those sixteen IDs,
with no force or volume deletion. Fresh checks preserve the Created/PID0
verifier, all four target volumes, original source volume, host files and
copied inputs, source38/catalog5/zero source clients, protected services,
guardian and hold48. Raw outputs and complete-profile receipts remain.
All older protected jobs, images and databases remain untouched.

The inventory drops from261,623 to255,791 bytes; fresh status retains
resource0/errornull/currentpolicy and limited inventoryfalse. Independent
terminal review5e718ac6 accepts the cleanup, with no launch authority
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/retire-normal68dd-metadata-helpers.json)).
The local public-stat column check and source-holder mount ordering are
reconciled from original captures under the existing typed/keyed rules;
no remote action replays. The actual operation ends in256.34 seconds under
its600-second bound.

### Retained-output candidate normal graph start, 2026-11-17

The ordinary graph starts once at 21:25:35.057273687 UTC on actor270a,
qualified source194f95, CONFIG61a and guardianc153. Plan7134b1bc and
reviewd97bb0c3 bind all108,390 units, native75, two workers and the exact
owned-copy boundary. Root's fresh launchGO bc8733cc binds unchanged source,
status age34.04 seconds and hold48 with1,217.28 seconds remaining.

Local SSH76038/UID501 starts at16:25:33 ET and stays attached through
foreground42233. Remote supervisor131923/UID1000 starts21:25:33 UTC,
birth ticks1034717271. ActorPID132442 starts21:25:35 UTC,
birth ticks1034717360, network inode4026533552 on the accepted server boot.
At21:30:08 UTC the maintained live record reports protected12 unchanged,
no OOM, progress and566,927,134,720 free bytes. Startup inspection and
terminal coverage/source-preservation/cleanup acceptance remain pending.
The four exact link-output exceptions remain owner-accepted for later QA;
no further causal investigation of them is required.

### Attended normal-graph hold renewal 49, 2026-11-17

Hold49 activates at21:35:23.375669036 UTC before48 expires, and is due
22:30:17 UTC. Install step2 and activation step5 each time out at transport;
read-only reconciliation proves both exact helpers finish naturally at0.
The intermediate unknown5 remains recorded. Sole remaining-index reviews
cc1e9270 and20d95639 permit only unattempted3–6 and then6; no command
replays. Final step6 exits0 and the current pointer becomes fff62543.

Independent terminal review66a5d48b verifies eight fresh public reads,
seven exact once-only intents, both complete helper profiles, protected12,
three stable remote receipt readbacks and pointer. It accepts49 as the
same-identity successor for the attended normal270a graph under reviewd97,
without expanding scope ([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-213017.json)).

Sole independent startup review bf64f399 accepts the actual normal actor,
exact supervisor, complete frozen configuration, eleven mounts, namespace
authorization, kernel limits, both firewalls, guardian, host files and all
fourteen protected services. It observes one owned database and source
freeze activity, granting no clone-readiness or graph-result acceptance
([startup receipt](../../evidence/runtime/postgres-stage2-2026-11-17/normal68dd-startup.json)).

Hold 50 activates at 21:55:09.527608074 UTC before 49 expires, due
22:54:46 UTC. All seven transports and both complete-profile helpers exit
zero; eight independent public reads, protected services, receipt readbacks
and pointer pass terminal review21f8d51e. It explicitly succeeds 49 for the
attended graph and conditional recovery-C operation. Recovery still requires
fresh graph readiness, capacity and its separate execution gate
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-215446.json)).

At 22:05:23 UTC the fresh initial receipt exists: 4,357,075 bytes,
SHA a62f45bf, all 108,390 unique expected units and digest d58f3f45.
Its actual business clock is 21:26:21.982421 UTC. Source checkpoint00c
and accepted checkpoint5039 are freshly observed, with the new actual
52-audit/98-version/113-input context14d034de; no old context is reused.
The unsupported projection remains null. Results and errors are empty.
At 22:05:44 UTC the first owned FILE_COPY creation is active; both-clone
readiness and recovery admission remain unproved. These progress captures
stay in scratch, with no graph-result acceptance.

Hold 51 activates at 22:15:53.235205468 UTC before 50 expires, due
23:14:57 UTC. Its activation transport times out; exact helper reconciliation
proves natural zero and permits only unattempted final step 6, which exits
zero. Independent terminal review7ae8dc57 accepts the seven once-only
commands, eight public checks, complete helpers, protected services,
readbacks and pointer, explicitly succeeding 50 for the graph and conditional
C and six-helper cleanup gates ([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-221457.json)).

Hold 52 activates at 22:36:32.578768061 UTC before 51 expires, due
23:36:20 UTC. Seven transports, both exact natural-zero helpers and eight
public terminal checks pass review3c29827a, explicitly continuing the graph
and conditional recovery and metadata-cleanup work
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-223620.json)).

A local Codex daemon restart leaves SSH76038 alive with its original UID
and birth, reparented to PID1. No remote service restart or graph relaunch
occurs. Both six-target cleanup preparations have no removal intent or
remote effect; raw reads and late local validation remain recorded.
The newly reviewed eight-target proposal adds only the two accepted hold51
helpers, preserving all other targets and limits. The graph has all three
owned databases and both worker connections by 22:26 UTC; live progress at
22:37 remains healthy, with protected services unchanged.

Awake helper20/PID11652 starts at 22:38:33 UTC, UID501, birth17:38:33 ET,
finite through November18 00:38:33 UTC. Its private record and atomic current
pointer precede TERM of exact helper19/PID69892; the old process is absent.

### Eight completed hold helpers retired, 2026-11-17

One exact-ID removal returns eight expected IDs, exit zero and empty stderr.
All 30 preflight commands and 30 post-checks pass; source, normal verifier,
guardian, protected services, volumes and databases remain preserved.
The operation finishes in 258.52 seconds under 600, with 471.38 seconds
remaining before the single effect, above its 360-second reserve.

Fresh status drops from 259,413 to 256,573 bytes. Headroom is 5,571 bytes:
it covers the measured-schema 5,041-byte C reserve, while the old 5,815-byte
projection still fails. No observation cap increases. Sole terminal review
13a54033 accepts the cleanup, granting no C creation or graph acceptance
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/retire-eight-hold-metadata-helpers.json)).

### C offline sync and staged-PG partial, 2026-11-17

The read-only filesystem helper passes complete cold/RAM/terminal checks:
UID/GID999 own PGDATA mode700; the expected system identifier and clean
shutdown match; there is no postmaster PID, WAL symlink or external tablespace.
Its unconfigured OOM field switches false/null presentation; finite same-CID
review889e5e83 accepts that one representation, with all other fields exact.

The same reviewer accepts a stricter 120-second sync timeout under the
original clocks (78f2e67c). One sync finishes naturally in0.385168 seconds;
full terminal checks pass. The new durable PG is created once asbf0f1334,
PID0/zero times. Its start is refused when only21.73 main seconds remain,
less than the40-second start-and-tail bound. No server, public sealer,
marker helper or SQL update starts. No clock resets after effects.

All20 independent post-checks finish35.79 seconds before the original
1,800-second deadline. Source, normal verifier, protected services, old C
and current hold52 remain preserved. Review8a95555d accepts this safe partial
boundary only. Metadata and sync helpers are stopped; the new PG remains
Created. Starting it and rebinding the still-old prepared marker need a new
concrete remaining-only action, not a repeat of Create or Sync
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/recovery-C-sync-staged-PG-partial.json)).

Hold53 activates at23:34:01.832995531 UTC before52 expires, due
November18 00:33:46 UTC. Seven transports, both exact helpers and eight
terminal public checks pass review109e14ad. It continues the attended graph
and only newly reviewed remaining C work and exact old52-helper cleanup;
the previous C operation stays closed ([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261117-233346.json)).

### Exact old hold52 helpers retired, 2026-11-17

One exact two-ID removal exits zero with the expected IDs and empty stderr.
All25 preflight and25 post commands exit zero. Direct absence and all
preservation checks pass, including stopped metadata/Sync and never-started
PGbf0f. The first aggregate observation still has3,719 bytes free and fails
the3,900-byte future C reserve. It remains a failed observation.

A bounded status-only reconciliation proves both removed IDs absent and
current53 helpers present:257,716 bytes,4,428 free, resource0/errornull and
unchanged policy. It finishes within the same600-second window, with no
second removal or clock reset. Sole terminal review9b78b9d0 accepts the
cleanup and budget only ([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/retire-two-hold52-metadata-helpers.json)).

### C durable runtime safe partial, 2026-11-18

A separate reviewed remaining-only operation starts existing PGbf0f once.
The full running profile, private environment booleans, system identifier,
exact catalog and roles, empty environment and old prepared marker pass.
Durability settings are fsync, full-page writes and synchronous commit on.
Initial parallel quiet-client checks see the other probes and fail; their
raw results remain. Sequential checks pass without changing the predicates.

Public directory creation and two uploads complete once, exit zero. No
public sealer or marker helper is created or started. With insufficient
time for the remaining bounded sequence, Root stops only PGbf0f using the
reviewed stock command. It exits zero at00:11:09.776958 UTC; full terminal
checks prove the same start identity, PID0, no OOM and exact configuration.
The marker remains unchanged, and the uploaded public files remain unsealed.

All38 read-only partial post-checks pass within the original1,200-second
window, including the four exact future-name absence checks. The old C,
source, current normal graph, guardian, protected services and hold53 are
preserved. Independent terminal review 18be2ce0 accepts only the safe partial result.
No recovery, marker update, restore or whole-C acceptance is claimed
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/recovery-C-durable-runtime-safe-partial.json)).

The normal graph at00:10:26 UTC remains running with no 60-minute progress
alarm or OOM:265,717 transactions and2,685,862,585 tuples observed. These
are database activity counters, not completed verification-unit counts.

Hold 54 activates at 00:17:31.860705377 UTC before 53 expires, with review
due November 18 01:17:20 UTC. Sole terminal review 813d8746 accepts the
seven transports, exact helpers, receipts, protected services and pointer.
Both prior C operations remain closed; new remaining work needs fresh
headroom and its own action review
([receipt](../../evidence/runtime/postgres-stage2-2026-11-17/hold-review-20261118-001720.json)).

Awake helper 21, PID 41341, starts November 18 00:17:48 UTC with UID 501
and a two-hour limit. Its verified record and atomic current pointer
precede TERM of exact helper 20, PID 11652; the old process is absent.
The new helper expires at 02:17:48 UTC.

At 00:19:11 UTC the same normal verifier is still progressing, with no
stall alarm or OOM and protected services unchanged. Its final unit counts
and timing acceptance remain pending.

### Normal verifier stopped after hold expiry, 2026-11-18

The 14:57 UTC status check finds the normal verifier stopped, PID 0, exit
137 without OOM. Its supervisor records incomplete verification, a generic
ValueError, confirmed stop and no automatic retry. It finishes at 01:19:35
UTC after 14,040.795 seconds, about 3 hours 54 minutes.

The installed hold still expires at 01:17:20 UTC. The last progress sample
is 01:17:33; the actor finishes at 01:19:34. The frozen supervisor requires
a current hold during each peer check. Expiry is therefore the likely stop
trigger; its generic error does not identify the exact failed predicate.
The terminal sample reports protected services unchanged and no OOM or
60-minute progress alarm. Those observations do not prove current source
preservation or owned-copy cleanup. No completed unit-set or timing
acceptance is available.

No restart, renewal, cleanup or provider operation occurs during this check.
A new attended interval and terminal reconciliation must precede another
verification run. The passing code tests and accepted recovery safe partial
remain separate from this incomplete runtime result
([observation](../../evidence/runtime/postgres-stage2-2026-11-18/normal68dd-stopped-status.json)).

### Stopped verifier and ownership reconciliation, 2026-11-18

The runtime receipt matches the initial 4,357,075 bytes and hash
`a62f45bf…`. It lists 108,390 distinct expected units and zero results.
There is no unit timing to accept. Independent review confirms the actor,
supervisor, cgroup and namespace authorization have retired. All fourteen
protected service profiles match.

The recorded token and process incarnation match the three remaining
databases, OIDs 9909447, 10359411 and 10359412. Root observed the five
original catalog rows and the 38,409,934,515-byte source size through
`postgres`. The reviewer opened one read-only metadata query directly on
C696 at 15:12:55 UTC, contrary to the administrative observation rule.
The connection closed. Root excluded that query from preservation evidence
and a fresh check through `postgres` found zero scratch clients. There was
no full source-content posthash. Independent review accepts the stopped
boundary and limited preservation. Exact owned cleanup remains a separate
operation ([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/normal68dd-terminal-reconciliation.json)).

A new hold activated at 15:34:27 UTC and was due at 16:04:59 UTC.
The prior interval expired 51,426.925 seconds before activation. The seven
administrative commands completed and eight independent public checks passed.
Both helpers exited zero. The sole reviewer accepted the reopened hold in
verdict `4367b9aa04b1`
([hold receipt](../../evidence/runtime/postgres-stage2-2026-11-18/hold-review-20261118-150459.json)).

The 15:44 UTC renewal kept the hold valid through 16:44:05 UTC.
Its terminal review verified both helper exits, the installed hold,
receipt readbacks and protected services
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/hold-review-20261118-154405.json)).

### Three verifier databases retired, 2026-11-18

Root dropped the three owned branches in order 2, 1, 0. Each operation
connected to `postgres`, checked the cluster, catalog, OID, size, owner
and zero target clients, then dropped the literal database without force.
Each exit was zero and the intervening catalog checks passed. Independent
review accepted all three removals. The five original database rows,
protected services, stopped verifier and its ownership record remain.
The filesystem had 455,052,029,952 bytes free before and 576,716,374,016
afterward. The operation took 21.18 seconds
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/normal68dd-owned-database-retirement.json)).

The remaining status-file limit also blocked fresh preparation. Root
removed the sixteen exited metadata helpers from eight reviewed holds.
All sixteen returned IDs and direct absence checks matched. The other
704 container IDs, images, volumes and retained hold receipts matched.
At 15:59 UTC the refreshed status was 253,798 bytes, leaving 8,346
against the estimated 8,089 needed. Independent review accepted the
removal and measured capacity
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/fresh-graph-retired-hold-helpers.json)).

### Fresh verifier prepared, 2026-11-18

At 16:19 UTC actor `00b44f7e…` was created with four fresh volumes. All
108 preparation commands met their expected exits. Sixteen helpers exited
zero. The copy contained 1,689 files and 69,548,306 bytes. All six
configuration predicates passed. Independent review accepted preparation
after 20 further read-only checks. The actor has never started. At 16:21
UTC the status file measured 259,632 bytes, leaving 2,512 bytes under
its 262,144-byte limit
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/normal668f-cold-preparation.json)).

The 16:25 UTC hold renewal activated before the prior interval expired.
Review is due at 17:24:56 UTC
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/hold-review-20261118-162456.json)).
The first read-only capacity check expected a created verifier in the
aggregate. The guardian omits optional stopped actors with exit zero.
Root checked its full Docker profile directly and allowed 410 status bytes
for its future running entry. After exact removal of its sixteen completed
helpers, the status file fell from 260,341 to 254,508 bytes. All direct
absence checks passed. The original five catalog rows had zero scratch
clients. Independent review accepted the removal and preservation checks
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/normal668f-preparation-helper-retirement.json)).

### New normal graph started, 2026-11-18

Verifier `00b44f7e…` started once at 16:45:47 UTC, PID 2356580, under
plan `bfa4ccc2…` and review `2d2b1181…`. The supervisor is PID 2356061.
The independent startup check confirmed namespace authorization, IPv4 and
IPv6 rules, resource limits, 14 protected services, one job-owned database
at OID 10359465 and its ownership record. It observed source-copy work
through administrative `postgres`. The two worker copies and the final
108,390-unit receipt were still pending at that check
([startup](../../evidence/runtime/postgres-stage2-2026-11-18/normal668f-startup.json)).

The startup receipt calls the source database hash `ae3bbcd1…` its
`qualified_source`. The graph plan records the application source separately
as `194f95d5…`, the image manifest as `1209bf1e…` and the Docker image
configuration as `61a23921…`
([application qualification](../../evidence/runtime/postgres-stage2-2026-11-17/retention-batch-qualification.json)).

The 17:03 UTC hold renewal timed out during the installer transport.
The same installer exited zero. Independent review accepted continuation
from step 3. Activation finished at 17:10:18 UTC, before the prior
interval expired. The reviewer confirmed the graph and supervisor identities,
protected services and continued monitoring through the new 18:03:47 UTC
review time ([renewal](../../evidence/runtime/postgres-stage2-2026-11-18/hold-review-20261118-170347.json)).

The 17:24 UTC manifest contains 108,390 distinct expected units, digest
`d58f3f45…`, and zero results. The source checkpoint is `00c831a4…` and
the checkpoint after input acceptance is `5039effb…`. By 17:45 UTC both
worker branches were active. The catalog and the ownership record agree on
OIDs 10359465, 10809425 and 10809426. The read-only administrative snapshot
found zero clients on the two protected sources and zero clone or copy
statements. The full graph and native75 timing remain open
([manifest and copies](../../evidence/runtime/postgres-stage2-2026-11-18/normal668f-initial-manifest-and-copies.json)).

### Recovery C preflight stopped before any change, 2026-11-18

The first local launcher passed a string to a path reader and exited
before opening an operation. After that fix and a fresh review, the
17:50 UTC preflight received 35 successful SSH responses and two connection
resets. The last three status reads were never dispatched in that preflight.
Two successful file-metadata reads included the path requested by `stat`;
the validator expected only the numeric fields. Four follow-up SSH reads
also failed. A separate diagnostic checked the final three status commands.

Independent review confirmed that Recovery C's Postgres remained stopped
with its prior start and finish times. The runner issued no file seal,
Postgres start or marker change. Keep the original outcomes. Use four
concurrent readers and validate each returned path along with its metadata
on the next attempt
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/recovery-C-marker-preflight-no-effect.json)).

### Recovery C public file ownership, 2026-11-18 18:07 UTC

All 40 preflight reads passed using four connections. One helper changed
ownership of the public directory and its two files and exited with code
zero. Its Docker command omitted the stop timeout. Docker returned no
such field, while the check expected ten seconds. The runner stopped
there. The two archived files had the expected sizes, hashes and ownership.
Direct child inode and link-count checks must wait until directory access
is restored. Independent review accepted the ownership change. The file
mode changes, Postgres restart and prepared-marker update remain pending
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/recovery-C-public-ownership-partial.json)).

### Status capacity during normal graph verification, 2026-11-18 18:21 UTC

The 262,144-byte status limit left 4,371 bytes available. Recovery C and
one hold renewal needed 4,865. A single reviewed removal retired the
sixteen exited helpers from eight completed November 16 holds. All sixteen
IDs were absent afterwards. The other containers, volumes, images and
historical receipts remained intact. The status file fell to 252,100 bytes.
The graph kept its original start and three owned databases. The operation
and its checks took 103.67 seconds
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/normal668f-retired-nov16-hold-helpers.json)).

### Recovery C files sealed and database restarted, 2026-11-18 18:35 UTC

All 42 preflight reads passed. Both permission changes completed. Direct
checks confirmed the original file and directory inodes, the two file
hashes and final modes 0444 and 0555. Postgres started once at 18:34:58
UTC. Its running Docker record retained the previous 00:11:09 shutdown
timestamp. The running-state check expected an empty timestamp and the
runner exited before sending SQL or changing the marker. Independent
review confirmed the same active process, PID 3502211, and the successful
configuration checks. The runner issued no stop
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/recovery-C-files-sealed-pg-running.json)).

### Recovery C clean shutdown, 2026-11-18 18:52 UTC

The proposed marker operation on the running database had no final action
approval. Root selected the independently reviewed shutdown. The two choices
shared one exclusive claim. Fresh checks verified the exact process, its
configuration and the public files before one stop. The database exited
with code zero at 18:52:26 UTC. All 36 read-only post-checks returned
successfully. Independent review accepted the shutdown and confirmed the
protected services and graph continued. The marker and schema remain unchanged
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/recovery-C-sealed-files-cleanstop.json)).

### Recovery C preflight observed PostgreSQL autovacuum, 2026-11-18 19:16 UTC

The catalog showed both real verification clients and an autovacuum worker
on one owned scratch database. The preflight required every session to be a
client connection from the verifier. It refused the autovacuum row before
claiming or starting the recovery database. An independent check reproduced
the exact failure from the saved catalog and confirmed the database remained
stopped with its public files sealed. The verifier continued. Keep both
client checks and check PostgreSQL maintenance backends separately
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/recovery-C-native-autovacuum-preflight.json)).

### Recovery C marker helper and clean shutdown, 2026-11-18 19:27 UTC

Four sequential SQL checks passed before the helper ran. The helper reported
that only the prepared database comment changed and that it loaded no rows.
It exited with code zero. A configuration check still expected the helper’s
own hostname after it joined the Postgres network namespace. A separate
read found the exact Postgres hostname and confirmed the expected user,
entrypoint, command and stop timeout. The original check stopped the run
before the SQL readback of the marker and database settings. Postgres shut
down with code zero at 19:27:49 UTC. All four public helpers were subsequently
observed offline with their original start and finish times. Independent review
accepted the helper’s reported change and the clean shutdown. The SQL readback
of the stored marker and the restore drill remain open
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/recovery-C-marker-helper-cleanstop.json)).

### Attended takeover and hold 64, 2026-11-18

The previous operator explicitly releases the checkout and hands over Stage 2
at 19:35 UTC. Session `codex-stage2-takeover-20261118` acquires the lock,
starts a finite attended watch and verifies the same graph remains healthy.
No commit, push or cutover is authorized.

A read-only marker proposal reaches final refresh at 19:48 UTC but refuses
an inherited 1,920-second initial lease floor. No Root GO, claim or C mutation
exists; the local failure remains in scratch. A narrow successor pins its
exact admission source through reviewed settings and GO, retaining every
state, transport and timing predicate. A fresh hold is required before it.

Hold 64 activates at 19:49:28.336909901 UTC before hold 63 expires, with
review due 20:49:17.243130 UTC. Sole review `5479bfc8` accepts all 39 checks
and continued monitoring of the same actor and supervisor only. The first
empty-stdin process-observation attempt remains recorded; a separate bounded
public script proves the exact process identities. No C authority comes
from renewal ([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/hold-review-20261118-194917.json)).

### C marker SQL verification complete, 2026-11-18

The separately reviewed read-only action starts only existing PGbf0f,
verifies its genuinely new 19:56:47.924505066 UTC birth and retained exact
19:27:49.647353717 prior finish, then runs four sequential read-only queries.
Cluster identity, roles and grants, UTC, durable settings and the prepared
marker match. The owned C database has zero environment rows and no other
clients. No COMMENT, helper, permission or setup command repeats.

The same PG stops naturally at 19:56:53.705845952 UTC, exit zero, PID zero
and no OOM. RAM3 passes. All 37 post observations pass, with the descriptor
and resource counts unchanged. Status uses 256,045 bytes, leaving 6,099
inside the unchanged cap and covering the measured 965-byte next renewal.
The complete action takes 46.604 seconds inside its original 1,200-second
window. Sole terminal review `733ab55f` independently recomputes the queries,
all saved post predicates and preserved boundaries.

This closes prepared-marker compatibility and verified offline state only.
Native41 indexes, restore, recovery and full graph acceptance remain open.
The earlier helper RAM failure stays recorded with its separately accepted
exact phase-specific hostname explanation
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/recovery-C-readonly-marker-verification.json)).

Hold 65 activates at 20:15:11.878091914 UTC before hold 64 expires, with
review due 21:15:01.011170 UTC. Sole terminal review `87d96bf9` accepts
all 39 checks and continued monitoring of the same graph only. The C SQL
verification and its independent terminal acceptance precede renewal; no
C operation overlaps it ([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/hold-review-20261118-201501.json)).

Finite awake helper 26, PID 80228 and UID 501, starts at 20:12:18 UTC
with its exact command and birth verified. Its private record and atomic,
directory-fsynced pointer precede further work. It expires at 22:12:18 UTC;
helper 25 is left to expire naturally. The new hold review independently
checks the selected awake identity.

Hold 66 activates at 20:45:23.013575339 UTC before hold 65 expires, with
review due 21:45:12.342918 UTC. Sole terminal review `4573c5eb` accepts
all 39 checks and continued monitoring of the same graph only. Awake26
remains verified through 22:12:18 UTC; no C or shared audit overlaps
renewal ([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/hold-review-20261118-204512.json)).

A separate 20:48 UTC read-only status measurement has 4,677 bytes free.
The prepared Shared141 cold geometry needs 5,191 using the prior measured
next pair, so exact historical-helper inventory preparation is selected;
no removal has occurred. Future cold admission still needs a fresh estimate
and actual completed graph and owned cleanup
([measurement](../../evidence/runtime/postgres-stage2-2026-11-18/shared141-status-capacity-trigger.json)).

Hold 67 activates at 21:14:12.382080933 UTC before hold 66 expires, with
review due 22:14:01.979412 UTC. Sole terminal review `990b056c` accepts
all 39 checks and continued monitoring of the same graph only. Awake26
expires at 22:12:18 UTC, so its next finite replacement remains scheduled
before that point ([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/hold-review-20261118-211401.json)).

A new finite read-only watch at 21:14 UTC surfaces the supervisor's probe
failure, stop reason, failed and paused fields. Their absence is preserved
as unknown rather than explicit false or per-unit success. Its first
sample is healthy. Only after that sample, exact UID/birth/argv checks
precede TERM of the two superseded local watch processes. The graph actor
and supervisor are unchanged. Original pinned watcher files are untouched.

### Space for the shared audit, 2026-11-18 21:37 UTC

One command removed the 16 exited Docker helpers from hold reviews 55–62.
The collector now has 9,639 bytes free against the shared audit’s 5,191-byte
requirement. Direct checks found all 16 absent and preserved the other 699
containers, 123 volumes and 36 images. The graph continued with both worker
connections. The original post-check failed on four PostgreSQL parallel
workers on the owned databases. It did not record their leader PIDs. A later
read through `postgres` linked two different parallel workers to the second
verification client. Independent review accepted the removal and current
resource preservation. The shared audit still awaits full graph verification
and owned database cleanup
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/retired-hold55-62-helpers.json)).

At 21:43:22 UTC the next hold activated before its predecessor expired.
All seven administrative steps completed and 39 independent checks passed.
The same verifier continued with a review deadline of 22:43:11 UTC
([receipt](../../evidence/runtime/postgres-stage2-2026-11-18/hold-review-20261118-214311.json)).

### All units returned and 13 writes timed out, 2026-11-19

The November 18 verifier exited with code 1 at 22:23:22 UTC, before the
22:43:11 hold deadline. It ran for about 5 hours 38 minutes. The terminal
receipt was read November 19. No overnight terminal report reached the
user. Docker recorded no OOM kill. All 108,390 expected units returned once.
There were 107,474 matches, 903 mismatches and 13 failures. Twelve link
units and the inventory unit rolled back when their writes exceeded 75
seconds. No shard aborted. The supervisor recorded a generic error after
exit; the graph receipt identifies the individual failures.

The 653 previously witnessed or owner-accepted rows match exactly. Of the
716 units missing in the preceding comparable graph, 467 now match and 249
differ. One formerly timed-out unit supplies the 250th additional mismatch.
Of these 250, 248 differ only in dependencies. The 2026 year projection differs in output
and dependencies. The Easter 2026 link unit differs only in output. Forty-two dependency pairs appear in the earlier witness. The
additional rows have no blanket disposition.

Independent review verified that all three owned databases and their
registry entries were gone. The original five catalog entries, source size
and 14 protected service records matched. Administrative observations
used `postgres`. The source had zero clients. These observations cover
catalog and size, without a new full source-content hash. The reviewer
accepted the failed measurement and cleanup. Shared migration and re-key
still await graph acceptance. Preparation of the owner's one-run diagnostic
continues ([receipt](../../evidence/runtime/postgres-stage2-2026-11-19/normal668f-terminal.json)).

The rehearsal hold reopened on November 19 at 15:34:33 UTC, 16 hours
51 minutes after expiry. All seven steps completed and 32 independent
checks passed. The review deadline is 16:30:55 UTC. The verifier remained
stopped throughout this reopening ([receipt](../../evidence/runtime/postgres-stage2-2026-11-19/hold-reopened-20261119-153055.json)).

### Stage 2 time limits waived, 2026-11-19

The owner extended the waiver to all Stage 2 time limits until further
notice. The core runner now reports elapsed time and whether it exceeded
300 seconds. All 68 runner tests passed, including a simulated 302-second
complete run and existing failure and cleanup cases. Ruff and mypy passed.
The full suites last ran before this change.

The rehearsal timing format passed 51 synthetic checks. A projection using
one rollback event for each of the 108,390 actual unit names fits in
85,547,136 bytes under the 134,217,728-byte receipt cap. Removing validated
timing data recovers the original rows exactly. These checks used saved rows.
The next server run still needs the broader workload waiver wired through
the supervisor and owned database sessions, a qualified image and launch.
Local free space is about 64 GiB against the 80 GiB minimum for a build
([waiver](../../../docs/plans/postgres-migration/7-stage-2.md#stage-2-time-limits-waived-until-further-notice)).

The next verifier candidate now covers the 75-second write limit, whole-graph
and clone deadlines, session statement, lock, idle and transaction timeouts,
and the supervisor’s elapsed hold-review check. It passed 67 synthetic checks.
On the local PostgreSQL 17.11 test service, the exact SQL emitted by the
candidate set all four session timeouts from 50 ms to zero. A read-only query
that slept for 125 ms completed. A new connection showed the original settings.
This exercised the database settings only. Source identity and ownership
checks still need the full server run. The local observer also reproduced the
November 18 terminal result from saved bytes and kept watching through an
earlier warning. Neither the verifier nor the observer has been launched.

The November 19 hold review took effect at 16:11:08 UTC before its
predecessor expired. All seven steps returned zero and the independent
review passed 38 checks. The verifier was still stopped. The five catalog
rows, source size and 14 protected service records matched the terminal
observation. The next review is due at 17:10:46 UTC
([receipt](../../evidence/runtime/postgres-stage2-2026-11-19/hold-review-20261119-161046.json)).

At 16:52 UTC the server assembled the verifier archive using the qualified
base image archive and the new timeout settings. The helper exited zero
after 8.42 seconds. Independent review confirmed the archive checksum, all
23 entries and the 249,067,520-byte length. The five database catalog rows,
source size and 14 protected services matched the earlier observation.
Docker image loading, installed-file checks and a fresh graph run are pending
([archive receipt](../../evidence/runtime/postgres-stage2-2026-11-19/waived-verifier-archive.json)).

The hold review took effect at 16:58:40 UTC. All seven steps returned zero
and the independent review passed 44 checks. The verifier was stopped.
The next review is due at 17:58:30 UTC. The current revision has one byte
left in its 200 KiB evidence allowance. Image loading and the next graph
run await room for their receipts. Closing the local revision needs owner
approval under the repository rules
([hold review](../../evidence/runtime/postgres-stage2-2026-11-19/hold-review-20261119-165830.json)).

### November 18 verifier mismatch accounting, 2026-11-19

The November 18 verifier reported 903 distinct mismatches. A per-unit join
against the raw receipt returned each unit once. It excluded all 13 failed
writes. The 401 previously witnessed link version differences, 248 previously
witnessed inventory differences and four owner-accepted link differences
match the November 17 result rows exactly. The four retain their earlier
disposition for later data QA.

For the next 248 dependency-only units, four saved November 11 server
captures contain the same unit names, generations, output labels, recipes
and dependency hash pairs. For every unit, replacing only the previous
selected inventory group and sorting the outer manifest reproduces the
November 18 found hash. These 248 consumers share 207 distinct roots.
Together with the earlier 248 they account for 492 resolution units and
four year units. Their stored output hashes already matched the verifier.
The full historical reconstruction has SHA-256
`2f1387527b6ebff2c563b2fdcbfefded816f8e261ec7134173304a60af235904`
([earlier reviewed captures](#all-496-dependency-differences-reconstructed)).

Two diagnostic reconstructions reproduced the exact remaining labels.

- Easter Swing 2026 has 5,759 rows. Changing only the string
  `identity_links.linker_version` from `9` to `10` on 1,339 rows
  reproduced its found hash. Its dependency digest stayed the same.
- The 2026 year projection has 15,140 rows. Applying the year transform with
  the November 18 verifier clock reproduced its found hash. Two coverage
  rows changed. The listed-only event count fell from 9 to 8 for the WSDC
  calendar via Wayback, and from 1 to 0 for the WSDC registry via origin.
  Both rows took the verifier's change timestamp. All 202 event tiers and
  14,931 placement associations matched. The inventory substitution also
  reproduced the changed year dependency root. The historical event or
  membership change behind the two old counters is unverified.

The operator incorrectly directed agents to use the SQLite reader that
had been stopped pending source-writer visibility. Two agents transferred
row data into local scratch despite the server-only rule. Reads stopped.
The owner authorized deletion of the six local exports and row-level
derivatives. After an independent metadata check, the operator unlinked the
six exact files and verified their absence at 19:04 UTC. They totaled
34,536,905 logical bytes. Hash-and-count summaries remain. The two output
reconstructions require confirmation through an admitted server-local
reader. No original source, graph result or deployed data was changed.

Independent review at 19:05 UTC accepted the exact union of 897
hash-attributed units and the four existing owner dispositions. It rejected
the two new SQLite-based explanations as gate evidence. Their next check
needs an admitted owned copy and hash-only reporting. The review has
SHA-256 `b15a3da3bab4e5a11ab5a5f7c487ba1bf067ff86b52b5ab1ccd9f8709b13ce28`.
The six-file deletion log has SHA-256
`b21f1c55928f88b751e40fc201cffa0ac59018c16679988c1995c1ba08f2284e`.
Both files are in the same local scratch directory as the accounting.

The local per-unit accounting is `/tmp/swingset-sol-all-7kj0ht4w/mismatches-903.csv`,
SHA-256 `523918627dfecb609f78f5603f13fc1a9deb242872cfe430be8a2a8b9ee27f94`.
Keep all 903 units outside clean re-key and use the normal re-derivation queue.
This investigation grants no graph acceptance. The 13 rolled-back writes
still need a successful run
([November 18 receipt](../../evidence/runtime/postgres-stage2-2026-11-19/normal668f-terminal.json)).
