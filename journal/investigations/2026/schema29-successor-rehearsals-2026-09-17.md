# Schema 29 successor rehearsal packaging, 2026-09-17

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
