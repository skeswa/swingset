# Schema 29 successor rehearsal packaging, 2026-09-17

## Candidate 006 production outcome

Candidate 006 used packet 005, SHA-256
`0d455559b7e0cd767cb1739f10271eb12e8178bebb5a4ab283bd8e74733b6ff3`,
against checkpoint 004. Its exact source, NixOS build, service bindings, packet,
actual migration, restore and scratch-input receipts passed independent review.
The [live preflight](../../evidence/runtime/held-schema29-migration-2026-09-17/preflight-001/receipt.json)
passed without mutation. The first system-switch command failed before changing
either system link because it named an absent `nix-env`; that log remains
retained. The corrected switch activated candidate 006 under the hold.

The [live migration](../../evidence/runtime/held-schema29-migration-2026-09-17/execution-001/receipt.json)
passed at 23:11:59 UTC. All 116 predecessor logical table receipts were
unchanged, and only the one-row dispatch-fence table was added. The
[independent postmigration audit](../../evidence/runtime/held-schema29-migration-2026-09-17/postmigration-review-001/receipt.json)
matched those receipts to checkpoint 004 and fresh WAL-aware live reads. Both
schema markers are 29; integrity and foreign keys pass. The active and
persistent candidate-006 system, exact hold, seven sidecars, unchanged H16
baseline and six inactive ordinary units were verified.

The separately gated production input operation then passed at 01:39:24 UTC on
2026-09-18. Its preflight was read-only, its disk-backed seal changed only the
private copy, and execution matched the seal across all 117 tables. Exactly 12
accepted input values changed within the 51-row map; 112 tables remained exact,
the hold stayed present, and all 4,931 named judges were preserved. See the
[compact operation receipt](../../evidence/runtime/held-schema29-input-acceptance-2026-09-17/receipt.json).
Collection resumption, repair activation, source requests, public writes and new
publication did not occur. Candidate-005 rehearsals remain historical evidence
and do not certify candidate 006.

## Candidate 005 successor

After the owner resumed work, the coordinator generated
[packet 003](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet-003/packet.json)
for source `/nix/store/z0rnsn69aav2h8p2wgzydar6k9rw5kk9-source`, receipt
`9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99`.
Its packet SHA-256 is
`6748af985bbe7b6a74c30c094c72cc117fd6f259a661fb5aeb052e51e309d230`.
Independent review passed 41 tests, Ruff, mypy and a byte-identical 11-file
rebuild. See the [review receipt](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/candidate005-review-001/review-002/checks.json).
The builder's difference from packet 002 is formatting only; the input helper
binds the new source receipt. The first rebuild attempt used a Mac temporary
path unavailable in the VM and remains retained separately.

Packet 003 still binds checkpoint 002 and its 15-request cutoff. A fresh
verified checkpoint must preserve later paid usage before live rollout.

The [actual migration](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/migration-001/migration-receipt.json)
passed at 18:40:47 UTC. All 116 predecessor tables were unchanged; only
`history_dispatch_fence` was added. Integrity, foreign keys and reopening checks
passed. The [independent audit](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/actual-review-001/migration-audit.json)
also verified the VM specimen, hold and retained receipt bytes.

[Scratch preparation](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-001/prepare.json)
passed at 18:45:44 UTC. [Input acceptance](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-001/accept.json)
passed at 18:48:32 UTC with bundle
`f4e789c30a69d8f439f2e1068f7a620d6cff8160d329e05474ab5f92929b5426`.
All 4,931 named judges and their null WSDC IDs were preserved. Protected request
accounting, controls and original history checks passed. These operations used
disposable `/var/tmp/swingset-schema29-input-20260917-001`; they did not accept
production inputs.

The [actual restore](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/restore-001/restore-receipt.json)
passed at 18:53:25 UTC. It made 112 serial public verification requests, zero
source requests and zero public writes. It preserved the hold and all 116
predecessor tables with only the documented singleton pressure-epoch increment
during restore. Subsequent migration preserved the restored table receipts;
`RESTORE_PENDING` cleared after the protocol verified the acknowledged baseline.
This validates checkpoint 002, including its 15 paid Archive requests, not later
production bytes.

The [first bounded scratch replay](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-001/drain-001.json)
closed at 19:01:21 UTC: 100 attempts in 402.1713 seconds, comprising 32 parse
commits, 67 projection commits and one parse admission requiring review. It had
no interrupted units or deadline overrun. All 4,931 judges and protected
accounting, controls and history remained preserved; the
[independent audit](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/actual-review-001/drain-001-audit.json)
passed. The WDR rounds input retained critical unknown and non-authoritative
row-loss warnings; it stayed unpromoted while independent work progressed. See
the [blocked input](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-review-001/blocked-attempt.json).

The scratch queue still has 31,789 parse and two projection units. This bounded
turn does not establish sustained throughput or fixed-cohort acquisition
service. Production remains schema 28 under hold, with no new publication.

The [isolated timing diagnostic](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/overhead-002/overhead.json)
passed at 19:13:04 UTC. Seven paired samples of 9,800 no-op updates measured 49
populated tables out of 59 targets. Median update time rose from 9.59 to 12.49
microseconds (2.90 microseconds added, ratio 1.3025). All 177 schema-29 triggers
were verified; 489 older triggers were disabled equally in both variants, then
restored. The specimen and scratch database SHA-256 remained identical. This
excludes older-trigger interactions, durable commits, selection, parsing and
acquisition. Ten focused tests, Ruff and mypy passed in the
[coordinator review](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/overhead-review-002/checks.json).
The first cohort preflight had no eligible rows under its older isolation rule;
no benchmark ran in that attempt. See [D-0094](../../decisions/0094-measure-schema29-cost-on-a-disposable-copy.md).

The [second bounded turn](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-001/drain-002.json)
closed at 19:21:43 UTC: 100 attempts in 413.86 seconds, with 31 parse and 67
projection commits, one admission requiring review and one unit exception.
Preservation passed again, with no interruptions or selection-budget overrun.
The two turns total 200 attempts and 197 commits; they leave 31,758 parse and two
projection units. The first turn's blocked WDR snapshot was not retried.

The [second-turn blockers](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-review-001/drain-002-blockers.json)
include a different admission-review input and a manual `registry_crosscheck`
snapshot that the ordinary page-kind lookup does not recognize. That exception
requires routing investigation; successful bounded turns do not close operating
acceptance. No ordinary production unit has resumed.

The fresh schema-28 checkpoint attempt 003 verified 68,866 files and
8,770,615,161 bytes locally, manifest
`6ac7bb2d291136ef502abcb33f8762bf4449fd2fdc0160fb995f2aba0921f9d9`.
Its upload was OOM-killed at 19:31:16 UTC because the coordinator's transient
command omitted the existing `TMPDIR=/var/tmp` setting. The upload was building
a tar in memory-backed `/tmp`; no remote acknowledgment was established. The
[failed attempt](../../evidence/runtime/schema28-checkpoint-2026-09-17/production-003/failure-state.json),
verified checkpoint and service log remain retained. Only its closed, incomplete
temporary tar was removed, with a cleanup receipt. Production remains unchanged.

[Checkpoint attempt 004](../../evidence/runtime/schema28-checkpoint-2026-09-17/production-004/receipt.json)
passed at 19:39:00 UTC with the same local manifest and disk-backed temporary
storage. Its verified private acknowledgment is
`68d738dcd78cb1654d053b479ea6dbd911f4b8ed`; live database changes were zero.
The checkpoint contains 20 paid Archive requests (2,952,065 bytes), four DCN
requests (132,764 bytes), and the durable origin-day and robots-provenance files.

The new builder passed [independent coordinator checks](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/current-builder-review-002/checks.json):
56 tests, Ruff and mypy. The earlier check attempt named a nonexistent test
file; that invocation failure is retained as review 001. Actual
[packet 004](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet-004/packet.json)
SHA-256 `366b0dbab70048cc2b85c4bdb7e06d24973478d8cfee965ee01ebb40d7fb5300`
binds candidate 005 to checkpoint 004. The
[independent packet review](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet004-independent-review-001/audit.log)
verified its exact closure, checkpoint evidence, usage and sidecars. The
[actual migration attempt 002](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/migration-002/migration-receipt.json)
passed at 19:49:15 UTC: all 116 predecessor table receipts and seven top-level
sidecars were preserved, with only the dispatch-fence table added. Integrity,
foreign keys and reopening passed. Its independent audit and separate actual
restore and input replay remained required at that checkpoint. No live
schema-29 migration, production input acceptance, ordinary resumption or
publication had occurred yet.

The [fresh migration audit](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/actual-review-004/migration-audit.json)
passed, including the coordinator's separate checkpoint/specimen byte checks.
The [fresh actual restore](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/restore-002/restore-receipt.json)
passed at 20:07:05 UTC, and its
[independent audit](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/actual-review-004/restore-audit.json)
passed. All 116 predecessor tables were preserved except the documented
singleton pressure-epoch increment before migration. All seven sidecars were
preserved. The restore made 112 serial public verification reads, zero source
requests and zero public writes. Production stayed on schema 28 under hold.

Input preparation attempt 002 then correctly rejected extra SQLite sidecars
beside checkpoint 004. The coordinator's earlier inspection used ordinary
read-only SQLite access, which created an empty WAL and 32 KiB SHM file at
20:01:57 UTC. The failed
[preparation receipt](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-002/prepare.json)
and both temporary file bodies remain retained. Cleanup checked their exact
hashes, empty WAL, regular-file identity, closed operations and absence of
open file descriptors before removing only those unmanifested files.

The cleanup script's final inventory check incorrectly excluded a nested
`checkpoint.json` by basename and stopped; this verifier error is retained.
A separate [complete verification](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/checkpoint004-transient-verification-002/verification.json)
passed at 20:13:21 UTC: all 68,866 files and 8,770,615,161 bytes match the
original manifest, with no missing or extra paths. No retained database,
manifest or receipt bytes changed. [D-0102](../../decisions/0102-read-sealed-checkpoints-without-sqlite-sidecars.md)
records immutable checkpoint reads and the distinction from live WAL reads.

[New input preparation attempt 003](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-003/prepare.json)
passed at 20:15:49 UTC against the same reviewed packet and checkpoint. Input
acceptance passed at 20:18:40 UTC. The bounded replay
[receipt](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-003/drain-001.json)
closed at 20:26:59 UTC: 100 attempts produced 32 parse commits, 67 projection
commits and one admission review in 408.47 seconds. It preserved all 4,931 named
judges, issued no network requests and left 31,789 parse and two projection
units. This closes the missing receipt gap; it does not establish sustained
throughput, production input acceptance or operating acceptance.

The later read-only attempt-interval query found 12.088 and 7.605 seconds of
recorded work execution in the first two 100-attempt turns, versus 385.990 and
397.264 seconds of preceding gaps. See the
[timing result](schema29-replay-throughput-next-2026-09-17.md#retained-query-result).
Those gaps include selection, accounting, admission and derivation capture; they
are not a pure selector timer. A bounded selector-only candidate-bound profile
is the next diagnostic before more replay or parser optimization.

The sections below retain the prior candidate-004 packaging record and commands.

The [builder](../../tools/runtime/prepare_schema29_rehearsals.py) prepares three
separately executed rehearsals for the actual schema 28 checkpoint. This is local
implementation and offline validation. It does not establish production input
acceptance, deployment, ordinary collection, repair activation or publication.
The engineering choice is [D-0086](../../decisions/0086-seal-schema29-successor-rehearsals.md).

## Exact authority

The predecessor checkpoint is
`/var/lib/swingset/checkpoints/extension28-held-20260917-002`, manifest SHA-256
`4929859092cbd3e15122f3598b5da477a65498976cc744c52cf2b142d1cebea3`.
Its acknowledged private archive commit is
`d71060d4f77b6073f797bd7232bf2aa3694ba685`. The
[coordinator's checkpoint receipt](../../evidence/runtime/schema28-checkpoint-2026-09-17/production-002/receipt.json)
records local verification and remote acknowledgment. The packet seals that
passed receipt, its exact checkpoint-verification record and the operating
helper that checked the private remote head and manifest hash. Execution checks
their bytes and semantic checkpoint/commit binding. It does not renew that
acknowledgment or fetch the private archive.

The packet builder requires one complete frozen source manifest with an exact
schema 29 literal and an exact deployed Nix source path. Candidate 004 is
`/nix/store/c0dpchrf4b6hkxlc2yn4jm1v96ld0xd2-source`, source receipt
`44208cb5b38f0cd3f8b6c7c91f1b5d1cd650ef677d52b4bd54c17d8576c485e7`.
Building a packet for it does not assert that its full validation or operational
acceptance has passed. The coordinator selects the accepted packet after those
separate gates.

The packet contains exact prior helper bodies, derived helper bodies and its
runner. Base SHA-256 checks and counted replacement seams reject unexpected
upstream edits. The packet digest covers the complete closure; extra files and
changed bytes fail execution. Direct derived-script execution is refused.
The runner supplies all source, checkpoint, archive, schema and helper identity
flags; callers cannot override them. It checks both predecessor schema markers,
the 116-table population and settled execution admissions before running a
driver. Both scratch schema markers must be 29 before accept or drain; those
phases cannot authorize a migration. Runtime imports must belong to the exact
frozen source, which is verified before and after work by the reused helpers.

## Distinct operations

`migration` copies only the verified database and hold to a new disposable
directory. It compares every predecessor table, runs integrity and foreign-key
checks, reopens the specimen and verifies its hold. It performs no network
requests and is not an operational restore.

`restore` uses the existing top-level `restore_from_checkpoint` protocol. It
requires the live hold and inactive ordinary units, copies the complete private
checkpoint, preserves `RESTORE_PENDING` during public verification, and inspects
the actual public head and manifest. The acknowledged public baseline remains
`2a6c7dc744fb36eabb5163c0a527d787d3721f4f`, candidate
`cand_8f31cad7226643ae`. A mismatch leaves the barrier installed. After successful
verification, restored state briefly remains schema 28 under the byte-identical
hold before the explicit schema 29 migration. No workers start during that
interval. Public reads retain the reviewed serial transport and spacing; no
source acquisition or public writes occur.

Normal restore activation advances the singleton `event_pressure_state.epoch`
once, making old scheduling hints stale. The successor computes exactly that
expected change from the checkpoint and compares all 116 tables against it.
The singleton sequence and every other row must remain exact. Any additional
mutation rejects the rehearsal. The subsequent migration comparison starts from
that verified restored state; it does not erase the recorded restore delta.

`inputs prepare` copies and verifies all private checkpoint artifacts into new
disposable state and migrates it. All 116 predecessor tables must remain exact.
`inputs accept` uses normal frozen input capture and acceptance on that scratch
database only. External configuration and the complete CSV override inventory
must equal the frozen source. `inputs drain` uses the existing ordinary
parse/project/link selector and worker, control scopes, retry and admission rules.
It cannot fetch, publish or enable repairs.

During acceptance and drain, paid host budgets, host rows, completion spacing,
spacing-baseline authority, acquisition-turn/request rows and controls remain
exact. Original immutable event-operation, progress, accounting, retirement and
timing-history rows are protected through marker-bound high-water receipts. New
ordinary interpretation history is allowed; new acquired-operation facts and
request admissions are forbidden. Derived observation caches can change through
ordinary interpretation. The existing complete retained-file checks, named-judge
continuity checks and nullable WSDC-ID preservation remain in force.

Drain bounds are unchanged: at most the selected unit limit, with a selection
time budget rather than a hard total wall deadline. An already selected worker
and finalization can overrun that budget; receipts report elapsed time. Queue
counts and a turn with no runnable unit do not prove fleet completion. Multiple
turns and independent release coverage remain necessary.

## Commands

Build into a new directory using the reviewed helper bodies:

```sh
python journal/tools/runtime/prepare_schema29_rehearsals.py build \
  --source /private/tmp/swingset-extension-freeze-20260917-004 \
  --source-receipt-sha256 44208cb5b38f0cd3f8b6c7c91f1b5d1cd650ef677d52b4bd54c17d8576c485e7 \
  --runtime-source /nix/store/c0dpchrf4b6hkxlc2yn4jm1v96ld0xd2-source \
  --bases journal/tools/runtime \
  --output <new-packet-directory>
```

Use the candidate runtime's Python environment and `PYTHONDONTWRITEBYTECODE=1`.
The coordinator supplies the reviewed packet SHA-256 for every invocation:

```text
python <packet>/runner.py run --packet-sha256 <digest> migration -- --destination <new-directory>
python <packet>/runner.py run --packet-sha256 <digest> restore -- --destination <new-directory>
python <packet>/runner.py run --packet-sha256 <digest> inputs -- prepare --scratch <new-state> --output <new-receipt>
python <packet>/runner.py run --packet-sha256 <digest> inputs -- accept --scratch <state> --output <new-receipt> --marker-sha256 <prepare-marker-sha256> --config <exact-config> --overrides <exact-overrides>
python <packet>/runner.py run --packet-sha256 <digest> inputs -- drain --scratch <state> --output <new-receipt> --marker-sha256 <prepare-marker-sha256> --config <exact-config> --overrides <exact-overrides> --max-units 100 --max-seconds 600
```

Migration and restore destinations and input receipt paths must be new. Preserve
failed receipts and specimens; inspect the failure before constructing another
run. Do not execute the old schema 14 main functions against this checkpoint.

## Offline validation

The earlier [38-test check](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/offline-check-001/checks.json)
and packet 001 remain historical. Review found that their restore comparison
would reject the normal epoch invalidation; they are superseded and must not
be used for operational restoration. The successor adds that narrow contract,
actual restore-main acceptance/corruption tests and sealed private-archive
verification evidence.

The [independent final review](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/independent-review-001/checks.json)
passed 41 tests against candidate 004 in 2.43 seconds, Ruff and mypy. It rebuilt
the complete packet byte-identically and verified unchanged helper, test,
dependency and packet hashes. There were no remaining blocking findings.
The [successor packet 002](../../evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet-002/packet.json)
has SHA-256 `3dae03a682ed2d75ad9ac0d41b264335c062bc15dccd4503b1aa7b34c41f9ecf`.
It is bound only to candidate 004. A later source freeze needs a newly generated
and reviewed packet; the failed candidate-004 full-suite receipt is not promoted
by these focused helper checks.

The new tests exercise actual schema 28 databases, all 116 predecessor tables,
schema 29 migration, the top-level restore protocol with mocked public transport,
remote mismatch barriers, byte-identical holds and paid completion-spacing rows.
They also test original history preservation, ordinary offline parsing, exact
schema markers, fixed authority flags, deterministic packaging and altered
closure rejection. These specimens do not substitute for the coordinator's
actual checkpoint rehearsals or operating acceptance.
