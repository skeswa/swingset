# Reading JesAnn Nail’s full recorded participation

Date: 2026-09-17  
Type: Outcome  
Topic: Dataset exploration  
Related decision: [D-0064](../../decisions/0064-present-all-participation-with-source-overlap.md)

## Result

A standalone local page now presents all recorded competition participation
for JesAnn Nail, WSDC #7849. It includes prelims, semifinals, Strictly, and
Pro-Am, with filters, individual scoring details, and filtered CSV exports.
It has not been deployed or published as a website.

The pinned dataset is public commit
`2a6c7dc744fb36eabb5163c0a527d787d3721f4f`. All 33 copied Parquet files matched
the release manifest before extraction. The page uses 85 registry records,
34 name-matched score-sheet entries, and six name-matched judging appearances.

Nineteen corresponding registry/sheet records are grouped for display. The
result is 100 contest-and-role appearances: 15 detailed-result-only, 19 with
both sources, and 66 registry-only. Thus 34% have detailed result data and
66% have only a registry summary. The 85% supported by the registry overlaps
with the 34%; adding those percentages double-counts 19 records. Judging is
separate. These counts include unconfirmed name matches and do not describe
all real-world appearances.

The internal [Jes Test](../../tools/quality/jes_test/README.md), accepted in
[D-0083](../../decisions/0083-benchmark-jesann-registry-coverage.md), measures
registry coverage independently of placements. Against the same pinned
release, 19/85 registry entries (22.4%) have corresponding individual result
evidence; all 19 have final placements and judge marks. The 19 assessable final
placements agree with the registry. The agreement denominator is limited to
these 19 entries. The page uses this correspondence to group participation,
so a placement disagreement remains visible as one appearance. Future release
reports can compare against this retained baseline to canary coverage progress,
regressions, new disagreements, and changed registry claims.

## Validation

The Jes Test panel showed 19/85 covered entries (22.4%) and 100% agreement
among the 19 assessable final placements. A same-release canary comparison
found 85 shared registry entries and no coverage, agreement, or claim changes.
The all-participation list remained at 100 appearances, and the page had no
horizontal overflow at 320 pixels or browser console errors.

Chrome browser checks passed for default participation, source percentages,
source overlap, four preliminary/semifinal entries, partner search, combined
filters, chronological sorting, empty-state recovery, expansions, and CSV
provenance. All 376 scoring rows were available across the 34 sheet entries:
34 callback outcomes, 168 callback marks, and 174 final ranks. All 939 judging
marks were available across 35 rounds. Native keyboard expansion worked.

The page was inspected at desktop and mobile sizes; the final page had no
horizontal overflow at 320 or 1440 pixels. A local HTTP preview also passed.
A desktop Lighthouse snapshot returned 100 for accessibility, best practices,
SEO, and agentic browsing. This is an automated snapshot, not a full manual
accessibility certification. The browser console reported no script errors.

## Reproduction

Follow the [build instructions](../../tools/quality/person-history/README.md).
The generator requires only Python’s standard library; the result works from
`file://` with no network requests or external fonts. The retained
[input verification and browser checks](../../evidence/quality/jesann-nail-2026-09-17/README.md)
record the dataset and observed results.
