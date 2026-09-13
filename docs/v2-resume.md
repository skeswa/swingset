# v2 resume handoff

Owner requested pause on 2026-09-13 UTC, then authorized only completion of
the paused H16 scratch replay. Replay and verification are now complete; work
is stopped at that boundary. Build, deployment,
production initialization, publication, acquisition and future package work
remain paused and require a new instruction.
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
The original pause accounting remains unchanged. No build state was created,
and production remains schema14 with zero materialized pointers and the same
public baseline. All six ordinary units remain inactive.

Stop there. Only after a new instruction, use `/var/tmp/h16-changelog-build-driver.py` with the exact source,
receipt, bundle and actual final replay receipt. Its defaults name an old pin,
so override all three values. Create the new independent build state
`/var/tmp/swingset-h16-changelog-build`; do not add baseline artifacts to the
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

The new production release driver is
`/var/tmp/h16-changelog-production-release-driver.py`, SHA
`0c78189606d69f9388e24620d8e8a781bc3730f1937b5c727cfa17ffd83a2bdd`.
Its reviewed guide is `/tmp/h16-changelog-production-release.md`.
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
