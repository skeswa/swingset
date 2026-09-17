# Reducing repeated release validation

Date: 2026-09-16 UTC  
Type: Outcome in progress  
Topic: H16 build completion  
Related decision: [D-0007](../../decisions/0007-bounded-closure-proof-reuse.md)

## Summary

Build completion now reuses a validated closure only after hashing its exact
database evidence again. All three validation boundaries and the 45-second
write limit remain. Local adversarial tests pass. A diagnostic copy completed
the whole transaction in 23.00 seconds; a new frozen source has been assembled
and its NixOS system built. Full frozen acceptance remains in progress.

This is implementation and diagnostic evidence. It does not establish a
published release or clear the production hold.

## Change and tests

The first validation reconstructs the dependency graph and source witnesses.
It keeps only identifiers and an evidence digest. Later validations reread the
relevant rows and dependency bytes. An unchanged digest avoids repeated JSON
decoding; any difference causes full validation again. Every call also checks
the supplied manifest's digest. Reuse is confined to one connection and one
completion operation.

The evidence includes unaccepted selected source generations, accepted-decision
existence, policies, snapshot body hashes, and global revocations. It also
includes raw derivation and dependency evidence, so direct BLOB writes cannot
hide behind SQL immutability triggers. Decoded payloads are not retained.

The focused suite passes 42 cases. New cases exercise later-boundary mutations,
savepoint rollback, another connection, changed manifests, late admission,
direct BLOB writes, and collection of decoded documents while the reuse scope
is still open. Actual build-completion tests prove that late rejection rolls
back generation rows and retained proof.

## Large retained-state diagnostics

Both diagnostics used the previous frozen H16 runtime with explicitly recorded
module overrides. They made no source requests or production changes.

| Measurement                                          | Result                        |
| ---------------------------------------------------- | ----------------------------- |
| First validation, including proof capture            | 12.62 seconds                 |
| Second validation                                    | 2.00 seconds                  |
| Third validation                                     | 1.98 seconds                  |
| Three validations combined                           | 16.60 seconds                 |
| Whole completion on an independent failed-build copy | 23.00 seconds                 |
| Completion diagnostic peak process RSS               | 2,125,176 KiB, about 2.03 GiB |

The [previous investigation](h16-validation-investigation-2026-09-15.md)
measured 34.78 seconds for three validations. These measurements were taken
separately, so they are not a controlled end-to-end speedup claim. An actual
new full build must establish performance after candidate generation.

## Frozen source

The source is `/nix/store/4c4q8rkiz6jqizcy93bid6cz8mfvp20b-source`, with receipt
SHA-256 `71dc14101ca19880f3addfa2dc0073dbe50bce0e4e2be27d7bc91811b6d1e338`.
The built system is
`/nix/store/nxvnvfrzdn4xj82im2hlj0pa42ck2zqn-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.

The assembly extends the old 2d5 release source with three runtime files,
the new tests, and pytest's explicit test import path. It retains schema 14,
configuration, overrides, and historical helper layout. The current worktree's
Monterey parser fix and schema 15 remain outside this original H16 release.
The assembly and Nix build are not deployment.

The working checkout passes 1,407 tests, Ruff, and mypy. A separate writable
mirror of the frozen source passes all 1,199 tests in 274.62 seconds, Ruff, and
mypy over 166 source files. Independent verification checked every source file,
the narrow overlay, and all three service bindings. All 636 receipt files in
the mirror still matched after testing; generated test caches were excluded.
The [frozen test gate](../../evidence/releases/h16-validation-2026-09-16/frozen-tests-gate.json)
binds those results. It permits the fresh scratch replay, not publication.

## Fresh scratch replay

The new replay began on 2026-09-16 at 14:24 UTC in
`/var/tmp/swingset-h16-proof-replay`, prepared from the original H15 checkpoint.
Its accepted bundle is
`410bd096dec912921c41702e1dc8fa5ef610385fd07afef52104561069eadcb8`;
the scratch marker hash is
`f2001ab21849adfd1b3bc46b1f19d189e40b284baa438d848f64b984a4a4db5f`.
The supervisor permits at most twenty 600-second invocations, stops on failure,
nonprogress, or its memory threshold, and performs no source requests.
It passed sixteen focused mock checks before launch.

Replay finished at 16:03:59 UTC after eleven invocations. All 34,986 scopes
are current: 32,445 projections and 2,541 links. Every attempt succeeded;
all 34,987 execution admissions settled. Active replay time was 97.70 minutes,
with 99.90 minutes elapsed for the supervisor. There were no source requests,
parsing, publication, or production changes.

Independent verification passed in 9.99 seconds. It acquired the exclusive
writer lock, found no pending scopes or foreign-key violations, passed SQLite
quick checking, and confirmed the seven inherited unfinished run records were
unchanged. The gate hash is
`a92b371276792734a89cdce4d76f961ae568661ff0fb434e38cab4735a9dc71b`.

The main database and WAL metadata were unchanged by verification. SQLite
rebuilt its shared-memory sidecar. The WAL remains nonempty; the build must use
the reviewed driver's ordinary read-only connection and SQLite backup API,
not immutable mode or a copy of the main database alone.

The coordinator reviewed the gate and authorized the separate scratch build.
Production deployment, initialization, and publication remain held.

## Full build and independent audit

The scratch build passed in 368.36 seconds, including 332.21 seconds in the build
function. Durable completion and semantic preflight passed under the unchanged
45-second write deadline. It selected 34,986 generations and 58,169 source
supports, with zero network requests or publication. Peak sampled anonymous
memory was about 4.40 GiB; the monitor did not terminate it.

Candidate: `cand_cbbeeadd90634cc9`. Manifest:
`cf0deef44cc7a71099fea643431b5f899e0e2f42b79f2ec8435074e4ed7c0c08`.
The direct `systemd-run --wait` exit was zero. The transient service disappeared
after success, so default status fields for a missing service are not used as
exit evidence.

The separately gated audit failed after 23.06 seconds. Fifty of 54 checks passed:
closure and durable completion, keys/references, emitted coverage recounts, all
17 years, 66 baseline year-coverage rows, and identity acceptance constraints.
Two judge-preservation checks and two registry-occurrence checks failed. The
identity comparison found 1,512 missing or changed named-null-ID judge records
whose source bindings persist; 3,418 were retained. These are failed comparisons,
not yet a diagnosis of why rows differ. Missing WSDC numbers do not authorize
deleting names. The occurrence checks require one event per retained occurrence
and matching registry placement references.

This establishes that the completion timeout is resolved on the retained full
build. It does not accept the candidate. All failed evidence and candidate files
remain intact.

## Preservation diagnosis and repair

Read-only diagnosis traced 208 overwritten registry occurrence identities to
mapping. A generated scoring-event ID could collide with an inventory-backed
event when date or status prevented the ordinary match. Mapping then replaced
the registry identity and metadata. The canonical history pass left 22,201
registry placement associations null before the build began.

Release reconstruction rejected 164 event alternatives even though selected
inventory generations contained admissible structural bases. Rejection of one
alternative removed the entire event and its supported judges. Of 1,513 missing
judge rows, 1,512 have retained source bindings; the extra row is the placeholder
name `---`. These defects predate the memory fix: the relevant frozen modules
match the previous source exactly.

The local repair preserves inventory-backed event collisions and permits an
admissible structural base to survive a rejected legacy event alternative.
Revoked support and unsupported result rows retain their withdrawal behavior.
The repaired source still needs frozen tests, ordinary accepted-runtime replay,
scratch build, and independent audit. See [D-0022](../../decisions/0022-preserve-supported-event-bases.md)
and the [diagnosis](../../evidence/releases/h16-validation-2026-09-16/audit-failure-diagnosis.md).

A full retained-state diagnostic applied only the new reconstruction module
to the old selected closure. It preserved all 4,931 baseline judges with zero
name or ID differences, retained 2,541 events, and omitted no events. It took
76.94 seconds with about 1.33 GiB peak sampled anonymous memory. Database
change counts were zero and retained database/WAL sizes and modification times
were unchanged. The 47 remaining occurrence mismatches and 22,201 registry
association mismatches still reflect the old mapping generations. This is
diagnostic evidence, not release acceptance. See the
[result](../../evidence/releases/h16-event-preservation-2026-09-16/diagnostic-result.json)
and [orchestration receipt](../../evidence/releases/h16-event-preservation-2026-09-16/diagnostic-orchestration.json).

The corrected schema-14 source is now frozen at
`/nix/store/z689qy41inndill3d92ym8im852x3649-source`; its receipt SHA-256 is
`0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb`.
Independent verification checked all 639 files, the exact eight-path delta,
and an identical host test mirror. Runtime changes are limited to mapping and
reconstruction. The event-completion extension remains excluded. The corrected
frozen suite passed all 1,333 tests in 295.77 seconds, including 129 build tests.
Ruff and mypy over 166 modules passed, and every frozen file still matched after
testing. The [frozen validation](../../evidence/releases/h16-event-preservation-2026-09-16/frozen-validation.json)
is bound to the [source verification](../../evidence/releases/h16-event-preservation-2026-09-16/source-verification.json).

The NixOS system built offline in 8.09 seconds at
`/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
It was not activated. Independent checks verified all three service source
bindings and unchanged active/persistent systems with all six ordinary units
inactive. The first pure evaluation attempt refused the existing host-config
import; the successful second attempt used `--impure` with `--offline` and
unchanged lock inputs. Both receipts are retained.

The coordinator rechecked the test logs, source/system receipts, and replay
script hashes before releasing the [scratch-only gate](../../evidence/releases/h16-event-preservation-2026-09-16/frozen-tests-gate.json).
Fresh replay runs in `/var/tmp/swingset-h16-event-preservation-replay`, from the
same original H15 checkpoint, with bundle
`fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a`.
Its captured marker is
`a2eb6255beddabe1917808da3916e219433fb9bf7fc85dd6427d9e28bad1a1e2`.
These checks do not establish completed replay or release acceptance.

The replay subsequently completed all 34,986 generations, including 2,541
link scopes. Independent final verification found no unfinished scopes, no
running attempts or unsettled admissions, and no database integrity errors.
It acquired the writer lock and made no database writes. The final verification
SHA-256 is `b05e18004e7a275d482205c840932efce39535b8f251002ade75553d4ddf4b1c`.

An active log pathname was replaced during replay. Its detailed sample gap
from 17:51:41 to 18:02:47 UTC remains disclosed. The original guard continued;
a VM-local observer retained the remaining samples. Independent lifetime
total memory peaks for all 13 preparation/worker units were below 6 GiB
(maximum 3,028,332,544 bytes), and every unit exited successfully. The
[coordinator review](../../evidence/releases/h16-event-preservation-2026-09-16/replay-coordinator-review.json)
accepted this evidence for the next scratch build. See
[D-0030](../../decisions/0030-retain-live-operation-evidence-outside-workspaces.md).
The new build in `/var/tmp/swingset-h16-event-preservation-build` then passed
in 441.81 seconds overall. Its independent audit passed all 54 checks,
resolving both preservation failures. The authorized production sequence is
recorded in [the production release outcome](h16-production-release-2026-09-16.md).
Production acceptance remains separate. The replay's
nonempty WAL must be included by normal SQLite backup, not a main-file copy.

## Test discovery correction

The earlier 1,199-test frozen run used default pytest discovery, which skipped
`tests/build`. Focused closure suites were run separately. These receipts remain
valid for their actual scope, but the combined count is not an exhaustive suite
claim. The next frozen validation must explicitly include build tests. The
working-source discovery setting is corrected in [D-0023](../../decisions/0023-include-build-tests-in-default-discovery.md).

## Evidence

- [Read-only measurements](../../evidence/releases/h16-validation-2026-09-16/readonly-proof-reuse.json) and [script](../../evidence/releases/h16-validation-2026-09-16/readonly-proof-reuse.py).
- [Completion diagnostic](../../evidence/releases/h16-validation-2026-09-16/completion-diagnostic.json) and [script](../../evidence/releases/h16-validation-2026-09-16/completion-diagnostic.py).
- [Assembly script](../../evidence/releases/h16-validation-2026-09-16/assemble-source.py) and [reviewed input hashes](../../evidence/releases/h16-validation-2026-09-16/reviewed-input-hashes.json).
- [Build and audit preparation](../../evidence/releases/h16-validation-2026-09-16/build-audit-preparation.md).
- [Independent frozen-source verification](../../evidence/releases/h16-validation-2026-09-16/frozen-source-and-mirror-verification.json).
- [Scratch replay preparation](../../evidence/releases/h16-validation-2026-09-16/replay-preparation.md).
- [Actual replay input binding](../../evidence/releases/h16-validation-2026-09-16/replay-input-binding.json).
- [Supervisor review](../../evidence/releases/h16-validation-2026-09-16/supervisor-review.json).
- [Completed replay supervisor](../../evidence/releases/h16-validation-2026-09-16/replay-supervisor.json).
- [Independent final replay verification](../../evidence/releases/h16-validation-2026-09-16/replay-final-verification.json).
- [Full build result](../../evidence/releases/h16-validation-2026-09-16/build-result.json), [orchestration](../../evidence/releases/h16-validation-2026-09-16/build-orchestration.json), and [resource samples](../../evidence/releases/h16-validation-2026-09-16/build-resources.json).
- [Failed independent audit](../../evidence/releases/h16-validation-2026-09-16/audit-result.json) and [audit orchestration](../../evidence/releases/h16-validation-2026-09-16/audit-orchestration.json).
- [Separate local check fixes](local-validation-checks-2026-09-16.md).
