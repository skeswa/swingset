# Identity review packet, 2026-09-13

A portable, unreviewed packet was prepared from settled local correction state
at `2026-09-13T05:24:48Z`. Read-only input:
`/var/tmp/swingset-v1-correction`, parsed, projected and linked with pinned
runtime `/nix/store/zl9b6h0w5qssiyixb9l10xszw2yg3yn3-source`. This is local
correction evidence, not a published-release precision claim. The subsequent
release baseline guard withheld 251 newly selected source-ID or
registry-placement links. The frozen sample was retained unchanged; it does not
claim to match those later release counts.

The population contains 95,178 subjects: 36,966 confirmed accepted entries and
58,212 unresolved entries, including entries without candidates. No confirmed
judge joins are present. The latest observed subject year is 2026; future
calendar listings without subjects do not define the era split.

The seed was declared before inspecting membership:
`swingset-v2-h17-20260913`. Cohort: `v2-local-correction-20260913`. Three subjects
per observed stratum, capped by stratum size, selected 275 subjects from 97
strata: 138 accepted and 137 unresolved. All 848 copied artifacts passed digest
verification and every local HTML evidence link resolves. No artifacts or
human adjudications are missing by implication: artifact count is verified;
human adjudication count is explicitly zero.

**Held-out evaluation is unavailable.** The conservative event/person/name
linkage graph contains one component. The fixed seed assigned it to tuning.
The packet remains useful for tuning review; it cannot establish held-out,
public, whole-population, or judge precision. Do not retry seeds to obtain a
preferred split. The earlier unreviewed packet was superseded solely to fix
latest-year classification against future listings; the seed did not change.

| Preparation step           | Seconds |
| -------------------------- | ------: |
| Read settled population    |    3.22 |
| Freeze and stratify sample |    6.44 |
| Copy and render evidence   |    1.78 |

The process finished before the next memory-heavy build. The pipeline and
publication state were not mutated, and no network requests were made.

Portable host packet:
`/tmp/swingset-v2-phase1-check/h17-review/index.html`.
Its README gives review/export instructions. Reviewers supply their name,
method, evidence references, decision and independently verified WSDC ID;
unresolved decisions require searching beyond the frozen candidate list.
Use `swingset.link.evaluation evaluate --split tuning` for downloaded reviews.
Missing or inconclusive reviews never become positive labels.

Sample digest:
`c10b47e24a491711e00cf652ad4cc880f6bc326c1418afe54725d378acc8f7a6`.
Machine timing and stratum denominators:
[h17-review-packet-20260913.json](verification/h17-review-packet-20260913.json).
Reproduce with [prepare_identity_review.py](prepare_identity_review.py), the
same settled state, seed, cohort, cutoff and `--per-stratum 3`. The packet's
`sample.json`, `provenance.json`, and input revision records remain the review
reference. No score policy or default join was expanded by evaluation tooling.
