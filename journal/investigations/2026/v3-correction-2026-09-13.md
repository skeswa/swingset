# Publishing identity corrections (V3)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

V3 published at `4653f3a3a6076d3af474c28f7bd0e93998ca0a9c` on
2026-09-13 at 07:15:24 UTC, following the rehearsal against V1 dataset
`7cfcf4ec5dbc994d91f3e4d816f43b3abe16637b`. The publication boundary verified
current journal and input digests, the baseline and all candidate file hashes.
The remote commit and file hashes were verified before baseline promotion.

Production candidate: `cand_a9a87deb3ee34dac`. Manifest SHA-256:
`9009fb4141d26dff1a15e9e35c87587c3eb620f75e5af9dda5865a18d1538eec`.
The production build took 110.42 seconds and matched the rehearsal's identity
counts. Publication took 146.98 seconds. The receipt records 386.78 seconds
from correction detection to verified publication, including the build and audit.

The pinned source is `/nix/store/4fv8f8r3f1b5pl20vifqxvx8i76k96hp-source`.
NixOS generation is
`/nix/store/cvlimn0913b84ww5xc2nj1igkdvqmycx-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
Schema9 was live at publication; admission remained in shadow and all scheduled services were held.
The override file is the accepted empty journal; probable assertions cannot
populate default IDs. The correction-only path has now been exercised against
the live dataset with unrelated work still queued.

The schema7 checkpoint was cloned and migrated to schema9. Migration preserved
37,393 observations exactly, created no accepted source generations or identity
decisions, and passed foreign-key checks. Current inputs were then accepted in
the clone. The header-only override file became the empty decision journal.

Candidate: `cand_a8580553523d4b02`. Build time: 150.53 seconds. Manifest SHA-256:
`b5cf073615019ce3f5e64f1405071551d6c2c3e799fb6f17517dbdfd64bc15e4`.

| Check                                              | Result                                                             |
| -------------------------------------------------- | ------------------------------------------------------------------ |
| Default entry IDs                                  | 36,715 before; 34,959 after                                        |
| Default judge IDs                                  | Zero before and after; all 4,919 named judge records retained      |
| Default IDs added or replaced                      | Zero                                                               |
| Unsupported individual source references           | 82 joins withheld; all are EEPro mixed-person cells                |
| Printed ID unavailable in the subject's owned cell | 1,504 joins withheld                                               |
| Current decision resolver veto                     | 166 ambiguous locators and four source/registry conflicts withheld |
| Raw source facts and score rows                    | Unchanged in every structural table                                |
| Queued work                                        | All 32,780 units preserved exactly                                 |
| Unrelated parse findings                           | One failure and 26 warnings remain visible                         |
| Independent acceptance checks                      | All 24 pass                                                        |
| Candidate integrity audit                          | Pass                                                               |

The public identity assertions carry source-reference IDs, accepted decision IDs,
policy version, acceptance state and journal digest/generation. Published support
is explicitly legacy and unassessed until H7 checks it. It receives no admission
or removal authority. Default-join expansion remains withheld pending H17.

The final offline suite passed 535 tests, with Ruff and strict mypy clean.
The manual-link regressions pass: a manual assertion
requires a current positive decision, and a name or assignment method cannot
claim confirmed publication status. Final runtime checks and deployment are
complete: the production acceptance and integrity audits passed before the
verified publication recorded above. Admission enforcement belongs to V4;
scheduled services remain held during that bootstrap.

Receipts: [build](../../evidence/releases/v3/v3-rehearsal-20260913.json),
[acceptance](../../evidence/releases/v3/v3-acceptance-20260913.json),
[integrity](../../evidence/releases/v3/v3-integrity-20260913.json),
[production build](../../evidence/releases/v3/v3-production-build-20260913.json),
[production acceptance](../../evidence/releases/v3/v3-production-acceptance-20260913.json),
[production integrity](../../evidence/releases/v3/v3-production-integrity-20260913.json),
[verified publication](../../evidence/releases/v3/v3-production-publication-20260913.json).
