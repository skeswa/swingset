# V2 and V4 publication

Published on 2026-09-13 at 08:47:00 UTC, commit
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`, following V3 commit
`4653f3a3a6076d3af474c28f7bd0e93998ca0a9c`.
Candidate: `cand_7f8cf9bcbf7e4a60`. Manifest SHA-256:
`87ac286a1ae452f09f7e5cea646f260ac34035f3d05bb83de17c7d9cc8f08884`.

## Acceptance

All 57 independent acceptance checks and the separate integrity audit pass.
The publication driver checked the current inputs, policy, acknowledged parent,
candidate hashes and remote parent before publishing. The normal settled build
had no pending parse, projection or linking work.

- All 1,888 retained registry occurrences since 2010 have exactly one event.
  The release has 2,541 events and 66 source/year coverage rows. Every required
  year, 2010–2026, has coverage and an open finding explaining why its event
  list is not yet accepted. No year was accepted by this replay.
- Nine reviewed page kinds are enforced: eight contract4 kinds and EEPro
  autoindex contract5. Accepted pointers resolve passing generations. All 323
  current guarded units have public reasons, and failed legacy observations
  remain unchanged.
- The exact 67 reviewed unsupported contests remain visible, with their
  unsupported results withheld: 68 rounds, 1,358 entries, 930 placements and
  683 final marks. No unreviewed contest changed to unsupported.
- All 4,919 previously published named judges survive. Twelve additional
  printed judge names are recovered. All 4,931 judge IDs remain null.
- Every one of the 34,955 default dancer joins has independently checked
  retained support. No new or replacement default identity join is published.
- The finite phase1 import contains 192 snapshots. Its 204 retained bodies and
  161 extracts have closed artifact references. The catalog still names 17
  pending captures. No existing acquisition control changed; seven newly
  discovered round intents remain visible findings without watches.

Four previously published dancer IDs are newly withheld on retained entries.
Their printed names and source cells are unchanged. The full replay detected
different people sharing a fallback row-based source reference in French Open
2026 pro-am follower results. The resolver requires reference review rather
than choosing between those semantic identities. The
[continuity receipt](verification/v4-default-id-continuity-20260913.json)
records all four before/after traces. These are separate from unsupported
contest withdrawals; no reference migration was approved.

## Runtime and memory

Deployed source:
`/nix/store/acbgadnny89l7kp459im2lx6bp2ll39q-source`.
NixOS generation:
`/nix/store/7mvwc6cy25p352jxrrpdqy1acc7y47xb-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
Private schema 9, projector 19. The source inventory contains 466 files,
SHA-256 `5e7529a577dc154140fc0701b91a8d3116b280076d20fe02e52b479fb03a3447`.

The first build was killed by the VM memory cgroup after all derivations
committed. The fix removes redundant table copies, retains copying at mutation
boundaries, and compares one baseline table at a time. Tests compare the exact
changelog with the captured previous implementation and verify nested inputs
remain unchanged. All 627 tests then passed; Ruff and strict mypy passed.

The resumed build reused the completed 31,613 parse, 29,695 projection and
2,541 link units. It completed in 146.33 seconds. Its highest sampled resident
memory was 6,234,816 KiB, about 5.95 GiB. This is a sampled measurement, not an
upper bound; headroom remains limited as history grows. The original failed
attempt and both predecessor receipts are retained.

The publication receipt also retains the original journal detection timestamp
from V3 and a 5,882-second correction latency. That interval is not a measurement
of this V4 publication alone. H16's publication-progress accounting must retain
the distinction; the immutable receipt has not been rewritten.

## Verified private backup

The 64,907-file checkpoint passed complete file verification and uploaded at
`2026-09-13T09:06:01.655424+00:00`, private commit
`2dd1c31510aec7ce10d752403cf5901f8a375636`. Its manifest SHA-256 is
`28b08a723746f40490aa071951fb4071da9f96f29ede2d282a5cefc189a835cf`.
The [backup receipt](verification/v4-production-backup-20260913.json) retains
the checkpoint path and verification result. The upload service exited successfully.

## Remaining gates

V2 and V4 are complete. V5 infrastructure and V6 may proceed. Historical sheet
acquisition still requires owner year acceptance and enforcement for the exact
page kind. H17 human adjudication remains pending; this release establishes no
measured identity precision and permits no default-join expansion. No repair
kind is activated. Scheduled workers remain held while V6 proceeds.

Receipts: [acceptance](verification/v4-production-acceptance-20260913.json),
[integrity](verification/v4-production-integrity-20260913.json),
[build](verification/v4-production-build-20260913.json),
[publication](verification/v4-production-publication-20260913.json),
[engineering review](verification/v4-production-review-20260913.json),
[memory samples](verification/v4-build-memory-20260913.json).
