# Parsing

A parser reads a saved page and reports what the source says. This page defines its inputs and outputs, failure behavior, and the checks required before its interpretation can replace earlier evidence.

[Reference index](README.md)

## On this page

- [Contract](#contract)
- [Version changes and failures](#version-changes-and-failures)
- [Source interface](#source-interface)
- [Page kinds and observations](#page-kinds-and-observations)
- [Parsing rules](#parsing-rules)
- [Fixtures and tests](#fixtures-and-tests)
- [Source admission (H6 and H7)](#source-admission-h6-and-h7)

## Contract

Each page kind has two pure functions:

```python
def extract(body: bytes) -> Extract
def parse(extract: Extract, ctx: ParseContext) -> ParseResult
```

`extract` turns bytes into the page's content in the source's own
vocabulary, with no context: for an EEPro round, the contests with
their header rows and cells; for a DCN page, the evaluated `results`
subtree; for a WDR file, the decoded JSON. It is the only code that
reads the body. Its result is a plain, canonically serializable value
(sorted keys, no floats as text), and `sha256(canonical(extract))` is
the fingerprint used by the fetch layer. Because the fingerprint hashes
everything `parse` consumes, attributes
included, every source-content change visible to parse is visible to
fetch. Context and implementation-version changes are handled separately
by [invalidation](state.md#invalidation).

`parse` attaches provenance and produces observations in source
vocabulary. `ParseContext` carries `snapshot_id`, `watch_id`, `url`,
`source`, `kind`, optional `source_ref`, and `fetched_at`. It has no
canonical event id. `ParseResult` carries observations, watch specs, and
warnings. The transactional writer stores these and enqueues projection
work; neither pure function performs I/O.

The newest successfully parsed snapshot for a watch owns its current
observations. Re-parsing that snapshot replaces the set; successfully
parsing an older snapshot cannot replace a newer current set. Compare
`(observed_at, snapshot_id)` for a deterministic order, where
`observed_at` is the capture time of an archived body and the fetch
time otherwise. Historical reparse
updates its diagnostic and version records even when it does not own the
current observations. Failures preserve the watch's last-good set.

Order at fetch time: fetch, `extract`, compare fingerprint. Unchanged:
discard the body, record `checked_at`. Changed: archive the body, store
the extract as a derived blob, then `parse` later from the derived
blob. `extract` raising `ExtractError` (missing table, unknown column,
payload that does not evaluate) is treated as changed: the body is
archived with `extract_status = failed` so it can be diagnosed, the
watch keeps its previous fingerprint, and the run counts the failure.
Neither function does I/O.

## Version changes and failures

Each page kind has an `EXTRACT_VERSION` and a `PARSER_VERSION`. Snapshots
record the attempted pair; current observations retain the versions that
produced them. Accepting either version change enqueues every archived
snapshot of that kind in the same transaction as the accepted version.
An extractor bump re-extracts before parsing and invalidates the cached
watch fingerprint's version; a parser bump can use the stored extract.
After re-extraction, only the watch's current snapshot can update its
cached content fingerprint, and only at the current extractor version.
An older archived body must never replace that cache.

A successful parse commits observations when eligible, warning findings,
new watch specs, downstream work, attempted versions, and work completion
together. A handled extract or parse failure records diagnostic evidence
and attempted versions, leaves last-good observations unchanged, and
completes that attempt. It is not retried indefinitely by checking
`parse_status != ok`. A version bump or `swingset reparse` explicitly
enqueues another attempt; a new fetch creates its own snapshot work.
Unexpected errors roll back and leave work pending. Failure counts and
run failure thresholds remain as specified in [operations](operations.md#observability).

## Source interface

`PageKind` declares `kind`, `EXTRACT_VERSION`, `PARSER_VERSION`,
`change_mode` (`validators`, `body_hash`, or `extract`), the two pure
functions above, and `expected_statuses(watch) -> frozenset[int]`.
[Fetching](fetching.md#response-classification) interprets those expected
statuses before changing host state.

`Source` declares its name, hosts, page-kind mapping,
`seed_watches(config, overrides) -> list[WatchSpec]`, and
`policy(watch, event, host_config, now) -> Policy`. Event context is
optional for index and registry watches. Watch specs describe method,
URL, canonicalized form data, source reference, and page kind. The
scheduler persists and deduplicates them; parsers do not write watches.

Observation payloads are frozen dataclasses with an explicit kind and
source scope. The stored representation is versioned JSON, validated
and decoded at the storage boundary. Projectors receive typed payloads;
JSON decoding and casts do not spread into projection or linking. Scope
kinds are a closed union of source event, dancer, source index, and
calendar, with the identifiers in [architecture](architecture.md#observations-and-projections).

## Page kinds and observations

[Implementation plan](../../journal/archive/v1-implementation-plan.md) owns build order. This
inventory names parser output, before canonical projection.

| Page kind                                     | Input                    | Observations and watch specs                                         |
| --------------------------------------------- | ------------------------ | -------------------------------------------------------------------- |
| `wsdc_calendar.events`                        | print list HTML          | `CalendarRow`s                                                       |
| `wsdc_registry.dancer`                        | `/lookup2020/find` JSON  | One `DancerLookup`, including registry placements or a verified miss |
| `eepro.index`                                 | `event.php`              | `SourceEventRow`s and event watch specs                              |
| `eepro.autoindex`                             | Apache directory listing | File metadata and round watch specs                                  |
| `eepro.round`                                 | round HTML               | `RoundSheet` with printed entries, judges, marks, and placements     |
| `scoringdance.sitemap`, `scoringdance.recent` | sitemap or recent list   | Source event references and watch specs                              |
| `scoringdance.event`                          | results index            | Event sheet and round watch specs                                    |
| `scoringdance.round`                          | round HTML               | Round sheet, including printed WSDC ids                              |
| `wdr.rounds`, `wdr.awards`                    | `routeInfo.json`         | Round and award sheets                                               |
| `dcn.list` (later)                            | upcoming / archive HTML  | Source event rows                                                    |
| `dcn.event_results` (later)                   | results tab HTML         | Event sheet and PDF watch specs                                      |
| `dcn.round_pdf` (later)                       | roundscores PDF          | Round sheet with printed bibs, judges, and marks                     |

## Parsing rules

- HTML is parsed with `selectolax` (fast, lenient). Tables are read by
  header text, never by column position alone, because judge counts vary.
- Judge columns are identified by header text or `title` attribute.
  Preserve anonymous labels such as "Judge 1" so projection can set
  `anonymous = true`.
- Observations keep marks as the source printed them (`Y`, `A1`, `10`, `4.5`,
  `1`, `2.1`) in `mark_raw`. Projection adds the normalized enum and
  numeric value. Both legends (10/4.5/4.3/4.2/0 and 1/2.1/2.2/2.3/3) map to the same
  enum: `yes`, `alt1`, `alt2`, `alt3`, `no`.
- Names are stored exactly as printed in `name_raw`. Normalization is a
  separate step.
- Explicit paired names require individual ownership evidence before a
  single-person identity can attach. Existing supported Jack and Jill
  layouts split the printed pair using their established role order. Other
  layouts retain a couple, the original name, and a `paired_name` finding
  with reason `paired_name_ownership_unresolved`; punctuation normalization
  must not erase that boundary. A retained pair is not an individual subject.
- An unrestricted or unspecified contest division remains `none` or
  `open`; it is not inferred to be Newcomer or another skill level. Identity
  scoring treats that division evidence as unavailable.
- The DCN Nuxt payload is evaluated by a `node` subprocess from nixpkgs
  running a small fixed script (`sources/dcn/nuxt_eval.js`) that defines
  `window`, evaluates the payload, and prints JSON to stdout. The
  subprocess gets the payload on stdin, no arguments, no network, no
  filesystem beyond the script, and a 5-second timeout. The Python side
  is a pure function `evaluate_nuxt(body: bytes) -> dict` so the engine
  can be swapped without touching the parser.
- A contest whose layout the parser cannot interpret is still an
  observation with an unsupported-layout warning. Projection emits
  a `contests` row with `parse_status = unsupported` and no rounds,
  so coverage gaps remain visible.
- PDFs are read with `pdfplumber`. Tables are located by the "Result"
  header and judge legend. Each PDF parser fixture must include at least
  one prelims and one finals sheet.
- The writer rejects unexplained empty output from a watch that
  previously produced observations. A source must explicitly classify
  legitimate emptiness, such as a verified registry miss; pure parsers
  do not query prior state to decide this.

## Fixtures and tests

Every parser has a `fixtures/` folder with real archived bodies (trimmed
of nothing, so the test is honest) and the expected records as JSON.
Fixture bodies come from our own archive so tests never hit the network.
`pytest` runs them all. A parser change that alters expected output must
update the fixture in the same commit.

Fixtures are committed, not downloaded during tests. If a suppression
request names a person in a fixture, re-record it from a different event
and update its expected output; the runbook covers this case. Research
verification keeping headers only does not change the fixture contract.

Newsletter parsing preserves PDF layout columns and wrapped event names. The
captured Volume 6 issue yields 37 sidebar listings and four dated New Events
approvals. Unrecovered colour semantics emit `newsletter_colour_unverified`;
sidebar rows retain `newsletter_status_colour_unverified`, with the registry
fallback defined by backfill. Quarter-only approvals remain review findings,
unknown layouts do not authorize empty replacement, and malformed printed
dates fail with a stable reason. Other newsletter layouts remain unverified.

## Source admission (H6 and H7)

`ParseResult.interpretation` carries pure field accounting and coverage beside
observations. Contracted adapters use `sources/interpretation.py`; the evidence
layer independently checks archived structure, body and extract hashes, ordered
input slots, versions, and pagination. Contracts live in `admission/`, with
registry and round accounting separated from policy and persistence.

Every relevant field is handled, excluded with a reason, or unknown. Critical
unknowns block selection. Noncritical unknowns remain in the report. Source row
counts are checked against interpreted rows; listed child identifiers are
checked independently of whether child pages have been acquired. Ragged,
role-specific judge panels are retained without filling a rectangular matrix.
An empty output or `legitimate_empty` flag supplies no terminal witness.

Contract 4 covers registry lookups, EEPro indexes and round sheets,
scoring.dance indexes and round sheets, and WDR rounds. Other page kinds remain
explicitly unassessed. Verified empty presentation columns and source aggregate
columns have explicit exclusions. `RoundSheet.scoring_method_raw` retains the
literal EEPro `Avg` header or WDR `roundSubHeader`; it does not add a canonical
scoring method. EEPro numeric contests, including finals, WDR `Average Raw Scores`,
and WDR's explicit `Solo` category are marked unsupported with a finding and
emit no canonical results. Their raw judge scores, averages, medals, and rows
remain evidence. WDR `#` is a source-order ordinal, not a placement. Unknown
methods, typed cells, and callback/mark codes still block. WDR `S<n>` outcomes
and generic paired-finals bib ownership remain excluded, unverified findings;
their retained documents receive no removal authority.

EEPro autoindex uses contract 5. Its independent body witness enumerates raw
DOM file hrefs, including PDFs, and compares their count and order with
FileRows and HTML child watches. Truncated visible labels cannot hide a file.
The extractor derives file names from href paths. Other page kinds remain on
contract 4; a new autoindex review cannot silently approve another contract.

Blocked-admission finding summaries include stable reason codes from the
stored generation report and latest selection decision. Staging-only guards
such as `non_authoritative_row_loss` remain visible. Private exception text
and paths are excluded from those public summaries.
WDR updates also require unchanged witnesses for all previously selected raw
rows, including duplicate counts and column context. This is checked during
staging and again inside selection: retaining old observations alone would
not prevent a newer panel from omitting their projected facts. Changed or
missing rows require explicit review or revocation of the old evidence.

The ordinary parser stages immutable evidence before replacement. Policies
default to `shadow`: reports add no admission pointer or removal authority and
the existing parse behavior continues. `paused` retains staged evidence without
promotion. `enforce` requires a recorded external review of that exact contract
and corpus digest, including a passing control. A report alone cannot activate
a policy. A contract change creates new reports and a new version; old failed
reports remain unchanged.

The admission transaction checks the desired input fingerprint, previous
accepted generation, current snapshot and recipe, intact complete manifest,
and pending-work token. It commits observation replacement, both old and new
scope invalidations, the accepted pointer, and token-specific completion
together. A stale snapshot can complete only its own obsolete work; newer
snapshot or requeued-input work remains pending. An interrupted transaction
exposes the old complete generation; replay after commit is idempotent.

EEPro and scoring.dance round documents may retire only claims owned by their
watch. Registry lookups, WDR rounds, and historical event discovery grant no
deletion authority. Their observations
remain by snapshot; the latest present registry claim corrects the same native
key while omitted historical placements and prior found identities survive.
Archived index captures use `watch_id/snapshot_id` units so a later capture
cannot retire another year's evidence. Explicit revocation withdraws proven
inadmissible claims and restores an assessed predecessor when available.

The read-only review command is:

```
python -m journal.tools.admission.assess_source_admission --state RETAINED_STATE \
  --state PHASE1_STATE --output NEW_REVIEW_DIRECTORY --cutoff UTC_TIME
```

It writes every report, a digest receipt, and an HTML index linking exact
example bodies and extracts. Shared snapshots are assessed once with both
state roots recorded. It does not migrate, fetch, promote, or fabricate review.
After an external review, `record_corpus_review` verifies the supplied digest;
`activate_contract` enables that page kind and queues retained parse work.
`pause_contract` preserves the last accepted output. Manual-extraction callers
must supply their reviewed and observed sentinel to `evaluate`; an absent or
changed acknowledgment cannot promote the candidate.
