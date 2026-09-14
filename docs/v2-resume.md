# v2 resume handoff

Owner paused production steps after the current scratch build on 2026-09-15
UTC, intending to resume later with a stronger network connection. The new
frozen scratch replay completed and passed independent verification. Its full
build failed again at the 45-second final completion transaction. Evidence and
state are preserved; no further build, deployment or production action is
running. Wait for the owner to resume before continuing release work.
No commit or push was requested or performed.

## Completed and current

V1–V4 are published. H11–H15 are deployed. H16 is deployed, but its full
production initialization and release publication remain outstanding.
All scheduled cycle, backup and summary services/timers remain held by
`/var/lib/swingset/operator-hold`.

The public baseline is commit `81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`,
candidate `cand_7f8cf9bcbf7e4a60`, manifest
`87ac286a1ae452f09f7e5cea646f260ac34035f3d05bb83de17c7d9cc8f08884`.
Production remains schema14 with zero materialized derivation pointers.
Scratch progress is not production progress.

The active and persistent NixOS system is
`/nix/store/r29kapzbjk0ch5h0vkk288jd47dvg90j-nixos-system-swingset-lxc-25.11.20260630.b6018f8`,
source `/nix/store/dihx01r68nq8nqxzm1dlgrzl5qrjw583-source`.
The persistent profile was repaired to match the already-active system;
no activation or state migration occurred during this status-update turn.

## H16 replay and next build

The old ownership-fix replay completed all 34,986 scopes, but its build was
stopped above 6 GiB anonymous memory before tables were written. Preserve
`/var/tmp/swingset-h16-event-map-replay` and its separate failed build state.

The bounded-changelog fix passes 1,199 frozen tests. Its source is
`/nix/store/093lylp4naslq5yb2mygbkkgkbsar3ym-source`, source receipt
`3930dbc9f6023250a5253043ade60bd3fc90c57bda27e2c64f8520aa4c588a92`,
NixOS system
`/nix/store/p968fhdgrjdjfkqd96a8vyrzz8ic2jqw-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
It is not deployed. Its read-only production preflight passed.

Its scratch replay is `/var/tmp/swingset-h16-changelog-replay`, bundle
`9370ccc35058661037c78b2dbcea797932dce1e4211638826149661a4e37e683`,
marker SHA `f1e869c0844943b28c0d6f58e8af1b601e72c244984d998bcce1c72ca5821591`.
The owner resumed only this replay after invocation 006. Invocations 007–012
completed the remaining 1,418 scopes in 50.57 minutes of invocation time.
The replay is now current: 34,986 committed scopes, comprising 32,445 project
and 2,541 link. No attempts failed or remain running. Independent read-only
verification found no unfinished project/link work or foreign-key violations;
all replay runs finished, no replay process remains, and the writer lock was
acquired and released. Seven historical unfinished run records match the
predecessor checkpoint exactly and were preserved.

The final receipt is `/var/tmp/swingset-h16-changelog-replay-012.json`, SHA
`279a4662066594d5aa5a80dcc6c9de8daa03863c38d6b8ea4dcacf202b5265c1`.
Complete receipt-chain accounting is
`research/verification/h16-changelog-replay-summary-20260913.json`.
Independent verification is
`research/verification/h16-changelog-replay-final-verification-20260913.json`, SHA
`f2ba01d5e2d0355fd9a9780d6264e47e9e4b6f3341de32250cc66ddb5b35f349`.
The original pause accounting remains unchanged. Production remains schema14
with zero materialized pointers and the same
public baseline. All six ordinary units remain inactive.

On 2026-09-14, the 093 full build created candidate
`cand_d80b9025652f4e10` in `/var/tmp/swingset-h16-changelog-build` but failed
durable completion. Preserve that state and its failed receipt; a `BUILT`
file alone does not establish accepted build completion. The process reported
5,562,844 KiB maximum RSS; the monitor did not terminate it. The journal
identifies repeated closure validation inside the 45-second completion
transaction. See `research/verification/h16-changelog-build-20260914.json`
and its resource/journal evidence. Production remains unchanged.

The corrected release source is
`/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source`, receipt
`f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f`,
system
`/nix/store/86p1plylmwlna3ympba52g26pwvmjv0i-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
Independent verification checked all 633 frozen files and service bindings.
All 1,199 frozen tests passed in 294.78 seconds; Ruff and mypy165 passed.
The final working-copy suite passed 1,400 tests, but that evolving tree is not
the release source. Evidence is
`research/verification/h16-closure-frozen-runtime-validation-20260914.json`.

The new scratch is `/var/tmp/swingset-h16-closure-replay`, marker SHA
`d3c8385032c6ba66baea1afffe53bec5ad5bd1dacb269a5b56f8a3c2eeeb0377`.
Replay 001 began on 2026-09-14 at 21:52:47 UTC, using the frozen configuration
and overrides. Its actual accepted bundle is
`558bb5f4e88b47fe80a691254d3b21ac9e491296e80bb18f4599b64a88bfe9c0`.
The history agent owns replay writes and captures each finished receipt under
`research/verification/h16-closure-replay-NNN-20260914.json`.
Invocations 001–011 have exited successfully. The replay is current with
34,986 successful scopes: all 32,445 projection scopes and 2,541 event-link
scopes. No failed or abandoned attempts were recorded. The final receipt is
`h16-closure-replay-011-20260914.json`, SHA
`53d7e8006bd492e6fcae2ed3d4f888d7ea3ae716f3375bb7188a43287a724bd5`.
Its unfinished inventory is empty. Total invocation time was 5,956.95 seconds
(99.28 minutes); orchestration interruptions added wall-clock gaps. The finite
local supervisor stopped without launching 012. Independent read-only final
verification passed in 10.45 seconds: no unfinished work, all attempts
successful, all admissions settled, no foreign-key violations, quick-check
passed and writer lock released. Its receipt is
`research/verification/h16-closure-replay-final-verification-20260914.json`, SHA
`44319b29c4a9345ea91f16bd84f2799d4316b6935e19a1d379b1c480347e6c21`.
A diagnostic copy completed the build transaction in 38.82 seconds, but it
is not release acceptance. The new replay and its actual full build/audit
must pass before any deployment. Preserve all older replay/build specimens.

The actual 2d5 full build then failed on 2026-09-15 UTC after 387.66 seconds.
State `/var/tmp/swingset-h16-closure-build` and candidate files
`cand_95d350f1e29a4e19` must be preserved. The final `_certify` call reached
closure support reconstruction and exceeded the ordinary 45-second write
bound. The completion transaction rolled back. This is not an accepted build;
no substantive candidate audit ran and the candidate is not publishable.
Process maximum RSS was 4,831,260 KiB (about 4.61 GiB); the monitor did not
terminate it. The earlier isolated 38.82-second diagnostic was insufficient
for the full-build conditions. Do not treat a stronger network connection as
a fix for this local validation deadline.

Exact evidence:

- `research/verification/h16-closure-build-20260915.json`, SHA
  `c56a4009b28fa41fd8f2ba847ebfc3cdd9ada0f42e54c5f13f5082973d5043e5`.
- `research/verification/h16-closure-build-resources-20260915.json`, SHA
  `15ac6339e916c53a8eaf7db9051e7b8ffb8542ec078629c4835e4bf6722f109a`.
- `research/verification/h16-closure-build-journal-20260915.log`, SHA
  `1653363380b28019b6f4af959e186fb9eef5b96d62b245b4d137d3419d966e09`.

Independent failure inspection confirmed candidate manifest
`7ce49736b44da6e0afc007a2da9d8692008f29daff9a3e06ee65926eb5991168`,
no committed build generation/artifact output and no `PUBLISHED` marker.
All 34,986 project/link scopes remain current; no worker or unsettled admission
remains and the writer lock is free. See
`research/verification/h16-closure-build-failure-verification-20260915.json`.
Its separate sidecar addendum preserves the strict filesystem check that
noticed an empty WAL created by the read-only SQLite connection. Main database
metadata was unchanged; no sidecar was deleted to alter the evidence.
The final pause receipt is `research/verification/h16-closure-pause-20260915.json`,
SHA `8c4fa20510b768885714be5ba6601f687f104a407028ac27b4f95609e3583337`.

On resume, address completion performance under full-build conditions before
another release attempt. Any changed runtime needs its own frozen acceptance
chain. Preserve the completed replay and both failed builds as evidence.
Do not extend the transaction deadline or claim diagnostic completion as
release acceptance.

The current working copy also includes pending Monterey parser and alias
changes from a separate investigation. They are not part of this original-scope
H16 release. The new 2d5 source extends the reviewed 093 package with only
the validated performance fix and uses its frozen override files during input
capture. The Monterey acceptance and later event-completion extension remain
separate outstanding work; preserve those working-copy changes.

The resumed release uses `/var/tmp/h16-changelog-build-driver.py` with the exact source,
receipt, bundle and actual final replay receipt. Its defaults name an old pin,
so override all three values. The failed independent build state
`/var/tmp/swingset-h16-changelog-build` must be preserved; choose a new state
after the next frozen replay. Do not add baseline artifacts to the
replay. Run `/var/tmp/h16-changelog-build-monitor.py` with the build unit.
The VM cap is 8 GiB; stop cleanly above 6 GiB anonymous memory. Do not overlap
other heavy VM jobs. Audit the resulting actual candidate serially with
`/var/tmp/h16-changelog-audit-driver.py`.

Only after that build and independent substantive audit pass: set the
persistent Nix profile and activate the reviewed H16 system, immediately
stop all six ordinary services/timers, execute the exact production acceptance,
and fill the initializer gate from actual receipts. The initializer is
`/tmp/h16-live-initialize.py`, captured as
`research/verification/h16-initialization-driver-20260913.py`.
Its operations guide is `/tmp/h16-live-initialize.md`; examples naming old
pins must be replaced with the actual reviewed values above.

The previous production release driver is
`/var/tmp/h16-changelog-production-release-driver.py`, SHA
`0c78189606d69f9388e24620d8e8a781bc3730f1937b5c727cfa17ffd83a2bdd`.
It names 093 and must not run for 2d5. The reviewed new driver is
`/tmp/h16-closure-production-release.py`, SHA
`9da822db06cd1fcb4d0119706b7c91bc6e128e1447414b817f6b91ff703a51ca`;
its 34 guards passed against the frozen mirror. The new deployment driver is
`/tmp/h16-closure-switch-20260914.py`, SHA
`94ceacce56d3f24d2fadbc0a3d9d59cee0acd6fd32537e61e113441aeada7985`;
its four mocked activation/hold tests passed. Both target
`/var/lib/swingset/operations/h16-closure-release-20260914` and remain
unexecuted. Their exact files have been staged in the private operation
directory with mode0600 and service ownership; no production preflight or
activation ran. Exact scripts, tests and coordinator review are retained under
`research/verification/h16-closure-*20260914*`.

The copied operational gate/evidence are prepared locally at
`/tmp/h16-closure-operation-preparation-20260914`; gate SHA
`2ed8c721a07efa12cca325e8f3a048417f82b5c72f4197370fe8bfd52d731d7a`.
This records the retained backup verification and new source, without claiming
a fresh remote check or a passed production preflight. The exact gate/helper
and nested evidence were staged privately; the staging receipt is
`research/verification/h16-closure-operation-staging-20260915.json`.

The reviewed production initializer supervisor is
`/tmp/h16-initializer-supervisor-v2.py`, SHA
`2f6c6f88604db55342bc9d3adbfee9b789143846815cc6380768d26ebb952cae`,
also staged as `supervise-initialization.py` in the private operation directory.
All 44 mocked boundary tests passed, including independent interruption review.
It is unexecuted and requires an actual initialization gate/marker. Its v1
predecessor remains retained as preparation evidence, not the executable choice.
No initialization gate or marker exists for this new production release.
It requires actual production initialization, build and independent audit.
Publication credentials come only from the existing private service environment.

The verified predecessor checkpoint remains
`/var/lib/swingset/checkpoints/h15-before-h16-20260913`, private commit
`c656e88c775b23ae5879661924d57fa1f93cfa7d`.
After actual H16 publication, create and remotely verify a new private
checkpoint before schema15 migration. The old H15 checkpoint cannot stand in
for that new prerequisite.

## Future history and H17 source

The complete history source
`/nix/store/sfsm1sjnbzjmchv52hyqsx6x4kr2r5cd-source` passed all 1,396 frozen
tests, Ruff, mypy173 and Nix build. All 652 files and the 18 fixture adapter
checks were independently verified. It is not deployed.
See `research/verification/wp16-frozen-runtime-validation-20260913.json`.

A subsequent H17 fix makes new sampling consult authoritative unfinished
parse/project/link work, including missing enqueue hints. Its three source/test
changes pass 25 focused cases, independent review, all 1,399 integrated tests,
Ruff and mypy173. Existing 275-subject review evidence is unchanged.
See `research/verification/h17-currentness-worktree-validation-20260913.json`.

That revision is assembled at `/var/tmp/swingset-wp16-h17-release-source`
with 654 files, receipt
`72a5f0e1d1aa8cca638a522ebb3f0bc2f0ff1ddbc4906e90bba64cacddf4d218`.
It has not been frozen into the Nix store, Nix-built or tested as a frozen
mirror. Those are the next package-preparation steps after resumption.
The reviewed plan is `/tmp/wp16-h17-reviewed-assembly-plan.json`, SHA
`6ed8945c236d91a55b65110b8695cd458e9984dd7dc06859f4689f4318cc27e5`.
Exact preparation files and pause accounting are captured under
`research/verification/wp16-h17-*20260913.*`.

The runtime enables no new origin/query acquisition or repairs. DCN and
generic score-sheet parsers still need real retained fixtures. H18/V7 must
wait for V5 and V6 completion, as the accepted plan requires.

## Existing external gates

The owner approved H3 judge acceptance. Named judges without WSDC numbers
remain valid with null IDs; do not ask for that approval again.
The owner also reported reading the Archive terms; that is recorded already.

Human H17 adjudication and alias/year review exports remain pending.
The original H17 packet is `/tmp/swingset-v2-phase1-check/h17-review`;
all 275 subjects have independently audited model notes under
`research/workflow-output/h17-proposals-20260913`. Model notes are not human
adjudications or held-out gold. The alias review is
`research/workflow-output/v2-phase1/alias-review.html`; zero of 17 years is
accepted for phase2 acquisition.

The fixture exception proposal is
`research/v2-new-source-fixture-proposal-2026-09-13.md`; no approval or actual
fixture request has occurred. The reviewed H13 adapter remains prepared only.

The Archive daily budget is exhausted for 2026-09-13 UTC. The next window is
2026-09-14 00:00 UTC (September 13, 18:00 Denver). The 17 remaining phase1
calendar captures have a reviewed bounded resume driver, but must not run
while this user-requested pause remains in effect. No phase2 watches,
query executor, source kinds, years or repairs were activated during this turn.
