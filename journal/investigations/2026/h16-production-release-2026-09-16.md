# H16 production release

The owner authorized production deployment and publication after the repaired
H16 release passes validation. See [D-0029](../../decisions/0029-resume-h16-production-release-after-validation.md).
This record distinguishes preparation, deployment, initialization, build,
audit, and publication. An earlier phase does not establish a later one.

Completed: the original frozen H16 release was published and remotely verified
at 22:37:24 UTC as
[`2a6c7dc744fb36eabb5163c0a527d787d3721f4f`](https://huggingface.co/datasets/skeswa/swingset/tree/2a6c7dc744fb36eabb5163c0a527d787d3721f4f).
The later event-completion runtime remains local and is outside this release.

## Reviewed input

- Frozen schema-14 source: `/nix/store/z689qy41inndill3d92ym8im852x3649-source`.
- Source receipt SHA-256: `0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb`.
- System: `/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
- Bundle: `fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a`.
- Production operation: `/var/lib/swingset/operations/h16-event-preservation-release-20260916`.

The frozen source excludes the later event-completion extension and Monterey
changes. Initialization uses the frozen source's config and overrides.
Scheduled jobs must stay held while their external override paths differ
from the reviewed bundle. Preserve the existing predecessor checkpoint and
all failed scratch candidates.

## Preparation

The production driver packet passed 105 offline guard tests. The coordinator
independently checked every prepared file hash and replayed every recorded
literal replacement against the retained predecessor. The initializer is
unchanged; other driver changes bind the new source, bundle, system, and
operation directory. Preparation receipt SHA-256:
`fb847c2302126a2dad31ba1e17226c2c60150b2ea45c89ab42181214c6ccbb9b`.
See the [packet](../../evidence/releases/h16-event-preservation-2026-09-16/production-preparation/README.md).

Full scratch replay passed independent verification: 34,986 successful
generations, 2,541 linked events, and no unfinished scopes. The coordinator
accepted the disclosed resource-sampling gap using independent lifetime
memory records and final state verification. See the
[replay review](../../evidence/releases/h16-event-preservation-2026-09-16/replay-coordinator-review.json).
The scratch build started at 19:41:44 UTC and passed: 385.26 seconds building,
441.81 seconds overall, with 90 resource samples and 5.11 GiB peak sampled
anonymous memory. Candidate `cand_2a6ff4c91cc64ee4` has manifest
`2c23cd4be4995cfef4645cd553a745c05bfc0b2e495d3c08e772dd765be474c1`.
The coordinator checked the exact replay-to-build receipt chain and released
the [independent audit gate](../../evidence/releases/h16-event-preservation-2026-09-16/build-coordinator-audit-gate.json).
The independent audit passed all 54 checks in 29.07 seconds. It preserved all
4,931 named judges, including null-ID records; matched 1,888 retained registry
occurrences; retained all 34,955 baseline entry IDs and 66 coverage-year rows;
and found no duplicate keys, orphaned references, or identity violations.
Audit receipt SHA-256:
`862a880eaf2d0263b5f35ed8733c90bd21a818c3eab3a0929c875ef24fb7b81d`.
The [scratch acceptance review](../../evidence/releases/h16-event-preservation-2026-09-16/scratch-acceptance-review.json)
authorized production preflight.

Private staging verified 18 files, the expected old active/persistent systems,
and all six ordinary units inactive. No database was opened by staging. See
[the staging receipt](../../evidence/releases/h16-event-preservation-2026-09-16/production-preparation/private-staging.json).

## Production phases

Production preflight passed in 61.77 seconds with direct exit 0 and about
163 MiB peak sampled anonymous memory. Protected live state matched the
verified H15 checkpoint; the V4 public baseline and holds were unchanged.
Its `passed=false` field denotes the not-yet-executed full acceptance;
`preflight_passed=true` is the intended preflight success result. Receipt
SHA-256: `3999c52e966407116568a6b4438618af837219e11feed8db33d8b812dc23f575`.

Production activation passed at 19:58:29 UTC in about 1.8 seconds. Both active
and persistent systems now point to the reviewed `sx7lpr80…` system. All six
ordinary units remain inactive and the runtime hold is intact. The coordinator
independently read the active system link. Deployment receipt SHA-256:
`342be820b7a4e4e1e7a354288fabf74f17e2d80253ed0e38f0a7ccecca5b9678`.

Read-only production acceptance passed at 20:02:21 UTC in about 105 seconds
overall. Its before/after protected state is identical; fresh-process doctor
and human/JSON requirement checks passed. No source requests, migration,
service execution, or publication occurred. Acceptance SHA-256:
`d9bcde097dd694e0c9ec0de28b048a140c8a1f59eb32180a2e5491587bdb5c4f`.
The large doctor JSON and human reports remain byte-exact local and private
VM artifacts under the diagnostic-dump ignore policy. The retained acceptance
and capture receipts record their sizes and hashes.

The coordinator reviewed the exact initialization gate, SHA-256
`45c7abb4843ba7e90c2a93f6326dba939d2f8a321f407dcbd7fecee99582c4da`,
and authorized preparation only. It binds actual acceptance, final scratch
replay, build, audit, and nested backup evidence. Preparation stopped at
20:09:00 UTC: the operational driver assumed the legacy `recipe/runtime` row
existed. The missing row raised before marker creation, bundle capture, or
input acceptance. No derivation ran. A read-only follow-up confirmed nine
bookkeeping/control tables and backup-neutral metadata still match preflight
and the verified checkpoint. Failed receipts and logs are preserved in
[the first preparation attempt](../../evidence/releases/h16-event-preservation-2026-09-16/production-preparation/initialization-prepare-live-001/).
A new versioned helper treats the absent prior recipe as unknown and uses
ordinary runtime invalidation. Seven tests against the frozen runtime passed;
180 downstream guard tests also passed. The coordinator verified the exact
one-lookup code change and every downstream hash/path substitution. The
frozen application source and bundle remain unchanged. See
[D-0032](../../decisions/0032-handle-missing-legacy-runtime-recipe-during-initialization.md).
The second preparation attempt passed that lookup and saved marker
`dad425983041d0a27ab2eae1127db1fc9aeec4a401f50d678d6fe87e176baece`,
then failed opening a legacy root-owned mode-0644 `control.lock`. Input
acceptance had not run. A bounded read-only diagnostic matched all nine input,
work, and control tables and backup-neutral metadata against the checkpoint.
At 20:30:09 UTC the targeted repair preserved inode `6400127`, contents, and
mtime while restoring service ownership and mode 0600 under both locks.
Repair receipt SHA-256:
`17cd96a85db85ba067fe19e8d58a6cc02a2bbc1ef4e71f1a1511373c1eb54563`.
Preparation attempt 003 reused the existing immutable marker and passed at
20:32:06 UTC. Input acceptance took 14.17 seconds inside the initializer;
the launcher, including prerequisite checks, took about 35 seconds. It accepted
the exact rehearsed bundle, invalidated old extractor cache labels, and performed
no source requests, parse execution, derivation attempts, build, or publication.
Successful preparation SHA-256:
`3194b019cddadf35e1c029257a41f35ab183611086a3c03a2371c5e61b36061f`.
See
[D-0033](../../decisions/0033-repair-production-control-lock-ownership.md).
A separate read-only anchor must capture
post-prepare parse tokens before the guarded supervisor starts.

The first anchor passed its semantic checks, including zero work attempts,
31,821 queued parse tokens, and seven inherited open runs, but stopped
on filesystem metadata: read-only SQLite created an empty WAL sidecar. The main
database's size and mtime remained identical. Both sidecars were service-owned
mode 0600. Failed anchor SHA-256:
`1c6d181c60b570f4af23fa2b0ffe1911ead968ee9b5744defcd4fe2a022788be`.
The coordinator authorized a fresh output from the same strict verifier with
the sidecar now present; no validation condition was removed.
The retry passed in 7.21 seconds with zero database changes and identical
main/WAL metadata. Successful anchor SHA-256:
`2bff03bce51be83ed62315274adbc07d0a5396d7b10e74a214bf9b0fb0421654`.
The coordinator independently reviewed it and authorized guarded project/link
session `reviewed-002`, at most twelve 900-second invocations, with the reviewed
6 GiB anonymous-memory guard. Final independent verification remains required
before the production build.

The reviewed memory guard's version 002 passed 65 offline tests; the independent
anchor/final verifier passed 53. Both are staged privately. See
[D-0031](../../decisions/0031-guard-production-initialization-memory.md).
Guarded production project/link initialization started at 20:36:11 UTC in
session `reviewed-002`. The first unit is
`swingset-h16-init-reviewed-002-001.service`. The root wrapper and worker logs
remained private VM-local files while active. Initialization completed at
22:16:45 UTC across seven successful bounded workers: 32,445 projections and
2,541 links, with no unfinished scopes. All attempts succeeded and the original
evidence, input authority, parse work, controls, and public baseline were preserved.
Peak sampled anonymous memory was 1,779,904,512 bytes. Closed worker, supervisor,
and resource logs are retained in `production-preparation/initialization-reviewed-002-live/`.

The independent final verifier passed in 23.18 seconds. It confirmed all 34,986
attempts, empty unfinished work, no foreign-key violations, a passing SQLite
quick check, unchanged parse tokens and seven inherited open runs, and zero
database writes with stable database/WAL metadata. Receipt SHA-256:
`dfd48d5550118410ecaab2ebd0e40ae132ab9b789ed948ea1474b9b7d7e33157`.
The coordinator reviewed the actual result and authorized the production build
through gate SHA-256
`ae511f0734be86aa1210a7f7a0b46064d984ee78e9206ab8e9a3bb565bb3fec4`.
The first build launch stopped before entering the build: the service account
could not hash two root-owned mode-0600 verification receipts. The failed
launch and its logs are retained. At 22:25:02 UTC a narrow metadata repair gave
the service group read access to those two receipts, preserving root ownership,
their inodes, bytes, hashes, and mtimes. The service user then read and verified
all six gate references. Repair receipt SHA-256:
`2313f21351ededa0185f973f6985d840cd85e803d9785437e5c84ca8d522fe1e`.
See [D-0040](../../decisions/0040-allow-service-read-access-to-verifier-receipts.md).
The same build gate passed through a fresh
`swingset-h16-production-build-reviewed-002.service`, using the unchanged serial
launcher, 6 GiB anonymous-memory guard, and frozen source. The driver took
388.23 seconds; total elapsed time was 401.55 seconds, with 5,477,404,672 bytes
(5.10 GiB) peak sampled anonymous memory. Candidate `cand_8f31cad7226643ae`
has manifest `6ff950afb842c2e2f1fc24c937df6633c890309bc9c6189b8706a4884eadc5a6`.
Its build receipt SHA-256 is
`f0b61a6adf804bcba992d3d6a8c1210f513887d63bec6e3b5b56bc8754b0f48c`.
Semantic publication preflight and all five preservation checks passed.

The independent production audit passed all 54 checks in 25.01 seconds. It
preserved all 4,931 named judges, including records without WSDC IDs, and all
34,955 existing default entry IDs. All 1,888 retained registry occurrences were
associated correctly, all 66 prior coverage-year rows survived, and all 17 years
were disclosed. No duplicate keys, orphaned references, or identity violations
were found. Audit receipt SHA-256:
`447cdf421482c35e8d619c1b4672ab31a74b93708b06dbdafc85421665e31560`.

Publication used the reviewed production candidate and expected V4 parent
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`. Remote verification acknowledged
commit `2a6c7dc744fb36eabb5163c0a527d787d3721f4f` at 22:37:24 UTC, and the local
baseline was promoted to that candidate. The publication driver took 58.32
seconds; direct process exit was zero and all preservation checks passed.
Its network request count is not instrumented. Publication receipt SHA-256:
`32c9c36ccd7d95c967e65115fcc07f6d32354f63d95b03cd37ce54a5b30324c1`.

The release contains 2,541 events, 4,931 judges, 93,820 entries, 197,990 registry
placements, and 4,176 coverage rows. The coordinator independently checked the
live publication receipt, baseline link, `PUBLISHED` record, manifest hash, and
active/persistent NixOS systems. See the
[final acceptance](../../evidence/releases/h16-event-preservation-2026-09-16/production-preparation/production-release-final-acceptance-001.json).
All phase processes stopped. The operator hold and all six inactive scheduled
units remain in place pending separate ordinary-runtime input and activation
checks. No historical acquisition, year acceptance, identity expansion, or
deployment of the newer local event-completion runtime is implied.
