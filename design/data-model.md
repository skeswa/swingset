# Data model

All published tables are Parquet. Types are Arrow types. `id` columns are
strings. Timestamps are UTC with microsecond precision. Dates are
`date32`. Enums are strings with a fixed vocabulary listed in
`docs/enums.md` (to be written) and enforced at build time.

## Identifiers

Ids are deterministic, readable, and stable across rebuilds. They are
built from natural keys, not from database sequences.

| Id | Format | Example |
|---|---|---|
| `series_id` | `wsdc-<registry event id>` when known, else `slug-<name slug>` | `wsdc-53` |
| `event_id` | `<yyyy-mm>-<series slug>` | `2026-08-summer-hummer` |
| `contest_id` | `<event_id>/<contest slug>` | `2026-08-summer-hummer/novice-jj` |
| `round_id` | `<contest_id>/<round type>[-<n>]` | `2026-08-summer-hummer/novice-jj/prelim` |
| `entry_id` | `<contest_id>/<role letter>-<bib>` for J&J; `<contest_id>/C-<bib>` for couples | `2026-08-summer-hummer/novice-jj/L-255` |
| `heat_id` | `<round_id>/heat-<n>` | `.../prelim/heat-3` |
| `judge_id` | `<event_id>/judge/<name slug or "anon-n">` | `2026-08-summer-hummer/judge/jane-doe` |
| `placement_id` | `<round_id>/place-<n>` | `.../final/place-1` |
| `snapshot_id` | `snap_<fetched_at compact>_<sha256 prefix 12>` | `snap_20260906T031500Z_9f2c1a7b3e4d` |
| `run_id` | `run_<start time compact>` | `run_20260906T031500Z` |

The month in `event_id` is the month of the event's end date, because
the registry records an event by series and "Month YYYY" and appears to
use the month results were reported (**unverified**; checked during the
bootstrap sweep by comparing registry months to calendar dates). This
lets `registry_placements` join to `events` on `(series_id, event_month)`
without tolerance logic. A series that runs twice in one month gets a
`-2` suffix.

If a source shows no bib (DCN payload before its PDF is parsed), the
entry id uses `<role letter>-name-<name slug>` and is rewritten to the bib
form once the PDF is parsed. The rewrite is recorded in `changelog`.

Two contests with the same slug in one event get `-2`, `-3` suffixes in
source order.

Ids and enum vocabularies are public API. Adding a value is allowed at
any time. Renaming or removing one is a schema change ([publishing](publishing.md#commit-strategy)).

## Tables

Columns marked `(prov)` are provenance columns present on every fact
table: `source` (enum), `snapshot_id`, `parser_version`, `first_seen_at`,
`last_seen_at`, `run_id`.

**`events`**

| Column | Type | Notes |
|---|---|---|
| `event_id` | string | key |
| `series_id` | string | |
| `name` | string | canonical name |
| `year` | int16 | |
| `start_date` | date32 | |
| `end_date` | date32 | |
| `city` | string | nullable |
| `region` | string | state or province, nullable |
| `country` | string | ISO 3166-1 alpha-2, nullable |
| `website` | string | nullable |
| `wsdc_status` | enum | `registry`, `trial`, `unconfirmed`, `unknown` |
| `sources` | list<string> | which platforms carry this event |
| `live_window_start`, `live_window_end` | timestamp | for transparency |
| (prov) | | |

**`contests`**

| Column | Type | Notes |
|---|---|---|
| `contest_id` | string | key |
| `event_id` | string | |
| `name_raw` | string | as printed |
| `division` | enum | `newcomer`, `novice`, `intermediate`, `advanced`, `allstar`, `champion`, `open`, `invitational`, `none` |
| `age_division` | enum | `none`, `juniors`, `sophisticated`, `masters` |
| `contest_type` | enum | `jack_and_jill`, `strictly`, `classic`, `showcase`, `pro_am`, `rising_star`, `other` |
| `partner_mode` | enum | `random_partner`, `open_couple`, `perm_couple` |
| `dance_style` | enum | `wcs`, `lindy`, `country`, `other` |
| `wsdc_points_eligible` | bool | J&J in a skill division at a registry event |
| `combined_from` | list<enum> | e.g. `[newcomer, novice]` when divisions were merged |
| `parse_status` | enum | `parsed`, `unsupported`, `failed` |
| `source_contest_ref` | string | platform's own id |
| (prov) | | |

**`rounds`**

| Column | Type | Notes |
|---|---|---|
| `round_id` | string | key |
| `contest_id` | string | |
| `round_type` | enum | `prelim`, `quarterfinal`, `semifinal`, `final` |
| `round_index` | int8 | order within contest, 1-based |
| `name_raw` | string | as printed |
| `scoring_method` | enum | `callback`, `relative_placement` |
| `callback_legend` | enum | `wsdc_10`, `legacy_3`, `unknown` |
| `judge_count` | int8 | |
| `chief_judge_id` | string | nullable |
| `entry_count` | int32 | entries that danced |
| `promoted_count` | int32 | nullable for finals |
| `source_round_ref` | string | |
| `score_sheet_url` | string | nullable |
| (prov) | | |

**`entries`** (participation)

| Column | Type | Notes |
|---|---|---|
| `entry_id` | string | key |
| `contest_id` | string | |
| `event_id` | string | denormalized |
| `role` | enum | `leader`, `follower`, `couple` |
| `bib` | string | nullable, as printed (may have letters) |
| `name_raw` | string | |
| `name_norm` | string | see 11.1 |
| `partner_name_raw` | string | couples only |
| `city_raw`, `country_raw` | string | DCN only |
| `wsdc_id` | int32 | nullable; the current best link |
| `link_status` | enum | see 11.4 |
| `link_confidence` | float32 | 0 to 1 |
| `partner_entry_id` | string | couples: the other person's entry when split |
| `rounds_danced` | list<enum> | |
| `best_round` | enum | furthest round reached |
| (prov) | | |

**`heats`**

| Column | Type | Notes |
|---|---|---|
| `heat_id` | string | key with `entry_id` |
| `round_id` | string | |
| `heat_number` | int16 | |
| `entry_id` | string | |
| `position` | int16 | nullable, order within heat |
| (prov) | | |

**`judges`**

| Column | Type | Notes |
|---|---|---|
| `judge_id` | string | key |
| `event_id` | string | |
| `name_raw` | string | |
| `initials` | string | nullable |
| `anonymous` | bool | |
| `wsdc_id` | int32 | nullable, linked like entries |
| (prov) | | |

**`callback_marks`**

| Column | Type | Notes |
|---|---|---|
| `round_id` | string | key with `entry_id`, `judge_id` |
| `entry_id` | string | |
| `judge_id` | string | |
| `mark` | enum | `yes`, `alt1`, `alt2`, `alt3`, `no` |
| `mark_raw` | string | |
| `mark_value` | float32 | in the round's legend |
| (prov) | | |

**`callbacks`**

| Column | Type | Notes |
|---|---|---|
| `round_id` | string | key with `entry_id` |
| `entry_id` | string | |
| `score_sum` | float32 | |
| `yes_count`, `alt_count`, `no_count` | int8 | |
| `outcome` | enum | `promoted`, `alternate_1`, `alternate_2`, `alternate_3`, `eliminated` |
| `tie_break_applied` | bool | nullable |
| `heat_number` | int16 | nullable |
| (prov) | | |

**`final_marks`**

| Column | Type | Notes |
|---|---|---|
| `round_id` | string | key with `placement_id`, `judge_id` |
| `placement_id` | string | |
| `judge_id` | string | |
| `rank` | int8 | |
| (prov) | | |

**`placements`**

| Column | Type | Notes |
|---|---|---|
| `placement_id` | string | key |
| `round_id` | string | |
| `contest_id` | string | |
| `event_id` | string | |
| `place` | int8 | |
| `leader_entry_id` | string | nullable for couple entries |
| `follower_entry_id` | string | nullable for couple entries |
| `couple_entry_id` | string | nullable for J&J |
| `leader_wsdc_id`, `follower_wsdc_id` | int32 | denormalized current links |
| `marks_sorted` | string | e.g. `1-1-1-2-2-4-5` |
| `tally` | list<int8> | Relative Placement column counts |
| `registry_points_leader`, `registry_points_follower` | int16 | nullable; from the registry once posted |
| `registry_confirmed` | bool | both sides found in registry for this event and division |
| `points_matches_expected` | bool | nullable; null until the registry posts. True when the registry's points equal what [WSDC rules](wsdc-rules.md) predicts from the prelims field size. A data-quality flag, not a claim. |
| (prov) | | |

No `expected_points` or `tier` column is published. Both are computed
internally to produce the flag and can be recomputed by anyone from
`rounds.entry_count` and [WSDC rules](wsdc-rules.md).

**`dancers`** (registry mirror)

| Column | Type | Notes |
|---|---|---|
| `wsdc_id` | int32 | key |
| `first_name`, `last_name` | string | |
| `name_norm` | string | |
| `is_pro` | bool | |
| `primary_role` | enum | `leader`, `follower`, `unknown` |
| `leader_required_level`, `leader_allowed_level` | enum | division codes |
| `follower_required_level`, `follower_allowed_level` | enum | |
| `leader_highest_level`, `leader_highest_points` | enum, int16 | |
| `follower_highest_level`, `follower_highest_points` | enum, int16 | |
| `recent_year` | int16 | |
| `registry_internal_id` | int32 | `dancer.id` |
| `registry_fetched_at` | timestamp | |
| `merged_into_wsdc_id` | int32 | nullable; if WSDC merged this number |
| (prov) | | |

**`registry_placements`**

| Column | Type | Notes |
|---|---|---|
| `wsdc_id` | int32 | key with `role`, `series_id`, `event_month`, `division`, `dance_style` |
| `role` | enum | |
| `dance_style` | enum | |
| `division` | enum | registry category: skill division or `juniors`, `sophisticated`, `masters`; age categories stay distinct in the key |
| `series_id` | string | |
| `series_name_raw` | string | |
| `event_month` | date32 | first of month; the registry gives only "Month YYYY" |
| `event_id` | string | nullable; matched to our events |
| `result` | string | `1`..`5` or `F` |
| `points` | int16 | |
| (prov) | | |

**`identity_links`** (current assertion per entry or judge)

| Column | Type | Notes |
|---|---|---|
| `link_id` | string | key |
| `subject_kind` | enum | `entry`, `judge` |
| `subject_id` | string | `entry_id` or `judge_id` |
| `wsdc_id` | int32 | nullable when asserting "no match" |
| `method` | enum | see 11.3 |
| `status` | enum | see 11.4 |
| `confidence` | float32 | |
| `constraints_applied` | list<string> | e.g. `bib_unique`, `division_allowed` |
| `asserted_at` | timestamp | |
| `run_id` | string | |

Earlier assertions are not kept in this table. They are recoverable from
the Hub commit history and are summarized in `changelog` with reason
`link_upgraded` or `link_downgraded`.

**`link_candidates`** (every scored candidate, so no information is lost)

| Column | Type | Notes |
|---|---|---|
| `subject_kind` | enum | key with `subject_id`, `wsdc_id` |
| `subject_id` | string | |
| `wsdc_id` | int32 | |
| `score` | float32 | final combined score |
| `name_similarity` | float32 | |
| `name_rarity` | float32 | |
| `division_ok` | bool | nullable when unknown |
| `role_ok` | bool | |
| `recency_ok` | bool | |
| `geography` | float32 | nullable |
| `bib_reuse` | bool | |
| `registry_confirms` | bool | |
| `source_id_confirms` | bool | |
| `rank` | int16 | 1 = best candidate for this subject |
| `chosen` | bool | matches `identity_links` |
| `run_id` | string | linker run that produced this row |

Rows reflect the latest linker run for each subject. A consumer can
re-derive any threshold policy from this table alone.

**`review_queue`**

| Column | Type | Notes |
|---|---|---|
| `item_id` | string | key |
| `kind` | enum | `ambiguous_link`, `contradicted_link`, `event_alias`, `unsupported_contest`, `registry_diff`, `parse_failure`, `conflict`, `invalid_response`, `unknown_enum` |
| `subject_id` | string | entry, judge, event, contest, or snapshot id |
| `summary` | string | one line a human can act on |
| `suggested_override` | string | a ready-to-paste CSV row for `overrides/` |
| `opened_at` | timestamp | |
| `run_id` | string | |

This table is computed at build from open findings and current state;
it is not stored in SQLite. Items disappear when an override resolves
them or the condition clears. See [architecture](architecture.md#findings-and-review)
and [build](build.md#review-queue).

**`changelog`**

| Column | Type | Notes |
|---|---|---|
| `changed_at` | timestamp | |
| `run_id` | string | |
| `table` | string | |
| `record_key` | string | JSON of the key columns |
| `field` | string | nullable; null means row added or removed |
| `old_value`, `new_value` | string | JSON-encoded |
| `change_type` | enum | `added`, `removed`, `updated` |
| `reason` | enum | `new_source_data`, `source_corrected`, `parser_fixed`, `link_upgraded`, `link_downgraded`, `manual_override`, `registry_update`, `suppression` |

**`snapshots`** (published subset of the archive index)

`snapshot_id`, `source`, `url`, `fetched_at`, `http_status`,
`body_sha256`, `body_bytes`, `content_changed`, `parser`,
`parser_version`, `parse_status`. Bodies are not published.

## Relationships

```
series 1─* events 1─* contests 1─* rounds 1─* heats
                          │             ├─* callback_marks ─┐
                          │             ├─* callbacks       ├─ entries *─? dancers
                          │             ├─* final_marks ─┐  │
                          │             └─* placements ──┴──┘
                          └─* judges
dancers 1─* registry_placements *─? events
entries 1─1 identity_links 1─* link_candidates
judges  1─1 identity_links
```
