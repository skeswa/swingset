# H17 assistive proposal index

All 275 frozen subjects have model-assisted annotations: 138 in the accepted stream and 137 unresolved. **Human review remains outstanding.** The accepted label describes the original pipeline stream, not human acceptance of these proposals.

Use the [original review packet](/tmp/swingset-v2-phase1-check/h17-review/index.html) to inspect the source and candidate evidence. Each proposal is optional assistance: a human can disagree, choose another candidate, or abstain. Record human decisions through the existing review workflow, not by treating this index as gold.

There are zero human adjudications, zero gold labels, and no held-out precision estimate. The frozen connected component belongs to tuning. These annotations neither expand default joins nor establish current-public errors. They cover the pre-release local population at the cutoff below.

## Cohorts

Offsets are zero-based within each stream sorted by `sample_id`; ranges below are inclusive. The machine manifest records exclusive stop offsets.

| Stream | Offsets | Samples | Proposals | Abstentions | Sidecar and notes |
| --- | --- | --- | --- | --- | --- |
| accepted | 000–019 | 20 | 13 | 7 | [proposals.json](proposals.json) · [notes](README.md) |
| accepted | 020–059 | 40 | 26 | 14 | [proposals-accepted-020-059.json](proposals-accepted-020-059.json) · [notes](README-accepted-020-059.md) |
| accepted | 060–089 | 30 | 20 | 10 | [proposals-accepted-060-089.json](proposals-accepted-060-089.json) · [notes](README-accepted-060-089.md) |
| accepted | 090–119 | 30 | 22 | 8 | [proposals-accepted-090-119.json](proposals-accepted-090-119.json) · [notes](README-accepted-090-119.md) |
| accepted | 120–137 | 18 | 13 | 5 | [proposals-accepted-120-137.json](proposals-accepted-120-137.json) · [notes](README-accepted-120-137.md) |
| unresolved | 000–029 | 30 | 6 | 24 | [proposals-unresolved-000-029.json](proposals-unresolved-000-029.json) · [notes](README-unresolved-000-029.md) |
| unresolved | 030–069 | 40 | 7 | 33 | [proposals-unresolved-030-069.json](proposals-unresolved-030-069.json) · [notes](README-unresolved-030-069.md) |
| unresolved | 070–109 | 40 | 8 | 32 | [proposals-unresolved-070-109.json](proposals-unresolved-070-109.json) · [notes](README-unresolved-070-109.md) |
| unresolved | 110–136 | 27 | 6 | 21 | [proposals-unresolved-110-136.json](proposals-unresolved-110-136.json) · [notes](README-unresolved-110-136.md) |

## Evidence gaps and verification

- Accepted: 94 proposals, 44 abstentions; 0 subjects have no candidate evidence; 0 listed candidate snapshots are missing.
- Unresolved: 27 proposals, 110 abstentions; 68 subjects have no candidate evidence; 0 listed candidate snapshots are missing.

Candidate counts come directly from the frozen packet. They do not measure search completeness or prove that an account is absent. Masked rows remain masked, and composite or ambiguous names require ownership review before any individual ID assignment. Same-month results at another event, opposite-role placements, and a partner’s ID are not interchangeable evidence.

The index verified exact membership and fingerprints for all 275 subjects, with no duplicate or omitted samples. It reverified 848 distinct referenced artifacts: raw-byte hashes for bodies and canonical JSON hashes for extracts, with portable-file hashes recorded separately. Earlier sidecars and original packet files remain unchanged. No source requests or identity writes occurred.

## File hashes

| File | SHA256 |
| --- | --- |
| [README-accepted-020-059.md](README-accepted-020-059.md) | `4dc75105fa073305b19763e08a2bfd3dd43c85e5b70a8a3ecea78b792794756a` |
| [README-accepted-060-089.md](README-accepted-060-089.md) | `3918cbbcb6404fcc113a66f1236e875d547e30930e4c63fd2b89b8ad9a4e0a90` |
| [README-accepted-090-119.md](README-accepted-090-119.md) | `cf3ffb4eaca7f70f5ccc0a95f92a0c99277f2978bf553902b7126c5ae4011dc5` |
| [README-accepted-120-137.md](README-accepted-120-137.md) | `34833e1eeea4781aec54162ca9bd4cd06e387a85ac621c09821e063e61873d6d` |
| [README-unresolved-000-029.md](README-unresolved-000-029.md) | `b087c6803229245d3a77f31295214b8dfa9d273759cc84c15c6a883863a0a1f8` |
| [README-unresolved-030-069.md](README-unresolved-030-069.md) | `84b786643109533ae449c56f7141aa4daad5bb843cc15040706c8712d167ec56` |
| [README-unresolved-070-109.md](README-unresolved-070-109.md) | `26b9576b39687c109487b22938fc48ef3b656cddb003522d77e69791396fd821` |
| [README-unresolved-110-136.md](README-unresolved-110-136.md) | `82e339d67039e70eb33d232c2048e55c417d36d848e99bf2e0940701a305dde1` |
| [README.md](README.md) | `30834bff5f3e42bdc09ba2f06d9c5c68be2f7d1fa88857433f40844d0b43e6a0` |
| [peter-current-public-check.json](peter-current-public-check.json) | `09ca4f1244d70682f9023708644aec6a1d9e94175e55d50d5922f40d4edc4579` |
| [proposals-accepted-020-059.json](proposals-accepted-020-059.json) | `a5ec3d8839f978e83c241707e51beb5acb0a66d1d8c27dae4519d931a6291ebd` |
| [proposals-accepted-060-089.json](proposals-accepted-060-089.json) | `13e15bbed816a6ffb07cc0b607dfe2455d63a44f683c508dc891166ed8cae322` |
| [proposals-accepted-090-119.json](proposals-accepted-090-119.json) | `ed72d8d7894b4a6a2bec343f8d331ca48740cefb233534d61419202089ffce77` |
| [proposals-accepted-120-137.json](proposals-accepted-120-137.json) | `efefeada0c5dcda7a6a591c584229e278d86f41cebf64abaf5c308a34dd783d3` |
| [proposals-unresolved-000-029.json](proposals-unresolved-000-029.json) | `f0d08a47e2f294de19366b0cf93fc0490c0df091c1856734e6ae4821fb09fc7f` |
| [proposals-unresolved-030-069.json](proposals-unresolved-030-069.json) | `f7a43574a85662d2b1337092b827d289447934a0b0431dd8890cc9cf920bc16b` |
| [proposals-unresolved-070-109.json](proposals-unresolved-070-109.json) | `df631d55fb60ec9538e476a76c849d9ea65e50e28448d7cc87fae4233ff90fe6` |
| [proposals-unresolved-110-136.json](proposals-unresolved-110-136.json) | `138b84fa4e35849029600b9be30fb7de1892b0eaf1cae9a9747492aea90e58a3` |
| [proposals.json](proposals.json) | `57cf2f6da50c9d6e1035a109542395f96cbd9e45868699a250fc7f236a5b8d2c` |

## Frozen packet provenance

- Sample file SHA256: `51fe53d52600b3cf1aa3a5ae34799d6e84d5243af17de258acfc959654b64836`.
- Provenance file SHA256: `16901007c15d2e97f861f3e74eb1d7b3a29b94f81924a3a9bfba9eef3049a450`.
- Sample digest: `c10b47e24a491711e00cf652ad4cc880f6bc326c1418afe54725d378acc8f7a6`.
- Population digest: `9b060d227bcb33e1068c887d26cd83bfef7999b1abca8c9433b089c2abef9488`.
- Seed: `swingset-v2-h17-20260913`.
- Cohort: `v2-local-correction-20260913`.
- Evidence cutoff: `2026-09-13T05:24:48Z`.
- Original runtime: `/nix/store/zl9b6h0w5qssiyixb9l10xszw2yg3yn3-source`.
- Original local state: `/var/tmp/swingset-v1-correction`.

[manifest.json](manifest.json) contains the exact per-sample file/JSON-pointer index, cohort counts, all file hashes, original provenance, and verified artifact inventory. This index is a separate discovery aid; it does not alter the original packet or review UI.
