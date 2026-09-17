# Identity linking

Linking connects a result entry or judge to a WSDC registry dancer. This page owns the evidence and correction rules. Start with [identity explained](../how-it-works/identity.md); the historical policy change is recorded in [D-0002](../../journal/decisions/0002-confirmed-public-identities.md).

[Reference index](README.md)

## On this page

- [Name normalization](#name-normalization)
- [Candidate generation](#candidate-generation)
- [Scoring and methods](#scoring-and-methods)
- [Link status](#link-status)
- [Retroactive correction](#retroactive-correction)
- [Newcomers and first points](#newcomers-and-first-points)
- [Implementation and inspection](#implementation-and-inspection)
- [Work ownership](#work-ownership)
- [Durable decisions and resolution (H8–H9)](#durable-decisions-and-resolution-h8h9)
- [Offline reviewed evaluation (H17)](#offline-reviewed-evaluation-h17)

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

Before normalization, explicit `and`, `&`, and `/` separators identify a
paired name. A source layout with established individual role ownership can
split that cell; otherwise retain the pair and abstain with reason
`paired_name_ownership_unresolved`. A couple or unsplit paired name cannot
receive one person's identity, including through source IDs or overrides.
Compound given names, surname particles, apostrophes, and hyphens alone do
not identify a pair.

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
current weights are hand-set in `link/weights.toml`. Offline fitting with
Splink is a future step, using reviewed scoring.dance evidence; the runtime
does not fit weights. Recorded candidate evidence includes signals that do
not currently contribute to the weighted score.

Signals:

| Signal                | Effect                                                                                                                                                                                                 |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Name similarity       | main signal                                                                                                                                                                                            |
| Name rarity           | recorded for review; does not currently affect the score                                                                                                                                               |
| Division consistency  | the loaded registry required/allowed levels for that role must allow the contest's division; an incompatible level sharply reduces the score. Historical event-date levels are not reconstructed here. |
| Role consistency      | registry primary role matches entry role; weaker signal because dancers switch                                                                                                                         |
| Recency               | dancer has registry activity within 3 years of the event                                                                                                                                               |
| Geography             | not currently computed; stored as unavailable                                                                                                                                                          |
| Bib reuse             | the same bib within one contest and role shares an assignment; recorded on the selected candidate, without a score weight                                                                              |
| Registry confirmation | one exact normalized-name identity matches the same event, role, division, style, and numeric place or finalist result `F`; decisive                                                                   |
| Source-provided id    | scoring.dance `data-wsdc`; decisive                                                                                                                                                                    |

Assignment runs separately for each contest and role: one WSDC id is
assigned to at most one bib in that scope, and one bib to at most one WSDC id.
The same bib in another contest is a separate assignment group. We solve with
`scipy.optimize.linear_sum_assignment` on `-log(score)`, then reject
assignments below threshold.

An unrestricted or unspecified contest division (`none`, `open`, or absent)
provides no eligibility evidence. Missing registry levels also provide no
eligibility evidence; neither is a level-zero mismatch. Known incompatible
skill divisions retain the score penalty. Judges with unknown role omit the
role weight from the score denominator and numerator, just as unavailable
division evidence omits its weight. The global acceptance threshold stays
unchanged.

For judges, fixing unavailable-role weighting cannot make a single-token name,
a different surname, or conflicting given-name tokens acceptable. Those
name-only candidates keep all their signals but score at most 0.89. Matching
first and last names may omit middle tokens in order, and curated first-name
nicknames remain available. This protects the retained Tze Ming/Tze Yi Wee,
Emily Huang/Emily Hung, and Camille Lange/Camille Lane comparisons without
granting any new default joins. Independent source evidence and human review
remain distinct in the acceptance packet.

A named judge may have no WSDC number. Keep that judge's source identity and
null `wsdc_id`; absence of a registry match is not evidence that the judge
record is invalid. Do not invent a number or force a match to fill the column.

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
`confirmed`. This publication safeguard applies before H3 to H5 scoring
corrections can expand joins; it does not complete H10's publication barrier
or correction-only release work. Consumers who want more recall, or a
different threshold, use `link_candidates`, which keeps every scored
candidate with every signal. Nothing the linker computed is discarded.

## Retroactive correction

Links change over time by design:

1. Event weekend: name-based links (`probable`, `possible`).
2. When the registry posts results, about a week later according to the
   owner, finalists can get `confirmed` via `registry_placement`. This timing is an expectation,
   not a deadline. A `probable` link that the
   registry contradicts is superseded and the entry is re-scored.
3. Any time: an accepted journal decision changes the reviewed support or
   restriction. A `same_person` decision can produce a `manual` link only
   when no active decision or source evidence contradicts it. A hold requires
   explicit supersession; a later timestamp or score never lifts it.
4. Registry merges remove merged numbers from the scoring pool. A future
   merge-rewrite path must use the same decision resolver; it cannot silently
   transfer a rejected pair to a surviving number.
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

## Implementation and inspection

The public operation is `link_event` in
[`service.py`](../../src/swingset/link/service.py). Its implementation has three phases:

| Phase                  | Owner                                                      | Result                                                                                   |
| ---------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Load retained evidence | [`evidence.py`](../../src/swingset/link/evidence.py)       | `LinkingSnapshot`: event evidence, rules, selected derivation, and journal token         |
| Resolve identities     | [`resolution.py`](../../src/swingset/link/resolution.py)   | `EventResolution`: conclusions, candidate assessments, review restrictions, and findings |
| Commit current results | [`persistence.py`](../../src/swingset/link/persistence.py) | Stored links and candidates, history, dependent updates, and completed work              |

`resolve_event(evidence, rules)` is deterministic and has no database or clock
argument. Read `_conclude` for the final evidence precedence: unresolved person
ownership, subject hold, reviewed positive, permitted unique printed identity,
unique permitted registry confirmation among generated candidates, then scored
assignment or unmatched. Cross-subject strong-claim conflicts are checked before
assignment. Restricted candidates remain available with their scores and signals;
they still contribute to the existing ambiguity comparison.

The [model](../../src/swingset/link/model.py) distinguishes `ConfirmedIdentity`,
`TentativeIdentity`, `UnmatchedIdentity`, and `WithheldIdentity`. Only the first
provides `SubjectResolution.default_wsdc_id`. Tentative conclusions retain the
existing probable/possible/ambiguous statuses. Both unmatched and withheld
conclusions store `unmatched`; inspect `conclusion.reasons`, `review`, and
`findings` to distinguish absent matches from prevented joins. History reason
precedence remains unchanged, including a hold on an already paired subject.

The shared deterministic journal policy lives in
[`policy.py`](../../src/swingset/link/policy.py). `DecisionResolver` in
[`decisions.py`](../../src/swingset/link/decisions.py) loads that policy and reads
durable source continuity for linking and publication. The commit rechecks both
the selected derivation and journal token. A stale standalone call returns
`False` with work still queued; a delegated call raises `SupersededWorkError`.
An unchanged successful standalone call also returns `False`, preserving the
existing public interface.

Placement points and confirmation watches live in
[`effects.py`](../../src/swingset/link/effects.py) and still update inside the
same transaction as identities, findings, history, and work completion.
Offline `evaluation*` modules prepare human review artifacts; they do not run
as part of event resolution. See [D-0042](../../journal/decisions/0042-separate-link-evidence-resolution-and-persistence.md)
for the design choice and validation links.

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

## Durable decisions and resolution (H8–H9)

`overrides/identity_overrides.csv` is the append-only journal defined in
[self-healing](recovery/identity.md#the-decision-journal). Capture validates syntax;
acceptance rejects removed or altered accepted decision IDs. Acceptance commits
new decisions, the selected journal digest, its generation, and conservative
relinking of every current and previously bound event together. Cross-reference
supersession requires a recorded, unique approved migration. The repository's
legacy file had zero rows; the conversion tool retains every nonempty legacy row
and its original provenance when used on another state.

`python -m swingset.state.identity_journal convert --state STATE --input OLD.csv
--output JOURNAL.csv` reads state without accepting decisions. Exact source
mappings become legacy positives or holds; absent and ambiguous mappings become
`insufficient_evidence` with the old entry ID and all available locators. Repeating
conversion is byte-identical. Operator review and normal captured-input acceptance
remain separate from conversion.

`ReferenceReader` locates entries and judges in retained observations. Its keys
include the original source event, source sheet, table, and a role-scoped bib or
original row/column position. Canonical event remapping does not change these
keys. Contest grouping and explicit split-bib ownership use projector rules.
A mixed-person cell alone does not establish either individual's ownership.
A baseline record can resolve its original locator without a current canonical
entry. Missing locators remain unavailable; names are never guessed into IDs.

Source-reference bindings and reviewed migrations are retained independently of
canonical rows. Changed row semantics, missing previous locators, ambiguous
splits, or an orphaned decision after unexplained renumbering withhold affected
joins. Explicit unique continuity carries decisions to the new locator and lets
old baseline locators see decisions subsequently reviewed there. Ambiguous
continuity cannot authorize a positive. Same-locator replacement requires
reviewed evidence naming the accepted semantic hash.

`DecisionResolver` returns the journal token, applicable decision and migration
IDs, policy version, reviewed positive, restricted pairs, subject holds, and
contradictions. Every manual, printed-ID, registry-placement, scoring, assignment,
and bib-reuse path consults that result. Conflicting strong claims for one dancer
on distinct bibs in the same contest and role also withhold. A negative pair does
not reject other candidates; pair-level insufficient evidence opens review
without asserting that the people differ. Candidate scores and signals remain
available even when the decision forbids selecting them. Named judges without
registry numbers remain named, unmatched, and fully usable in marks.

Each committed resolution records its token and cause. Accepted, revoked,
unresolved, and superseded assertions remain in append-only history, including
assertions removed by canonical reprojection. Linking rechecks the journal token
inside its write transaction. A concurrent acceptance rolls back obsolete output
and leaves required relinking queued. Checkpoint tests restore the journal,
accepted input and artifacts, and reference migrations exactly; an unaccepted
captured bundle remains unselected and is excluded from the checkpoint closure.

H10 owns the publication-time token check and correction-only release behavior.
H8–H9 do not authorize unreviewed default-join expansion.

## Offline reviewed evaluation (H17)

`python -m swingset.link.evaluation` samples a settled local state and evaluates
externally supplied adjudications. It makes no requests, changes no links, and
writes new local JSON artifacts. These mechanics do not complete H17's human
review gate. Reviewed public precision remains unavailable until a reviewed
sample names the published population it represents.

```sh
uv run python -m swingset.link.evaluation sample \
  --state /path/to/local-state --output /path/to/sample.json \
  --seed review-2026-09 --cohort local-identity-2026-09 \
  --cutoff 2026-09-12T18:00:00Z --per-stratum 10
uv run python -m swingset.link.evaluation evaluate \
  --sample /path/to/sample.json --reviews /path/to/adjudications.json \
  --output /path/to/evaluation.json
```

The population includes locally selected, confirmed default joins for entries
and judges, and unresolved entries even when they have no candidates. It is
explicitly a local population, not an assertion about a published release.
Strata separate accepted and unresolved streams, source, subject kind,
historical versus latest observed subject-year events (future listings without
subjects are excluded), and observed risk flags: common registry
names, unrestricted divisions, paired names, role switching, and first-point
eligible finals. The last flag means eligibility, not a reviewed claim that the
person earned their first point. Known failure fixtures remain a separate
regression stream and cannot be sampled as representative population evidence.

Each artifact records cohort, seed, method version, cutoff, population digest,
stratum denominators and selected sizes, accepted input digests, state revisions,
source locators, snapshot/body references, and the complete current candidate
IDs. A content digest binds each sampled subject to its frozen evidence.
Evidence fetched after the stated cutoff is refused. Missing source references
stay visible; the tool does not manufacture H8 references from canonical IDs.

Sampling ranks SHA-256 hashes within each stratum and takes the requested size
without replacement. Connected events and known or possible people share a
split: shared event IDs, accepted or candidate WSDC IDs, and matching names
connect the subjects. Tuning and evaluation use separate groups; the default
fraction is one half. Unknown aliases can still leak across groups and need
review. A highly connected corpus can occupy only one split; the artifact
reports its group count and the evaluator leaves absent cohorts unavailable.
Do not try different seeds to select an easier evaluation set.

Prospective sampling accepts repeatable `--prior-tuning-packet PATH` inputs
(`draw_sample(..., prior_tuning_packets=[packet, ...])`). Every supplied packet
must pass its content digest check. Schema 2 packets retain the event, name,
subject, and accepted/candidate ID keys of the entire tuning population,
including unsampled subjects and inherited prior exposure. A current component
touching any of those keys stays wholly in tuning. A bridge through another
current subject also excludes the full component; no component is cut to fill
the evaluation split. Previously frozen packets and their assignments remain
unchanged.

The manifest records prior packet digests, cohorts and cutoffs, excluded
components and their causes, and eligible/excluded denominators for each
stratum before split assignment. Excluded subjects remain in the full
population and tuning denominators. Evaluation reports carry these records.
Legacy packets without full-population exposure cannot establish disjointness:
supplying one makes prospective held-out eligibility unavailable and zero.
That uncertainty propagates into later packets. Zero eligible subjects does
not trigger another seed or manufacture an evaluation cohort.

This safeguard covers explicitly supplied prior tuning packets. It cannot
certify that omitted tuning data or unknown aliases are disjoint. Declare the
target population, cutoff, policy and sampling design before outcomes; supply
all relevant tuning exposure. A restricted eligible cohort represents that
stated population, not automatically a published release. These changes prepare
future samples; they do not relabel the frozen September 2026 tuning packet,
create reviewed labels, or establish held-out precision.

Adjudications are a JSON list. Each row supplies `sample_id`,
`sample_fingerprint`, `decision`, `reviewer`, timezone-aware `reviewed_at`,
`method`, and an `evidence` list of independently reviewed references. Accepted
links use `correct`, `incorrect`, or `insufficient_evidence`; a `correct` review
also supplies the selected `reference_wsdc_id`. Unresolved entries use
`matched`, `no_registry_identity`, or `insufficient_evidence`. A `matched`
review supplies its `reference_wsdc_id`. Decisive unresolved reviews must set
`candidate_search_complete` to true after looking beyond the offered candidates.
A reviewed identity absent from the frozen pool counts as a missing candidate.
Source-provided IDs and heuristic scores are inputs to review, not automatic
adjudications. Duplicate, unknown, stale, unattributed, or contradictory positive
reviews are rejected.

The report gives each stratum's population, sample size, reviewed and missing
counts, inconclusive reviews, false accepted links, resolvable abstentions, and
missing candidates. Accepted-link precision and a 95% Wilson interval appear
only when every selected subject in that stratum has a decisive review. The
interval uses an approximate binomial model without finite-population
correction. Partial review counts stay visible, but precision is unavailable.
There is no pooled population estimate: unequal stratum sampling fractions
cannot be averaged as if the sample were uniform. Reports include the sampling
method, review methods, sample digest, and review-input digest. Evaluation
records never alter the decision journal or authorize new default joins.

To review the sample without navigating its full JSON, create a portable local
packet. This reads retained artifacts and writes a new output directory; it
makes no requests or pipeline changes:

```sh
uv run python -m swingset.link.evaluation_packet \
  --sample /path/to/sample.json --state /path/to/settled-state \
  --output /path/to/review-packet
```

Open `review-packet/index.html`. Each selected subject has readable source and
registry evidence, frozen provenance, its split, and a blank review form.
Retained bodies and extracts are verified by digest before copying. HTML
bodies are served as plain text; PDFs retain their format. Missing or corrupt
artifacts are visible. The packet includes the unchanged sample, a blank JSON
review template, and an unavailable evaluation report. Form entries remain in
the browser until the reviewer downloads `adjudications.json`; no answers are
preselected. Evaluate that downloaded file with the command above. A packet is
review material, not evidence that a human review occurred.
