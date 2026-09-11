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
weights start hand-set in `link/weights.toml` and are later fit with Splink (Fellegi-Sunter with
term-frequency adjustment, DuckDB backend) using scoring.dance rows as
labeled truth, because that source prints WSDC ids next to names.

Signals:

| Signal                | Effect                                                                                                                                               |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name similarity       | main signal                                                                                                                                          |
| Name rarity           | a rare surname match counts more (term frequency)                                                                                                    |
| Division consistency  | the dancer's registry level for that role on the event date must allow the contest's division. A Champion dancing Novice is a near-impossible match. |
| Role consistency      | registry primary role matches entry role; weaker signal because dancers switch                                                                       |
| Recency               | dancer has registry activity within 3 years of the event                                                                                             |
| Geography             | DCN city/country vs. the dancer's recent event locations; weak                                                                                       |
| Bib reuse             | the same bib at the same event in another contest already linked to a dancer; strong                                                                 |
| Registry confirmation | one exact normalized-name identity matches the same event, role, division, style, and numeric place or finalist result `F`; decisive                 |
| Source-provided id    | scoring.dance `data-wsdc`; decisive                                                                                                                  |

Per-event constraints are applied after scoring as an assignment
problem: within one event, one WSDC id links to at most one bib per role,
and one bib per role links to at most one WSDC id. We solve with
`scipy.optimize.linear_sum_assignment` on `-log(score)`, then reject
assignments below threshold.

`method` enum: `source_id`, `registry_placement`, `bib_reuse`,
`name_unique`, `name_scored`, `assignment`, `manual`, `none`.

## Link status

| `link_status` | Meaning                                                                                  | Typical confidence |
| ------------- | ---------------------------------------------------------------------------------------- | ------------------ |
| `confirmed`   | source printed the WSDC id, or the registry shows the placement, or a human confirmed it | 1.0                |
| `probable`    | exactly one candidate above 0.9 and constraints hold                                     | 0.9 to 0.99        |
| `possible`    | best candidate between 0.7 and 0.9, or two candidates close together                     | 0.5 to 0.9         |
| `ambiguous`   | multiple candidates, none clearly best                                                   | below 0.5          |
| `unmatched`   | no candidate above 0.5. Common for Newcomers with no WSDC number yet                     | 0                  |
| `suppressed`  | removed on request; `wsdc_id` and `name_raw` are null                                    | -                  |

`entries.wsdc_id` and `judges.wsdc_id` are populated only for
`confirmed` and `probable`. Consumers who want more recall, or a
different threshold, use `link_candidates`, which keeps every scored
candidate with every signal. Nothing the linker computed is discarded.

## Retroactive correction

Links change over time by design:

1. Event weekend: name-based links (`probable`, `possible`).
2. When the registry posts results, about a week later according to the
   owner, finalists can get `confirmed` via `registry_placement`. This timing is an expectation,
   not a deadline. A `probable` link that the
   registry contradicts is superseded and the entry is re-scored.
3. Any time: a manual override row in `overrides/identity_overrides.csv`
   (`entry_id, wsdc_id or NONE, reason, author, date`) wins over
   everything and produces a `manual` link.
4. Any time: the registry merges two numbers. `dancers.merged_into_wsdc_id`
   is set and links are rewritten to the surviving number.
5. Parser or linker upgrade: all affected links are recomputed.

Build derives each change against the published baseline as a
`changelog` row. Tables always hold the current
belief. To ask "what did swingset believe on date X", load the tables at
the Hub commit from that date. The Hub keeps every version.

Judges are linked with the same machinery. Their candidates are
restricted to registry dancers with `is_pro = true` or with Champion or
All-Star points, which is almost always true of judges and removes most
same-name confusion.

## Newcomers and first points

A dancer without a WSDC number has no registry row until they earn a
point. Their entries stay `unmatched`. New numbers are issued throughout the
year. Recent unlinked, points-eligible individual Newcomer or Novice finalists
cause bounded daily probes for up to 30 days; probes continue weekly otherwise.
When a later lookup projects the new registry row, the next linker run
automatically revisits retained older results.

Registry confirmation requires one exact normalized-name identity with the
same event, role, division, and style, plus either the actual numeric place or
registry result `F` for an entry in the recorded finals. `F` can confirm the
identity but does not establish a numeric rank or attach points to that exact
placement. Bib reuse remains a separate linking signal.
This is expected and is the main reason the dataset is "eventually
correct".

## Work ownership

Linking consumes event-scoped work from [local state](state.md#invalidation).
A change to weights, nicknames, identity overrides, or `LINKER_VERSION`
enqueues all event scopes transactionally. Registry changes also enqueue
all events in v1 because new dancers can match formerly unmatched entries.
The whole event assignment and its output are one transaction. Link owns
identity columns, `identity_links`, and `link_candidates`; it never
advances a revision just to make an unrelated canonical change publish.
`LINKER_VERSION` is recorded on identity assertions. Weight fitting is
an offline v1.1 step that proposes a new weights file, never a runtime
mode inside this linker.
