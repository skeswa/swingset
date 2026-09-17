# Extension input rehearsal preparation, 2026-09-17

The [helper](../../tools/runtime/rehearse_extension_inputs.py) prepares an offline
rehearsal of input acceptance and ordinary parse/project/link work. It is
implemented locally. No actual checkpoint replay, production input acceptance,
collection, repair activation or publication is established by this document.

## Pins and phase boundaries

Runtime 003 is `/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source`, with source
receipt `60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6`.
The checkpoint is `/var/lib/swingset/checkpoints/h16-before-extension-20260917-003`,
manifest `22102b3cc07b07b49800c702396a748bbbe733e148bbca9b426bff370792223d`.
The coordinator's recorded private archive acknowledgment is
`da38556935ee18ee26a93e121ef461514ac2f423`. This helper makes no remote query and
does not independently renew that acknowledgment.

`prepare` verifies the entire checkpoint, copies all files independently,
recreates only the local baseline link, and migrates its database from schema 14
to 28. All predecessor tables must remain exact. It emits an immutable scratch
marker binding the checkpoint, runtime, helper, retained files, protected table
hashes and named-judge comparison population. It is a scratch preparation, not
the operational restore protocol.

`accept` requires the marker's exact SHA-256 and external `hosts.toml`,
`sources.toml` and complete CSV override inventory matching the frozen source.
It uses normal `capture` and `accept`; changed recipes invalidate their actual
dependent work. This acceptance applies only to the disposable database. A
separate receipt records the accepted bundle. No year or new-source approval is
created. A second acceptance phase is refused once its receipt exists.

`drain` checks the same accepted bundle and executes bounded ordinary work via
`next_offline`, normal control scopes and `derive_one`. It reconstructs at most
100 parse hints per turn through the ordinary recovery helper. Each selected
unit is attempted at most once per turn. Normal failures, retry deadlines,
admission policies and source pauses remain effective. Existing pending work is
not cleared to make the turn appear complete. The file hold remains installed;
the coordinator's explicit offline helper operates without starting ordinary
services. Database control pauses continue to constrain each unit normally.

Each phase verifies all retained files, including the published baseline. Every
mutation phase also verifies unchanged paid host usage, host timing/cache rows,
controls and predecessor execution admissions. New offline admissions are
allowed; new request admissions are forbidden by preservation checks. Original
admission policies remain exact; new ordinary shadow defaults are allowed, but
new enforcement or weakened policy is not. The helper compares every initially
named judge's identity, name and nullable WSDC number; differences stop acceptance
and remain reviewable in its failed receipt. It does not silently delete judges
without registry IDs.

## Invocation

Run the separately pinned helper with the reviewed runtime's Python environment
and `PYTHONDONTWRITEBYTECODE=1`. All phases require:

```text
<prepare|accept|drain>
--source <frozen-runtime-003>
--checkpoint <reviewed-checkpoint>
--scratch <new-disposable-state-outside-production>
--helper-sha256 <exact-reviewed-helper-hash>
--output <new-receipt-outside-state-and-input-roots>
```

Acceptance and drain also require `--marker-sha256`, `--config` and `--overrides`.
Drain accepts `--max-seconds` (60–3,600, default 600) and `--max-units` (1–10,000,
default 100). Use a new output receipt for every invocation. `--max-seconds` is
a selection budget, not a hard wall deadline. Selection stops with 50 seconds
reserved, but an already-selected worker may perform separate admission, body
and settlement transactions, each with the ordinary 45-second write deadline.
Parsing and other computation also lack a single helper-wide interruption timer.
Recovery and run-finalization work can add time too. Receipts report actual drain
elapsed time and any selection-budget overrun without claiming a total wall
bound. Checkpoint copying, source validation and complete retained-file
verification occur outside the selection budget; phase timestamps include them.

The retained 31,821 pending parses and conservative runtime invalidation make
multiple turns likely. Turn receipts report attempts, outcomes and pending queue
counts, explicitly excluding any claim that those counts cover complete
derivation history. `no_more_runnable_units_this_turn` can mean a pause, retry,
blocked input or the once-per-turn exclusion. It never means all work is current.
Independent complete accounting and subsequent release audit remain required.

## Offline checks

The [source-bound check](../../evidence/runtime/extension-input-rehearsal-2026-09-17/offline-check-001/checks.json)
passed 13 tests in 0.90 seconds against frozen runtime 003, plus Ruff and mypy.
The helper and test hashes stayed unchanged throughout that run. These are local
test receipts, not actual checkpoint replay or production acceptance.
Independent review identified the distinction between a selection budget and a
hard worker deadline. After making that limit explicit in receipts and this
document, [offline-check-002](../../evidence/runtime/extension-input-rehearsal-2026-09-17/offline-check-002/checks.json)
passed the same 13 tests in 0.90 seconds, Ruff and mypy against frozen runtime 003. It pins the corrected helper and tests; the earlier receipt remains intact.

Tests exercise a real schema-14 checkpoint copy and migration with an archived
body, real parsing and durable attempts, preserved hold/paid usage, source pause,
retained parser findings and unchanged-policy retry suppression. They also
exercise exact override inventory, unsafe destination rejection, linked or
changed evidence, original admission mutation and named null-ID judge reporting.
No fixture fetches or production commands are used.

An independent reviewer found no remaining blocker for this held, disposable
rehearsal after the selection-budget correction. The
[independent receipt](../../evidence/runtime/extension-input-rehearsal-2026-09-17/independent-review-001/checks.json)
pins the final helper and test bytes, verifies the imported runtime is frozen
003, and records 13 passing tests in 0.83 seconds plus Ruff. This review covers
source binding, full artifact copying, controls, paid-request preservation and
the absence of collection or publication paths. It does not establish actual
checkpoint replay or operational acceptance.

See [D-0065](../../decisions/0065-rehearse-frozen-inputs-in-bounded-offline-phases.md).
