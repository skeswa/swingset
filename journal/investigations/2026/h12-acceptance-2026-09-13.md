# Checking isolated work and retries (H12)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Work isolation lets one task fail without stopping unrelated tasks. This record concerns retries, saved artifacts, or migration checks. The original work ID is H12.

Implemented, deployed and accepted on 2026-09-13 UTC. Production uses the
reviewed schema11 source. H13 work remains in the working copy.
This revision does not activate repairs or accept historical years.

## Production acceptance

The pinned runtime is `/nix/store/0r3giz9x42hmwv448wzg8s2w1z47lwmc-source`.
Its source receipt SHA256 is
`ad1e0b8395b28559e6f4623459955b31574637503edddd994354520b384450ee`.
The NixOS switch selected
`/nix/store/dj8r9gkms681s26sz09flvb81v0f4iwi-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.

Before migration, the complete schema10 checkpoint was uploaded to private
commit `fe6047edb1cc6b23d7d0ef19f7cb5a077b8d0c66`. Its 65,419 files and
manifest `3323b2bede8fba5ebd30cb7a7062c9238695962a8bedd2bc1ce514acf649792c`
were verified locally. Remote repository privacy, head and immutable manifest
were verified at `2026-09-13T09:55:10.627353+00:00`.

The separately reviewed driver passed preflight, then migrated schema10 to11
and completed acceptance in 35.498 seconds. Every existing table remained
unchanged except schema metadata. The empty pending queue seeded zero work
generations and zero attempts. Doctor agreed in a fresh process at fixed time
`2026-09-13T09:58:23.871905+00:00`; human and JSON inventories agreed and
neither read changed state. Integrity and foreign-key checks passed.

The public V4 baseline stayed at
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`. Acceptance made zero source
requests and no publication. The persistent service hold remained present;
all three scheduled services and timers were stopped after activation.

Receipts: [execution](../../evidence/runtime/h12/h12-production-execution.json),
[preflight](../../evidence/runtime/h12/h12-production-preflight.json),
[gate](../../evidence/runtime/h12/h12-production-gate.json),
[backup](../../evidence/runtime/h12/h12-production-backup.json), and
[remote verification](../../evidence/runtime/h12/h12-production-private-verification.json).
Full doctor output is retained under
`/var/lib/swingset/operations/h12-20260913/` on the writer.

## Durable outcomes and independent work

Migration11 adds durable work attempts and queue generations. Outcomes retain
the exact input fingerprint, queue token, run, reason, evidence and retry
deadline. A deterministic failure or unchanged supersession does not become
eligible because another cycle starts or the same bytes are re-enqueued.
Changed inputs or an explicit operator retry can reopen it. Transient and
interrupted attempts wait for a recorded deadline. Output, downstream work and
successful completion commit atomically; a newer queue generation fences an
older completion.

The ordinary cycle runs independent derivations after a unit failure and
permits healthy acquisition when only ineligible failed work remains. A real
calendar scenario runs three fake-clock cycles with a permanently failing
extractor, a healthy fetch and an independent healthy projection. Both healthy
paths commit; the failed extractor runs once, and its body, attempt and unmet
requirement remain. No correction-only release is used.

The crash harness terminates before and after every cycle transaction. Restart
retains the interruption and its 60-second deadline; at most three additional
fake-clock recovery cycles reach the same committed canonical belief as the
uninterrupted run. SQLite integrity and foreign keys remain valid, work drains,
and SIGTERM recovery passes.

## Exact artifact recovery

The cycle shares a lazy local checkpoint recovery helper. Healthy reads do not
enumerate backups. A missing or corrupt artifact can be restored only after
checkpoint closure, database integrity and schema verification, physical file
size/hash checks, and verification of the requested decoded historical digest.
Each restore rechecks its selected artifact. Replacement source bytes cannot
stand in for the missing historical evidence.

Real cycle tests restore a missing retained body and commit its interpretation.
Without a valid matching checkpoint, the unit records `unavailable`, retains
the required digest and evidence, and allows an independent healthy projection
to commit. Merely restoring a file does not claim the work output succeeded.
Explicit `reparse` releases only the selected failed units' retry gates.

Backup closure now includes body and extract references in every retained
source-generation manifest, including blocked and staged generations whose
extract is absent from the snapshot's selected pointer. Backup verification
opens SQLite immutably. The [V4 checkpoint audit](h12-generation-closure-2026-09-13.md)
found zero omissions across 61,012 artifacts and 32,189 generations.

## Validation and limits

The integrated suite passed 759 tests in 62.61 seconds. Ruff and strict mypy
passed for all 139 runtime modules. A subsequent 41-test selection passed the
new generation-closure cases with artifact, cycle and inventory checks. These
are offline tests, not production execution or an acquisition measurement.

Durable work attempts appear in doctor, including a first running attempt
before any finding exists. Failed work requirements survive scanning and can
be reconstructed after an inventory-row loss. Attempt counts do not count as
verified progress.

H13 owns pause semantics and control servicing. H14 owns fair budget allocation.
H15/H16 own desired/materialized dependency generations and release closure.
Ordinary build still requires the retained pending queue to drain; H12's
healthy-work scenario establishes committed derivation and acquisition, not
publication through an unresolved dependency.

Tests: `test_work_isolation.py`, `test_work_attempts.py`,
`test_work_attempt_inventory.py`, `test_artifact_recovery.py`,
`test_crash_recovery.py` and the checkpoint tests.
