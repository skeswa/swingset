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
The writer leaves the set alone only when every row is equal, snapshot id and
extract and parser versions included. An equal payload under a new snapshot
or version label is rewritten and its scopes re-projected, because release
support matches those labels against the accepted generation (D-0241). A
parse that repeats its accepted generation writes nothing, so rows stored
before D-0241 keep their old labels until `swingset relabel --apply` stores
the accepted result again ([operation](../guides/operation.md#reparse-and-registry-work)).

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
A kind's code scopes act the same way without a label bump
([D-0207](../../journal/decisions/0207-scope-code-recipes-by-import-closure.md)):
a change in `recipe/code/extract/<kind>` counts as an extractor bump, and one
in `recipe/code/parse/<kind>` as a parser bump. A snapshot re-extracts when it
has no extract, its `EXTRACT_VERSION` changed, or its
`extract_recipe_sha256` differs from the extract scope's digest; a successful
extract stores that digest. A stored extract is key-sorted JSON, so a kind
whose parse output follows the body's key order (`ORDERED_EXTRACT`: the
registry and both WDR kinds) always extracts from the body; otherwise a
re-parse from the stored extract differed from the accepted generation and
never cut off (about 23% of registry snapshots on the 16:10 copy). A
`recipe/env` change re-parses every snapshot.
Every parse scope also holds the parse writers (`recipe_scope.PARSE_WRITERS`:
admission select and support, observations, verification, findings,
`history/acquisition`, `intent`, `origin_dispatch`, `fetch/wayback`), so an
edit there re-parses every kind. So do the shared contract core
(`admission/contracts.py`, `accounting.py`); a kind's own contract module
(`admission/kinds.py` names it) re-parses only the kinds it lists
([D-0261](../../journal/decisions/0261-dispatch-tables-are-in-no-scope-and-each-kind-names-its-contract.md)).
Other code re-queues no snapshot; see
[state](state.md#derivation-generations-h15) for how scopes are built. A
blocked parse attempt still re-opens on any package change
(`version/repository` is in the parse retry fingerprint), so a fix deployed
anywhere reaches the attempts it blocked without re-parsing the rest. A
retry that fails with the same message is a waiting repeat, not a failure
of the tick ([scheduling](scheduling.md); D-0249).
After re-extraction, only the watch's current snapshot can update its
cached content fingerprint, and only at the current extractor version.
An older archived body must never replace that cache.

A successful parse commits observations when eligible, warning findings,
new watch specs, downstream work, attempted versions, and work completion
together. A handled extract or parse failure records diagnostic evidence
and attempted versions, leaves last-good observations unchanged, and
completes that attempt. It is not retried by checking `parse_status != ok`:
it waits for input on the work queue's clock, a day doubling to a week, and
is then retried ([state: work queue](state.md#work-queue)). A version bump or
`swingset reparse` enqueues another attempt at once; a new fetch creates its
own snapshot work.
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

[`sources/registry.py`](../../src/swingset/sources/registry.py) names each
registered source's adapter module (`ADAPTERS`) and each page kind's owner
(`KIND_OWNERS`). `get_page_kind` imports only the owner, so one kind's parse
code does not import other sources. A new page kind needs a row there; a test
checks the table against every adapter's `page_kinds`. DCN is not registered.

| Page kind                                     | Input                    | Observations and watch specs                                                            |
| --------------------------------------------- | ------------------------ | --------------------------------------------------------------------------------------- |
| `wsdc_calendar.events`                        | print list HTML          | `CalendarRow`s                                                                          |
| `wsdc_registry.dancer`                        | `/lookup2020/find` JSON  | One `DancerLookup`, including registry placements or a verified miss                    |
| `eepro.index`                                 | `event.php`              | `SourceEventRow`s and event watch specs                                                 |
| `eepro.autoindex`                             | Apache directory listing | File metadata and round watch specs                                                     |
| `eepro.round`                                 | round HTML               | `RoundSheet` with printed entries, judges, marks, and placements                        |
| `scoringdance.sitemap`, `scoringdance.recent` | sitemap or recent list   | Source event references and watch specs                                                 |
| `scoringdance.event`                          | results index            | Event sheet and round watch specs                                                       |
| `scoringdance.round`                          | round HTML               | Round sheet, including printed WSDC ids                                                 |
| `wdr.rounds`, `wdr.awards`                    | `routeInfo.json`         | Round and award sheets                                                                  |
| `dcn.list` (offline preparation)              | reviewed archive HTML    | Raw index sheet; no watches or complete-year claim                                      |
| `dcn.event_metadata` (offline preparation)    | legacy Tapestry HTML     | Raw event metadata and unassessed results-tab locator                                   |
| `dcn.event_results` (later)                   | results tab HTML         | Event sheet and PDF watch specs                                                         |
| `dcn.round_pdf` (later)                       | roundscores PDF          | Round sheet with printed bibs, judges, and marks                                        |
| `swingdirector.round`                         | SwingDirector score PDF  | One raw sheet per printed title: judges, marks or ordinals, tallies, places; no watches |

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
- The implemented DCN `evaluate_nuxt(body: bytes) -> dict` boundary decodes
  the reviewed data-only Nuxt serialization in `sources/dcn/nuxt.py`. It accepts
  an immediately invoked function containing one returned literal tree and
  parameter references; it never executes JavaScript or starts a subprocess.
  Calls, property access, arithmetic, unknown statements, duplicate keys and
  mismatched arguments fail extraction. The observed `void 0` uses JSON
  serialization semantics: omit object members and retain array slots as null.
  Limits are 8 MiB HTML, 1 MiB script, depth 64, 50,000 nodes in both input and
  resolved output, and 1,024 parameters. Unknown serialization stays a retained
  parse failure; there is no fallback evaluation. This replaces the planned
  Node implementation technique under
  [D-0068](../../journal/decisions/0068-decode-dcn-source-data-without-running-scripts.md).
  Results and PDF kinds remain separately gated by real fixtures and review.
- A contest whose layout the parser cannot interpret is still an
  observation with an unsupported-layout warning. Projection emits
  a `contests` row with `parse_status = unsupported` and no rounds,
  so coverage gaps remain visible.
- PDFs are read with `pypdf`, the installed PDF library; `pdfplumber` is
  not a dependency. A layout is located by its printed headers and
  legend, and columns by text position where wrapping or rotated labels
  make line order unreliable. Each PDF parser fixture must include at least
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
and update its expected output; the runbook covers this case. A fixture
never carries a name its source hides elsewhere; such rows are redacted to
the source's own hidden text ([ethics](ethics-and-legal.md)). Research
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
observations. A contracted adapter marks its parse `@declared`
(`sources/interpretation.py`), and admission attaches the declaration where it
parses (`admission.contracts.declare`); the evidence layer independently checks
archived structure, body and extract hashes, ordered input slots, versions, and
pagination. Contracts live in `admission/`: one module per kind or kind family
(`admission/kinds.py` maps each kind to its module and version), separated
from policy and persistence.

Every relevant field is handled, excluded with a reason, or unknown. Critical
unknowns block selection. Noncritical unknowns remain in the report. Source row
counts are checked against interpreted rows; listed child identifiers are
checked independently of whether child pages have been acquired. Ragged,
role-specific judge panels are retained without filling a rectangular matrix.
An empty output or `legitimate_empty` flag supplies no terminal witness.

Contract 4 covers registry lookups, EEPro indexes and round sheets,
scoring.dance indexes and event pages. Contract 5 covers scoring.dance round
sheets and WDR rounds (see below). The WSDC calendar has its own contract 1
(see below), and so has `swingdirector.round` (`admission/swingdirector_contract.py`;
[long-tail guide](sources/long-tail.md)). Other page kinds remain explicitly
unassessed. Verified empty presentation columns and source aggregate
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
The extractor derives file names from href paths. Each kind's contract version
is its own; a new review of one kind cannot silently approve another.

scoring.dance round contract 5 (extract 5, D-0240) accounts for the columns
contract 4 left unknown. It only reaches a column contract 4 called unknown, so
an accepted round's accounting does not change.

| Column                                                                      | Contract 5                                                                                                                                              |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Blank column 2 with names, one result table                                 | The extractor labels it `Follower`, as in finals and Strictly pages. Two tables keep it blank and unknown: one stored page lists followers first there. |
| Blank, attribute-free column right after `Σ`, values like `+1` or `-0.2`    | Excluded: a chief-judge adjustment to the displayed sum. Any other value is unknown.                                                                    |
| Last column, blank header with class `text-center;`                         | A remark column. Each value is excluded by class (`CJA`, tie-break notes and scores, `VR pass`, and on placed rows `V1`/`VP` badges and review notes).  |
| Remark with DQ, DISS, disq, scratch, injury, withdrawal, fail or `SW`       | Excluded on a row the source shows as eliminated. On a row with a callback state or a placement it fails `source_remark_outcome_conflict`.              |
| Lowercase 1-3 letter header without a title, between Follower and Placement | Excluded: an anonymized per-page judge code with digit cells. No judge record is made; the placement is handled.                                        |

Any other remark value is unknown, so new remarks block. The conflict guard
keeps a round whose remark disqualifies a placed or called-back competitor
out of the dataset; publishing the displayed outcome could award a
disqualified dancer. The published `score_sum` stays the sum of individual
marks without the adjustment.

WDR rounds contract 5 (parser 4) accepts a Placement Order judge mark with
the legend's `|SVW` (Swing Violation - Warning, no placement change). `SV1`
to `SV3` move placements and stay unknown. The projector does not yet read
the mark under `|SVW`. Parser 4 writes `ResultTable.attribute_group` from a
key-sorted mapping, so the text no longer depends on whether the extract came
fresh from the body or from the sorted archive.

WSDC calendar contract 1 (`admission/wsdc_calendar_contract.py`, D-0257)
covers every layout `wsdc_calendar.events` reads. It ships in the `shadow`
policy until the history projection stops writing twins.

| Check                         | Contract 1                                                                                                                                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Listing count                 | Counted from the raw body: `event_name` cells and `tr_events_load` rows, `box` cards, FullCalendar `title` keys, numeric `addMarker` calls. It must equal dated rows plus `impossible_listing_date` findings. |
| Row fields, classes and types | The seven extract keys; classes `tr_events_load`, `event-trial`, `event-canceled`, `event-unconfirmed`; the printed types and the map's pin names. Anything else blocks.                                      |
| Dates                         | Every row has an ordered span. A known format printed backwards (`Feb 13 - 9, 2020`) is `impossible_listing_date`: that row is withheld. An unknown format blocks.                                            |
| Country                       | An alpha-3 code or none. A blank or `transparent` flag keeps none; an unknown flag is a noncritical finding.                                                                                                  |
| Empty listing                 | No terminal witness: `waiting_for_inputs`.                                                                                                                                                                    |
| Removal                       | `watch`. A live capture replaces the previous live capture's rows; an archived capture is its own unit and its rows survive a live capture on the same watch.                                                 |

The build matches an archived capture's rows under `calendar/wsdc-history`,
the scope the writer stores them in (`sources/phase_one.stored_scope_ref`).

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

A re-parse that repeats its kept generation is cut off at staging (D-0205).
The kept generation is the unit's accepted one, or, for a `shadow` kind with
no pointer, the undecided generation its desired fingerprint names. Cutoff
needs equal result, report and contract, a byte-equal manifest (labels,
snapshot ids, digests and times), a current snapshot and unchanged inputs, and
no revoked generation for the new fingerprint, directly or through an
equivalence. It writes a `source_equivalences` row and the desired
fingerprint, and returns the kept id; admission then returns `accepted` at
once. For an enforced kind, observations, revisions, watches from
`result.watches` and `interpreted` events are not rewritten, so a change in
what those writers produce for an unchanged result needs a `PARSER_VERSION`
bump or an explicit re-admit; a shadow kind rewrites its observations on every
parse. The snapshot row is still marked parsed (`parse_status='ok'`, versions,
time) when it is not already, before the registry re-reads it: a failed
attempt in between (a parser regression, then its fix) would otherwise leave
it `failed`, its registry checks unusable and its unit counted as withheld. An enforced unit whose newest generation is held in `needs_review` or
`waiting_for_inputs` keeps it too when admission would hold the repeat the
same way: the contract is unreviewed, or it is reviewed and the report's own
failures decide (D-0212). The parse reports that state with no generation or
decision; a parse failure repeats the same way. A changed result, a new snapshot or a version label bump still stages a
generation. Revoking a generation revokes its equivalent fingerprints too:
the same inputs parsed again are decided `revoked`. `[derivation] cutoff =
false` turns parse cutoff off with derivation cutoff.

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
`pause_contract` preserves the last accepted output. `swingset contract`
wraps the generation-cohort path for operators: `status`, `activate` (refused
for a kind whose code contract is `unassessed`, or a cohort staged under
another version) and `pause` ([operation](../guides/operation.md#activating-an-admission-contract)). Manual-extraction callers
must supply their reviewed and observed sentinel to `evaluate`; an absent or
changed acknowledgment cannot promote the candidate.
