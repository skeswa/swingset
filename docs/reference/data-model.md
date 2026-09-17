# Data model

This page describes the tables published in the dataset. An event contains contests; a contest has entries and rounds. Read the [overview](../overview.md) for an example and the [glossary](glossary.md) for unfamiliar terms.

[Reference index](README.md)

All published tables are Parquet. Types are Arrow types. `id` columns are
strings. Timestamps are UTC with microsecond precision. Dates are
`date32`. Enums are strings with a fixed vocabulary listed in
`docs/reference/enums.md` (to be written) and enforced at build time.

## Identifiers

Ids are deterministic, readable, and stable across rebuilds. They are
built from natural keys, not from database sequences.

| Id             | Format                                                                         | Example                                           |
| -------------- | ------------------------------------------------------------------------------ | ------------------------------------------------- |
| `series_id`    | `wsdc-<registry event id>` when known, else `slug-<name slug>`                 | `wsdc-53`                                         |
| `event_id`     | `<yyyy-mm>-<series slug>`                                                      | `2026-08-summer-hummer`                           |
| `contest_id`   | `<event_id>/<contest slug>`                                                    | `2026-08-summer-hummer/novice-jj`                 |
| `round_id`     | `<contest_id>/<round type>[-<n>]`                                              | `2026-08-summer-hummer/novice-jj/prelim`          |
| `entry_id`     | `<contest_id>/<role letter>-<bib>` for J&J; `<contest_id>/C-<bib>` for couples | `2026-08-summer-hummer/novice-jj/L-255`           |
| `heat_id`      | `<round_id>/heat-<n>`                                                          | `.../prelim/heat-3`                               |
| `judge_id`     | `<event_id>/judge/<name slug or "anon-n">`                                     | `2026-08-summer-hummer/judge/jane-doe`            |
| `placement_id` | `<round_id>/place-<n>`                                                         | `.../final/place-1`                               |
| `snapshot_id`  | `snap_<fetched_at compact>_<body hash 12>_<watch hash 12>`                     | `snap_20260906T031500Z_9f2c1a7b3e4d_12ab34cd56ef` |
| `run_id`       | `run_<start time compact>`                                                     | `run_20260906T031500Z`                            |

Existing snapshot IDs keep their original form without the watch hash. New
acquisitions include the watch hash so identical bodies returned by different
hosts within one second retain separate provenance. Bodies still deduplicate
by their full SHA-256.

The history start date is 2010-01-01: no `events` row exists for an
earlier edition ([backfill](backfill.md#the-start-date-rule)).

The month in `event_id` is the month of the event's end date, because
the registry records an event by series and "Month YYYY" and appears to
use the month results were reported (**unverified**; checked during the
bootstrap sweep by comparing registry months to calendar dates). This
lets registry placements match event editions by series name and month.
The implementation preserves registry `wsdc-*` and calendar `slug-*` series
IDs and stores the reconciled edition in `registry_placements.event_id`;
consumers join on that nullable event ID
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

| Column                                 | Type         | Notes                                                                                           |
| -------------------------------------- | ------------ | ----------------------------------------------------------------------------------------------- |
| `event_id`                             | string       | key                                                                                             |
| `series_id`                            | string       |                                                                                                 |
| `name`                                 | string       | canonical name                                                                                  |
| `year`                                 | int16        |                                                                                                 |
| `start_date`                           | date32       | nullable; null when only the registry month is known                                            |
| `end_date`                             | date32       | nullable                                                                                        |
| `event_month`                          | string       | `yyyy-mm`, the month in `event_id`                                                              |
| `date_precision`                       | enum         | `day`, `month`                                                                                  |
| `coverage_tier`                        | enum         | `sheets_complete`, `sheets_partial`, `index_only`, `registry_only`; see [backfill](backfill.md) |
| `history_source`                       | list<enum>   | which of `calendar`, `platform`, `registry`, `steprightsolutions` named the event               |
| `city`                                 | string       | nullable                                                                                        |
| `region`                               | string       | state or province, nullable                                                                     |
| `country`                              | string       | ISO 3166-1 alpha-2, nullable                                                                    |
| `website`                              | string       | nullable                                                                                        |
| `wsdc_status`                          | enum         | `registry`, `trial`, `unconfirmed`, `unknown`                                                   |
| `sources`                              | list<string> | which platforms carry this event                                                                |
| `live_window_start`, `live_window_end` | timestamp    | for transparency                                                                                |
| (prov)                                 |              |                                                                                                 |

**`contests`**

| Column                 | Type       | Notes                                                                                                   |
| ---------------------- | ---------- | ------------------------------------------------------------------------------------------------------- |
| `contest_id`           | string     | key                                                                                                     |
| `event_id`             | string     |                                                                                                         |
| `name_raw`             | string     | as printed                                                                                              |
| `division`             | enum       | `newcomer`, `novice`, `intermediate`, `advanced`, `allstar`, `champion`, `open`, `invitational`, `none` |
| `age_division`         | enum       | `none`, `juniors`, `sophisticated`, `masters`                                                           |
| `contest_type`         | enum       | `jack_and_jill`, `strictly`, `classic`, `showcase`, `pro_am`, `rising_star`, `other`                    |
| `partner_mode`         | enum       | `random_partner`, `open_couple`, `perm_couple`                                                          |
| `dance_style`          | enum       | `wcs`, `lindy`, `country`, `other`                                                                      |
| `wsdc_points_eligible` | bool       | J&J in a skill division at a registry event                                                             |
| `combined_from`        | list<enum> | e.g. `[newcomer, novice]` when divisions were merged                                                    |
| `parse_status`         | enum       | `parsed`, `unsupported`, `failed`                                                                       |
| `source_contest_ref`   | string     | platform's own id                                                                                       |
| (prov)                 |            |                                                                                                         |

**`rounds`**

| Column             | Type   | Notes                                                                    |
| ------------------ | ------ | ------------------------------------------------------------------------ |
| `round_id`         | string | key                                                                      |
| `contest_id`       | string |                                                                          |
| `round_type`       | enum   | `prelim`, `quarterfinal`, `semifinal`, `final`                           |
| `round_index`      | int8   | order within contest, 1-based                                            |
| `name_raw`         | string | as printed                                                               |
| `scoring_method`   | enum   | `callback`, `relative_placement`                                         |
| `callback_legend`  | enum   | `wsdc_10`, `legacy_3`, `unknown`; Step Right Solutions prints `legacy_3` |
| `judge_count`      | int8   | round-wide distinct judge roster; not each entrant's voting-panel size   |
| `chief_judge_id`   | string | nullable                                                                 |
| `entry_count`      | int32  | entries that danced                                                      |
| `promoted_count`   | int32  | nullable for finals                                                      |
| `source_round_ref` | string |                                                                          |
| `score_sheet_url`  | string | nullable                                                                 |
| (prov)             |        |                                                                          |

**`entries`** (participation)

| Column                    | Type       | Notes                                                                       |
| ------------------------- | ---------- | --------------------------------------------------------------------------- |
| `entry_id`                | string     | key                                                                         |
| `contest_id`              | string     |                                                                             |
| `event_id`                | string     | denormalized                                                                |
| `role`                    | enum       | `leader`, `follower`, `couple`                                              |
| `bib`                     | string     | nullable, as printed (may have letters)                                     |
| `name_raw`                | string     |                                                                             |
| `name_norm`               | string     | see 11.1                                                                    |
| `partner_name_raw`        | string     | couples only                                                                |
| `city_raw`, `country_raw` | string     | DCN only                                                                    |
| `wsdc_id`                 | int32      | nullable; the current best link                                             |
| `link_status`             | enum       | see 11.4                                                                    |
| `link_confidence`         | float32    | 0 to 1                                                                      |
| `partner_entry_id`        | string     | couples: the other person's entry when split                                |
| `rounds_danced`           | list<enum> | round types (`prelim`, `quarterfinal`, `semifinal`, `final`), not round IDs |
| `best_round`              | enum       | furthest round reached                                                      |
| (prov)                    |            |                                                                             |

**`heats`**

| Column        | Type   | Notes                       |
| ------------- | ------ | --------------------------- |
| `heat_id`     | string | key with `entry_id`         |
| `round_id`    | string |                             |
| `heat_number` | int16  |                             |
| `entry_id`    | string |                             |
| `position`    | int16  | nullable, order within heat |
| (prov)        |        |                             |

**`judges`**

| Column      | Type   | Notes                         |
| ----------- | ------ | ----------------------------- |
| `judge_id`  | string | key                           |
| `event_id`  | string |                               |
| `name_raw`  | string |                               |
| `initials`  | string | nullable                      |
| `anonymous` | bool   |                               |
| `wsdc_id`   | int32  | nullable, linked like entries |
| (prov)      |        |                               |

**`callback_marks`**

| Column       | Type    | Notes                               |
| ------------ | ------- | ----------------------------------- |
| `round_id`   | string  | key with `entry_id`, `judge_id`     |
| `entry_id`   | string  |                                     |
| `judge_id`   | string  |                                     |
| `mark`       | enum    | `yes`, `alt1`, `alt2`, `alt3`, `no` |
| `mark_raw`   | string  |                                     |
| `mark_value` | float32 | in the round's legend               |
| (prov)       |         |                                     |

**`callbacks`**

| Column                               | Type    | Notes                                                                                                    |
| ------------------------------------ | ------- | -------------------------------------------------------------------------------------------------------- |
| `round_id`                           | string  | key with `entry_id`                                                                                      |
| `entry_id`                           | string  |                                                                                                          |
| `score_sum`                          | float32 |                                                                                                          |
| `yes_count`, `alt_count`, `no_count` | int8    |                                                                                                          |
| `outcome`                            | enum    | `promoted`, `alternate` (source rank unknown), `alternate_1`, `alternate_2`, `alternate_3`, `eliminated` |
| `tie_break_applied`                  | bool    | nullable                                                                                                 |
| `heat_number`                        | int16   | nullable                                                                                                 |
| (prov)                               |         |                                                                                                          |

**`final_marks`**

| Column         | Type   | Notes                               |
| -------------- | ------ | ----------------------------------- |
| `round_id`     | string | key with `placement_id`, `judge_id` |
| `placement_id` | string |                                     |
| `judge_id`     | string |                                     |
| `rank`         | int8   |                                     |
| (prov)         |        |                                     |

**`placements`**

| Column                                               | Type       | Notes                                                                                                                                                                                   |
| ---------------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `placement_id`                                       | string     | key                                                                                                                                                                                     |
| `round_id`                                           | string     |                                                                                                                                                                                         |
| `contest_id`                                         | string     |                                                                                                                                                                                         |
| `event_id`                                           | string     |                                                                                                                                                                                         |
| `place`                                              | int8       |                                                                                                                                                                                         |
| `leader_entry_id`                                    | string     | nullable for couple entries                                                                                                                                                             |
| `follower_entry_id`                                  | string     | nullable for couple entries                                                                                                                                                             |
| `couple_entry_id`                                    | string     | nullable for J&J                                                                                                                                                                        |
| `leader_wsdc_id`, `follower_wsdc_id`                 | int32      | denormalized current links                                                                                                                                                              |
| `marks_sorted`                                       | string     | e.g. `1-1-1-2-2-4-5`                                                                                                                                                                    |
| `tally`                                              | list<int8> | Relative Placement column counts                                                                                                                                                        |
| `registry_points_leader`, `registry_points_follower` | int16      | nullable; from the registry once posted                                                                                                                                                 |
| `registry_confirmed`                                 | bool       | both sides found in registry for this event and division                                                                                                                                |
| `points_matches_expected`                            | bool       | nullable; null until the registry posts. True when the registry's points equal what [WSDC rules](wsdc-rules.md) predicts from the prelims field size. A data-quality flag, not a claim. |
| (prov)                                               |            |                                                                                                                                                                                         |

No `expected_points` or `tier` column is published. Both are computed
internally to produce the flag and can be recomputed by anyone from
`rounds.entry_count` and [WSDC rules](wsdc-rules.md).

**`dancers`** (registry mirror)

| Column                                              | Type        | Notes                                |
| --------------------------------------------------- | ----------- | ------------------------------------ |
| `wsdc_id`                                           | int32       | key                                  |
| `first_name`, `last_name`                           | string      |                                      |
| `name_norm`                                         | string      |                                      |
| `is_pro`                                            | bool        |                                      |
| `primary_role`                                      | enum        | `leader`, `follower`, `unknown`      |
| `leader_required_level`, `leader_allowed_level`     | enum        | division codes                       |
| `follower_required_level`, `follower_allowed_level` | enum        |                                      |
| `leader_highest_level`, `leader_highest_points`     | enum, int16 |                                      |
| `follower_highest_level`, `follower_highest_points` | enum, int16 |                                      |
| `recent_year`                                       | int16       |                                      |
| `registry_internal_id`                              | int32       | `dancer.id`                          |
| `registry_fetched_at`                               | timestamp   |                                      |
| `merged_into_wsdc_id`                               | int32       | nullable; if WSDC merged this number |
| (prov)                                              |             |                                      |

**`registry_placements`**

| Column            | Type   | Notes                                                                                                                                                                            |
| ----------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wsdc_id`         | int32  | key with `role`, `series_id`, `event_month`, `division`, `dance_style`                                                                                                           |
| `role`            | enum   |                                                                                                                                                                                  |
| `dance_style`     | enum   |                                                                                                                                                                                  |
| `division`        | enum   | registry category: skill division or `juniors`, `sophisticated`, `masters`; literal `PRO` and `TCH` retain unverified source categories; age categories stay distinct in the key |
| `series_id`       | string |                                                                                                                                                                                  |
| `series_name_raw` | string |                                                                                                                                                                                  |
| `event_month`     | date32 | first of month; the registry gives only "Month YYYY"                                                                                                                             |
| `event_id`        | string | nullable; matched to our events                                                                                                                                                  |
| `result`          | string | `1`..`5` or `F`                                                                                                                                                                  |
| `points`          | int16  |                                                                                                                                                                                  |
| (prov)            |        |                                                                                                                                                                                  |

**`identity_links`** (current assertion per entry or judge)

| Column                | Type         | Notes                                                                 |
| --------------------- | ------------ | --------------------------------------------------------------------- |
| `link_id`             | string       | key                                                                   |
| `subject_kind`        | enum         | `entry`, `judge`                                                      |
| `subject_id`          | string       | `entry_id` or `judge_id`                                              |
| `wsdc_id`             | int32        | nullable when asserting "no match"                                    |
| `method`              | enum         | see 11.3                                                              |
| `status`              | enum         | see 11.4                                                              |
| `confidence`          | float32      |                                                                       |
| `constraints_applied` | list<string> | e.g. `bib_unique`, `division_allowed`                                 |
| `source_ref_ids`      | list<string> | durable original source references used in the current decision check |
| `decision_ids`        | list<string> | causative accepted journal decisions; private notes are not published |
| `acceptance_policy`   | string       | version of the identity decision policy                               |
| `acceptance_state`    | enum         | `accepted`, `unresolved`, or `revoked` for this release               |
| `journal_digest`      | string       | accepted journal digest checked at build and publication              |
| `journal_generation`  | int64        | includes reviewed reference migrations as well as journal changes     |
| `asserted_at`         | timestamp    |                                                                       |
| `run_id`              | string       |                                                                       |

Earlier assertions are not kept in this table. They are recoverable from
the private append-only identity history and the Hub commit history. They are summarized in `changelog` with reason
`link_upgraded` or `link_downgraded`.

**`link_candidates`** (every scored candidate, so no information is lost)

| Column               | Type    | Notes                               |
| -------------------- | ------- | ----------------------------------- |
| `subject_kind`       | enum    | key with `subject_id`, `wsdc_id`    |
| `subject_id`         | string  |                                     |
| `wsdc_id`            | int32   |                                     |
| `score`              | float32 | final combined score                |
| `name_similarity`    | float32 |                                     |
| `name_rarity`        | float32 |                                     |
| `division_ok`        | bool    | nullable when unknown               |
| `role_ok`            | bool    |                                     |
| `recency_ok`         | bool    |                                     |
| `geography`          | float32 | nullable                            |
| `bib_reuse`          | bool    |                                     |
| `registry_confirms`  | bool    |                                     |
| `source_id_confirms` | bool    |                                     |
| `rank`               | int16   | 1 = best candidate for this subject |
| `chosen`             | bool    | matches `identity_links`            |
| `run_id`             | string  | linker run that produced this row   |

Rows reflect the latest linker run for each subject. A consumer can
re-derive any threshold policy from this table alone.

**`review_queue`**

| Column               | Type      | Notes                                                                                                                                                         |
| -------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `item_id`            | string    | key                                                                                                                                                           |
| `kind`               | enum      | `ambiguous_link`, `contradicted_link`, `event_alias`, `unsupported_contest`, `registry_diff`, `parse_failure`, `conflict`, `invalid_response`, `unknown_enum` |
| `subject_id`         | string    | entry, judge, event, contest, or snapshot id                                                                                                                  |
| `summary`            | string    | one line a human can act on                                                                                                                                   |
| `suggested_override` | string    | a ready-to-paste CSV row for `overrides/`                                                                                                                     |
| `opened_at`          | timestamp |                                                                                                                                                               |
| `run_id`             | string    |                                                                                                                                                               |

This table is computed at build from open findings and current state;
it is not stored in SQLite. Items disappear when an override resolves
them or the condition clears. See [architecture](architecture.md#findings-and-review)
and [build](build.md#review-queue).

**`changelog`**

| Column                   | Type      | Notes                                                                                                                                          |
| ------------------------ | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `changed_at`             | timestamp |                                                                                                                                                |
| `run_id`                 | string    |                                                                                                                                                |
| `table`                  | string    |                                                                                                                                                |
| `record_key`             | string    | JSON of the key columns                                                                                                                        |
| `field`                  | string    | nullable; null means row added or removed                                                                                                      |
| `old_value`, `new_value` | string    | JSON-encoded                                                                                                                                   |
| `change_type`            | enum      | `added`, `removed`, `updated`                                                                                                                  |
| `reason`                 | enum      | `new_source_data`, `source_corrected`, `parser_fixed`, `link_upgraded`, `link_downgraded`, `manual_override`, `registry_update`, `suppression` |

**`snapshots`** (published subset of the archive index)

`snapshot_id`, `source`, `url`, `fetched_at`, `http_status`,
`body_sha256`, `body_bytes`, `content_changed`, `parser`,
`parser_version`, `parse_status`, `via` (`origin`, `wayback`,
`manual`), `captured_at`, `archive_url`, `observed_at`. Bodies are not published.
`observed_at` is the capture time for archive evidence and the fetch time
otherwise; fetching an old capture today does not make its facts newer.

**`coverage`**

The public key is `scope_kind`, `scope_id`, `source`, `via`. `scope_kind`
is `source`, `year`, or `event`. The pending event-completion extension adds
`source_event` as specified below. Source rows have a null year. The internal
SQLite table remains keyed by year, source, and transport. Release construction
adds scope rows and metadata without changing canonical fact generations.

Year rows retain `events`, `contests`, `rounds`, `entries`,
`events_registry_only`, `events_index_only`, `events_sheets_partial`,
`events_sheets_complete`, `events_day_precision`, `events_listed_only`,
`events_accepted`, `expected_rounds`, `parsed_rounds`, `unresolved_findings`,
and `last_changed_at`. Filter `scope_kind='year'` for the original aggregate
view. `events_accepted` describes reviewed year inventory and is null on other
scope kinds. Counts overlap across sources and scope levels; do not sum them
as unique event totals.

`scope_status`, `scope_reasons`, and `missing_scopes` identify retained,
withheld, or unavailable support, including scopes with no selected fact rows.
Missing dancer scopes expose their kind and count, not registry identifiers.
Source unit counts are `discovered_units`, `acquired_units`,
`interpreted_units`, `mapped_units`, `withheld_units`, `unavailable_units`,
and `unassessed_units`. A successful acquisition remains counted when a later
check fails. A retained accepted interpretation remains counted when newer
input is blocked. `withheld_scopes` and `unavailable_scopes` count omitted
derivation scopes separately. `identity_subjects`, `resolved_identities`,
and `withheld_identities` count selected entry and judge subjects, separately
from source units. A named judge need not have a registry number.

`discovery_denominator` is null and `discovery_universe='unknown'` unless a
complete discovery universe is established. Known retained units supply the
acquisition denominator; acquired units supply the interpretation denominator;
interpreted units supply the mapping denominator. None of these denominators
establishes the discovery universe. `expected_rounds` remains null when source
enumeration has not established it. Partial rows alone do not prove complete
sheet coverage or reviewed identity accuracy.

Each row records `method`, `population`, `uncertainty`, `evidence_cutoff`,
`evidence_observed_at`, `usable_verified_at`, and `health_as_of`. The latest
usable verification is distinct from when the source evidence was observed.
Archive capture time remains evidence time even when acquired years later.
The initial public health cadence is daily, with immediate material status
changes; successful identical poll timestamps alone do not force releases.

Common public tables—events, contests, rounds, entries, judges, placements,
dancers, and registry placements—also carry `scope_status` and
`evidence_observed_at`. These columns describe the selected release. They are
not added to mutable canonical SQL rows or immutable projection payloads.
They distinguish accepted support, retained or unassessed legacy facts, and
withheld identity joins without changing source names or inventing timestamps.

Year acceptance records an owner and the digest of the reviewed event
inventory. A changed inventory or an open year finding withholds acceptance.
Repeated counting preserves `last_changed_at` when the counts are unchanged.

### Event completion coverage

Accepted H14/H16 extension; a limited slice is implemented locally and has not
been deployed or published. The target extends `coverage` with
`scope_kind='source_event'`, `scope_id=source_ref`, and nullable `event_id`.
Keep the existing `source` and `via` key columns. An unresolved canonical map
must not make the source event disappear. Year is null unless source evidence
supports it. Event and year aggregates must deduplicate shared requests and
must not sum overlapping transports or source-event populations as unique pages.

Source-event rows carry `enumeration_id`, `enumeration_snapshot_ids`,
`enumeration_complete`, and `listed_pages`, `acquired_pages`,
`interpreted_pages`, `represented_pages`, `unavailable_pages`, and
`unsupported_pages`. Each page count refers to the same pinned enumeration;
page counts are not round counts. Incomplete enumeration counts describe only
known links, with unknown total discovery coverage. Unavailable and unsupported
outcomes account for gaps and do not inflate success counts. A prior retained
interpretation can remain usable while a newer interpretation is blocked;
state which evidence supports each count under the release cutoff.

`represented_pages` counts enumerated pages supporting selected result facts
in the acknowledged release. It does not establish that every field or identity
from those pages was published. Existing identity counts, `scope_reasons`, and
`missing_scopes` retain that distinction. Mapping status is explicit and never
inferred from a nonzero acquired count. A fully acquired event can still have
unsupported interpretation, withheld identities, or no published results.

Pin the enumeration with the release's support inventory. Parent changes after
the cutoff belong to the next release. Local reports may show newer progress
beside the last acknowledged release, but cannot advance public completion
without a publication receipt. An archived parent, failed fetch, admitted
retirement, or unresolved alias cannot masquerade as complete sheet coverage.
This extension uses the existing coverage table, not a competing public dataset.

The current local implementation pins enumeration membership and its parent
support into the release dependency manifest, semantic fingerprint, and closure
proof. It emits `listed_pages`, `selected_interpreted_pages` (the selected
support subset), and `represented_pages` after result suppression. A shared
bounded verifier can also establish acquired and interpreted totals for the
pinned enumeration. Each total remains null unless every member is assessed;
`acquisition_unknown_pages` and `interpretation_unknown_pages` count unassessed
members. Capture currently examines at most 32 distinct requests across the
witness under one cumulative budget. Unsupported classifications, unknown
pagination, and legacy denominators remain explicit gaps.

Version-two local witnesses also count explicit unavailable-origin observations.
Any usable origin or archive acquisition takes precedence. Otherwise the bounded
search must cover every matching snapshot under the cutoff, and the latest
origin response must be `Gone` or `ExpectedUnavailable` with an error status and
a verified retained body. `Gone` requires HTTP 404 or 410. Conflicting latest
outcomes, incomplete searches, invalid timestamps, or unverifiable supporting
bodies leave the count null. Archive failures alone cannot establish origin
unavailability. Stored expected-unavailable classifications retain their
historical meaning; current watch success history does not rewrite them.

Zero means no qualifying unavailable observation among the fully assessed
members. It does not claim those pages were available. The exact response
metadata and body remain dependencies at every validation boundary. Older
version-one witnesses remain valid with unavailable counts unknown.

Version-three local witnesses add an explicit unsupported source-interpretation
observation. A generation must record a failed `critical_unknown` guard and a
critical field with disposition `unknown`, a path, and a reason. Its current
contract version, immutable generation fingerprint, request identity, full
manifest, retained bodies and extracts must verify. Revoked evidence cannot
supply the observation. A valid retained interpretation takes precedence;
an incomplete candidate search or unverifiable evidence keeps the count null.
The disposition currently requires a single-member manifest: an aggregate report
without per-request attribution cannot label every member unsupported.
Generic parse failures, missing parsers and unreviewed page kinds do not supply
positive unsupported evidence.

This classification adds no successful stage operation. `acquired_pages` may
still include the same page because ordinary raw-body verification independently
proved acquisition; `interpreted_pages` does not include the unsupported outcome.
The gap can account for a known obligation without satisfying interpretation,
retirement or publication. Zero means no qualifying critical-unknown observation
in the fully assessed retained domain. It is not a claim that every layout or
canonical scoring method is supported. Projection-only numeric/Solo exclusions
remain contest findings alongside valid source interpretation, including pages
with mixed supported and unsupported contests. Broader explicit page dispositions
remain unfinished. Version-one and version-two witnesses retain unknown
unsupported counts. Positive unsupported proofs retain their exact generation,
contract and artifact dependencies at every release validation boundary.

Local observations pin exact support and their capture and validation policy.
`usable_verified_at` records the artifact check time separately from the source
cutoff. Later evidence does not rewrite an old observation. Positive artifacts
are checked again at every validation boundary, including cached completion,
candidate reuse, and publication. These local stage counts neither select
output nor establish published completion. Doctor reads published coverage only from a verified,
acknowledged baseline with a matching closure receipt. Overlapping transport
rows are not added together.
## Recorded source-event history and retirement

Whole source-event retirement requires more than removing its listed pages.
An admitted replacement must have watch removal authority, omit a previously
declared event, and withdraw its predecessor's known page obligations. A bounded
check of the complete retained accepted source domain must also disprove every
independent event declaration. Empty declared groups remain declarations.
Missing evidence, pending enumeration bootstrap, an exhausted domain check or
an unproven immediate withdrawal edge leave whole-event retirement unknown.

Schema 27 stores immutable source-event retirement receipts separately from
current verification proofs. A changed source domain, admission journal,
enumeration or policy invalidates the current claim; the historical receipt
remains. Reverification of the same withdrawal creates no additional retirement
transition. Retirement never records successful acquisition, interpretation or
acknowledged publication. Sources whose retained domain exceeds the verification
budget remain unassessed, rather than receiving a sampled absence claim.

Fleet catalog pages report `current_state` and page-scoped `state_counts` as
`locally_accounted`, `waiting`, `explicitly_retired`, or `unassessed`. The existing
accounting assessment stays separate. `locally_accounted` can include supported
gap classifications; it does not assert full interpretation or publication.
Historical `reopened` transitions stay in immutable accounting receipts.

`swingset.schedule.event_history.report` returns bounded pages of recorded
accounting, enumeration, page-retirement or source-event-retirement history.
Pin the returned `through` value on the first page, then pass `next_cursor` as
`after`. Each page contains at most 100 records and counts only those records.
`reached_high_water` means that pinned recorded stream has been read through;
it does not assert complete lifetime history. The first recorded time, unknown
legacy history, and unobserved transitions are explicit. This metadata-only
report neither opens artifacts nor performs network requests or mutations.
Pagination cursors belong to the same retained database and its physical
backup/restore lineage; do not reuse them after rebuilding tables or other
maintenance that changes SQLite row IDs.

```sh
python -m swingset.schedule.event_history --state /var/lib/swingset --source eepro --source-ref eepro:test --stream accounting --limit 100
```

The history streams are `accounting`, `enumerations`, `page_retirement` and
`source_event_retirement`. Ordinary doctor fleet refreshes remain bounded
catalog samples; requesting history is a separate paginated read. Missing older
schemas return unsupported history, rather than manufacturing empty progress.

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
