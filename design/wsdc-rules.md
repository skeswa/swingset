# WSDC rules we encode

Source: WSDC Registry Event Rules 2026, v2026.1.

Callback legend: Yes = 10, Alt1 = 4.5, Alt2 = 4.3, Alt3 = 4.2, No = 0.
Legacy legend: Yes = 1, Alt1 = 2.1, Alt2 = 2.2, Alt3 = 2.3, No = 3
(lower is better).

Points by tier (unique competitors in the role), places 1 to 5:

| Tier | Competitors | 1st | 2nd | 3rd | 4th | 5th | Extra |
|---|---|---|---|---|---|---|---|
| 1 | 5 to 10 | 3 | 2 | 1 | 0 | 0 | |
| 2 | 11 to 19 | 6 | 4 | 3 | 2 | 1 | |
| 3 | 20 to 39 | 10 | 8 | 6 | 4 | 2 | 1 point through 10th |
| 4 | 40 to 79 | 15 | 12 | 10 | 8 | 6 | 1 point through 12th |
| 5 | 80 to 129 | 20 | 16 | 14 | 12 | 10 | 2 points through 15th |
| 6 | 130+ | 25 | 22 | 18 | 15 | 12 | 2 points through 15th |

Minimum 5 leaders and 5 followers in the final for points. Combined
divisions award the lower division's points. Only J&J at registry events
earns points. Strictly, Classic, and Showcase never do.

Level thresholds (allowed / required to move up): Novice 16 / 30,
Intermediate 30 / 45, Advanced 60 / 90, All-Star to Champion 150 / 225.
One point in a higher division moves a dancer up immediately. The
36-month windows on Advanced and All-Star points ended on 2023-07-05; the
2026 text has no time limits. How these values changed since 2010 is in
[WSDC rules history](wsdc-rules-history.md).

## Rule history that splits cohorts

From `research/prior-art-registry-analyses.md`; dates need checking
against the rule PDFs archived in `conniewang3/WSDC-Project/files/`.

| Change | When | Note |
|---|---|---|
| Novice points to move up: 20 to 15 | 2012 or 2013 (**unverified** which) | |
| Novice: 15 allowed to 16 allowed, 30 required | 2018 | Dancers with exactly 15 points before 2018 were grandfathered |
| All-Star eligibility: from "45 Advanced points in 3 years or 1 All-Star point ever" to "45 Advanced points in 3 years or 3 All-Star points in 3 years" | 2018 | 76 All-Stars were demoted by this change (Wang) |
| Thresholds in the table above | 2026 text | |

`points_matches_expected` in `placements` compares `registry_points_*`
against this chart with the tier inferred from `rounds.entry_count` of
the prelims round for that role. It is computed only after the registry
has posted. Neither the expected value nor the tier is published.
`registry_points_*` are the truth.
