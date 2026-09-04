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
One point in a higher division moves a dancer up immediately. Sliding
36-month windows may apply to Advanced and All-Star (**unverified** in the
2026 text).

`points_matches_expected` in `placements` compares `registry_points_*`
against this chart with the tier inferred from `rounds.entry_count` of
the prelims round for that role. It is computed only after the registry
has posted. Neither the expected value nor the tier is published.
`registry_points_*` are the truth.
