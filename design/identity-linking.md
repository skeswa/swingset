# Identity linking

This is the hard part and the reason the dataset says "best effort".

## Name normalization

`name_norm` is computed the same way for entries, judges, and registry
dancers:

1. Unicode NFKC, then strip combining marks (NFD, drop category `Mn`).
2. `casefold()`.
3. Replace punctuation with spaces. Collapse whitespace.
4. Drop generational suffixes (`jr`, `sr`, `ii`, `iii`) into a separate
   field.
5. Split into tokens. Keep the full token list. Also compute
   `first_token` and `last_token`.
6. Nickname expansion uses a curated CSV (`overrides/nicknames.csv`,
   e.g. `mike -> michael`, `liz -> elizabeth`). Applied only when
   generating candidates, never stored as the name.

The original `name_raw` is always kept. Normalization is lossy and is
only for matching.

## Candidate generation

For an entry with `name_norm`, candidates are registry dancers where any
of the following holds:

- exact `name_norm` match;
- same `last_token` and first tokens match after nickname expansion;
- Jaro-Winkler on the full normalized name >= 0.92 (RapidFuzz);
- token-set ratio >= 90 (handles "Mary Jane Smith" vs "Mary Smith").

Blocking on `last_token` first letter keeps this fast: 29k dancers is
small enough to score everything for every entry per event anyway.

## Scoring and methods

Each candidate gets a score in [0, 1] from a weighted combination. The
weights start hand-set and are later fit with Splink (Fellegi-Sunter with
term-frequency adjustment, DuckDB backend) using scoring.dance rows as
labeled truth, because that source prints WSDC ids next to names.

Signals:

| Signal | Effect |
|---|---|
| Name similarity | main signal |
| Name rarity | a rare surname match counts more (term frequency) |
| Division consistency | the dancer's registry level for that role on the event date must allow the contest's division. A Champion dancing Novice is a near-impossible match. |
| Role consistency | registry primary role matches entry role; weaker signal because dancers switch |
| Recency | dancer has registry activity within 3 years of the event |
| Geography | DCN city/country vs. the dancer's recent event locations; weak |
| Bib reuse | the same bib at the same event in another contest already linked to a dancer; strong |
| Registry confirmation | after the event, the registry shows this dancer placed in this contest's division at this series in this month; decisive |
| Source-provided id | scoring.dance `data-wsdc`; decisive |

Per-event constraints are applied after scoring as an assignment
problem: within one event, one WSDC id links to at most one bib per role,
and one bib per role links to at most one WSDC id. We solve with
`scipy.optimize.linear_sum_assignment` on `-log(score)`, then reject
assignments below threshold.

`method` enum: `source_id`, `registry_placement`, `bib_reuse`,
`name_unique`, `name_scored`, `assignment`, `manual`, `none`.

## Link status

| `link_status` | Meaning | Typical confidence |
|---|---|---|
| `confirmed` | source printed the WSDC id, or the registry shows the placement, or a human confirmed it | 1.0 |
| `probable` | exactly one candidate above 0.9 and constraints hold | 0.9 to 0.99 |
| `possible` | best candidate between 0.7 and 0.9, or two candidates close together | 0.5 to 0.9 |
| `ambiguous` | multiple candidates, none clearly best | below 0.5 |
| `unmatched` | no candidate above 0.5. Common for Newcomers with no WSDC number yet | 0 |
| `suppressed` | removed on request; `wsdc_id` and `name_raw` are null | - |

`entries.wsdc_id` and `judges.wsdc_id` are populated only for
`confirmed` and `probable`. Consumers who want more recall, or a
different threshold, use `link_candidates`, which keeps every scored
candidate with every signal. Nothing the linker computed is discarded.

## Retroactive correction

Links change over time by design:

1. Event weekend: name-based links (`probable`, `possible`).
2. One to seven days later: registry posts results. Finalists get
   `confirmed` via `registry_placement`. A `probable` link that the
   registry contradicts is superseded and the entry is re-scored.
3. Any time: a manual override row in `overrides/identity_overrides.csv`
   (`entry_id, wsdc_id or NONE, reason, author, date`) wins over
   everything and produces a `manual` link.
4. Any time: the registry merges two numbers. `dancers.merged_into_wsdc_id`
   is set and links are rewritten to the surviving number.
5. Parser or linker upgrade: all affected links are recomputed.

Every change writes a `changelog` row. Tables always hold the current
belief. To ask "what did swingset believe on date X", load the tables at
the Hub commit from that date. The Hub keeps every version.

Judges are linked with the same machinery. Their candidates are
restricted to registry dancers with `is_pro = true` or with Champion or
All-Star points, which is almost always true of judges and removes most
same-name confusion.

## Newcomers and first points

A dancer without a WSDC number has no registry row until they earn a
point. Their entries stay `unmatched`. When they later get a number, the
next linker run finds the new registry row (weekly probe, [sources](sources.md#wsdc-registry-pointsworldsdccom)),
and `bib_reuse` plus `registry_placement` confirm the earlier entries.
This is expected and is the main reason the dataset is "eventually
correct".
