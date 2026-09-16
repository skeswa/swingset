# Reliable curation of scraped datasets

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

## Assessment

Swingset collects West Coast Swing competition results and links entrants and
judges to World Swing Dance Council (WSDC) registry identifiers.

Swingset's proposed self-healing architecture is well aligned with documented
practice in maintained public-data projects. The strongest precedents support
archiving source evidence, separating extraction from curation, tracking
dependencies, retaining reversible identity decisions, and validating output
before release. OpenSanctions is the closest comparator for heterogeneous
scraping and consequential person matching; Our World in Data is particularly
relevant for reproducible public curation. OpenAlex and Wikidata illustrate
correction and identifier lifecycles. Their specific practices are examined below.

The architectural recommendation is to strengthen the existing proposal with
strict source interpretation, acceptance of bounded source generations, durable
negative identity decisions, explicit deletion evidence, and statistically
designed review of accepted output. These are recommendations for Swingset, not
claims that every comparator implements one universal self-healing architecture.

The evidence does not establish a turnkey system that makes arbitrary scraped
data eventually true. Current production documentation, established standards,
and recent research address different parts of that problem. Reliability should
therefore mean a supported recovery mechanism and measurable accuracy controls,
with explicit conditions and unresolved evidence.

The evidence horizon is September 12, 2026. Undated documentation is treated as
maintainer-described current behavior at that date, not an independently verified
operational guarantee. The 2026 papers discussed here are preprints. Foundational
standards and older production papers remain relevant but are labeled by date.

## Production comparators

| Comparator            | Relevant documented practice                                                         | Architectural application                                                        | Transfer limit                                                        |
| --------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| OpenSanctions / zavod | Source statements, guarded interpretation, dataset assertions, reversible resolution | Evidence-backed claims and source-specific acceptance                            | Its identity evidence and error costs differ from dance results.      |
| Our World in Data     | Archived inputs, distinct curation stages, dependency checksums                      | Offline replay and current-recipe materialization                                | Many inputs are published datasets rather than changing result pages. |
| OpenAlex              | Manifest-based synchronization, deletion handling, identifier lifecycle              | Reconcile complete snapshots as well as incremental changes                      | WSDC does not provide the same completeness or deletion interface.    |
| Wikidata              | Referenced statements, temporal qualifiers, deprecated assertions                    | Preserve corrections without continuing to publish known errors as current facts | Community statement ranking is not a numerical confidence model.      |
| Dagster               | Conditions on asset state and dependencies                                           | Reconciliation based on desired data state                                       | Automation defaults do not automatically revisit all history.         |

The table is an architectural comparison, not a performance ranking. The primary
documentation supporting each row follows.

### OpenSanctions: preserve the source assertion

OpenSanctions stores individual property statements with source attribution and
temporal information, then constructs consolidated entities. Its statement model
retains source-specific entity identity alongside the deduplicated identity.
This provides a concrete precedent for preserving what each source asserted
separately from the integrated record. [1: Statement model](https://www.opensanctions.org/docs/statements/)

For Swingset, a results page's printed WSDC number should remain a source claim.
The assertion that an entrant is a particular registry dancer is a separate
resolution result. Store links from the accepted assertion to all material
support and contradictions. A single winning snapshot on an entry is insufficient
when acceptance depends on an event mapping, a registry lookup, and a reviewed
name interpretation.

This does not require converting every cell into RDF. A small relational
assertion-support table for consequential joins and contested fields is a
reasonable first implementation. Existing archived observations can remain the
primary source record; normalize additional claim detail where correction and
explanation require it.

### OpenSanctions: a parser must account for its scope

Zavod's strict-interpretation guidance requires each in-scope source value to be
handled, deliberately excluded, or surfaced as a problem. It distinguishes
structured-record checks from HTML selector checks and scales failure severity
to whether continued processing is safe. The requirement concerns declared scope,
not every incidental element of a content-rich website.
[2: Strict interpretation](https://zavod.opensanctions.org/best_practices/strict_interpretation/)

Swingset should give each adapter an executable interpretation contract. Examples
include recognized contest categories, required date selectors, expected pagination,
and accounting for listed round URLs. An unexpected category affecting role or
points eligibility should invalidate that interpretation. A cosmetic element can
be explicitly excluded. An unexplained omission must not count as successful
coverage merely because the parser returned a well-typed object.

Zavod also documents export assertions for counts and property fill rates.
Minimum failures abort export; maximum failures warn. Its guidance says to use
healthy runs to establish bounds and investigate violations instead of adjusting
bounds around unexplained output. These are operational checks, not proof that
every emitted identity is correct. [3: Dataset assertions](https://zavod.opensanctions.org/metadata/)

For Swingset, add source-specific quality histories: extracted date coverage,
round-link counts, named-entry rates, supported-category rates, and duplicate
identifiers. Use exact invariants when justified and statistical guards elsewhere.
A valid retraction may reduce output; the gate should demand an explanation or
supporting evidence, not enforce permanently increasing row counts. Thresholds
and their rationale must be reviewed inputs, not values a repair worker silently
loosens to turn a failed run green.

### Discovery can remain automatic when extraction needs review

Zavod describes separating revision discovery from curated extraction. A crawler
can monitor a stable document identifier or relevant content hash, while a
reviewed local extraction carries the interpreted facts. Unknown revisions
produce review work. Its guidance explicitly distinguishes acknowledging changed
bytes from proving their correct interpretation and discourages noisy whole-page
sentinels when a smaller stable region exists.
[4: Change detection](https://zavod.opensanctions.org/best_practices/change_detection/)

This is a useful path for Swingset's long tail: rules documents, old PDF results,
and event pages whose layout is too irregular for reliable extraction. The system
can still self-detect that its curated interpretation is stale. The requirement
then becomes `needs_review`, with the changed source, prior extraction, and exact
review action attached. A newly discovered revision is not silently ignored and
does not need to be interpreted autonomously before it becomes actionable.

### Identity repair needs durable decisions and a lifecycle

Current OpenSanctions documentation describes automatic high-confidence merges,
LLM-assisted or human review of other candidates, manual cluster splits, and
reconciliation through historical identifiers. Collection and deduplication are
separate, so a newly acquired source record can precede its resolved identity.
These are current maintainer-described practices, not a claim that all matching
is manual. [5: Identifier lifecycle](https://www.opensanctions.org/docs/identifiers/)

The project's 2021 account explicitly describes recording positive and negative
matching judgments and making decisions reversible. That article is historical;
its manual-only workflow should not be presented as the current system.
[6: Original deduplication design](https://www.opensanctions.org/articles/2021-11-11-deduplication/)
The maintained nomenklatura README describes a SQL-backed resolver for matching
decisions, distinct from candidate generation and comparison.
[7: Resolver implementation](https://github.com/opensanctions/nomenklatura/blob/main/README.md)

Swingset should retain `same`, `different`, and `insufficient_evidence` decisions
with the relevant source subjects, evidence fingerprint, reviewer, and policy
version. A reviewed wrong match must remain excluded after an unrelated parser
or linker replay. A material evidence change can reopen the decision; it must
not silently discard it. If subject IDs move during an event remapping, the
review decision needs a stable source-subject anchor or an explicit migration.

This does not require minting new global person IDs. WSDC numbers can remain the
published identity namespace. The new records identify assertions and decisions.
If a registry merge is later supported, record its relationship explicitly;
an old ID returning not found does not identify its replacement.

### Our World in Data: curation is a distinct, reproducible stage

OWID's ETL separates upstream snapshots, format normalization, curated datasets,
and publication-oriented outputs. The documented workflow explicitly places
harmonization and substantive processing in the curation stage rather than the
initial format conversion. [8: ETL workflow](https://docs.owid.io/projects/etl/architecture/workflow/)

Its design calls for archived upstream inputs, declared dependencies, and output
that is a function of those inputs. Checksums determine which datasets need
rebuilding. It is designed as a standalone Python workflow rather than requiring
special infrastructure. [9: Reproducibility and checksums](https://docs.owid.io/projects/etl/architecture/design/features-constraints/)

These precedents reinforce Swingset's current archive/project/link separation
and the proposed desired-versus-materialized fingerprints. They do not justify
copying a particular checksum algorithm or adopting a new orchestration stack.
Keep explicit runtime and policy dependencies and compare incremental output with
clean replay. Separate evidence verification from semantic change so repeated
successful source checks do not cause unnecessary full relinking.

OWID's contributor guide also asks for assertions, sanity checks, and comparison
with earlier versions for abrupt changes during data curation.
[10: Curation guidance](https://docs.owid.io/projects/etl/guides/data-work/add-data/)
For Swingset, source fidelity and canonical correctness should have different
acceptance checks. Preserving every printed value can be correct extraction
while treating a paired name as an individual remains incorrect curation.

### OpenAlex: incremental updates need deletion reconciliation

OpenAlex's current synchronization guide describes manifests, partitions by
update date, upserts, and reconciliation of deletions. It recommends checking
that the manifest remains unchanged during a snapshot download. The guide says
deleted or merged-away API IDs return 404 without a survivor redirect; explicit
deletion logs currently cover works, while other entity types require other
reconciliation methods. [11: Snapshot synchronization](https://help.openalex.org/access/sync/)

The important transfer is the need for both incremental updates and periodic
comparison against a complete authoritative set. The important limit is that
Swingset rarely receives such a set. A failed page fetch, incomplete index parse,
or finite run of unused registry numbers cannot be treated like a complete
OpenAlex snapshot.

Add explicit deletion authority to each source contract. Only an accepted,
complete bounded enumeration can support deletion by absence within that scope.
Otherwise record uncertainty, schedule a check, and retain historical evidence.
Distinguish a corrected placement, a delisted record, an unavailable page, and an
unresolved registry identity. Each has different consequences for historical
facts and current joins.

### Wikidata: prevent known errors from being reintroduced

Wikidata distinguishes preferred, normal, and deprecated statements. Its guidance
separates erroneous statements from correct historical facts, whose relevant
time should be expressed through qualifiers. Keeping a deprecated assertion can
explain why it should not be added again; ordinary query paths omit deprecated
statements unless explicitly requested. Rank is not a probability of truth.
[12: Statement ranking](https://www.wikidata.org/wiki/Help:Ranking)

Swingset should make this distinction explicit in its assertion lifecycle. A
past dancer name may remain valid historical evidence. An incorrect person join
should be revoked and excluded from default output. Keep the reason, evidence,
and superseding assertion where available. Source observation time, the time an
assertion concerns, and the time Swingset accepted or revoked it are separate;
unknown effective dates should remain unknown.

## Orchestration, freshness, and quality measurement

### Asset state is the right scheduling abstraction, with historical caveats

Dagster's declarative automation evaluates asset and dependency state to request
work. It distinguishes missing materializations from upstream updates and can
evaluate checks separately. Its documented defaults are narrower than full
historical reconciliation: eager automation generally considers the newest time
partition, and `on_missing` only considers partitions introduced after the
condition was enabled. [13: Declarative automation](https://docs.dagster.io/guides/automate/declarative-automation)

Swingset should adopt the state-based reasoning without assuming that an
orchestrator's defaults solve backfill. Requirements must be enumerable for all
retained event years. A durable full-scan cursor should revisit older scopes even
if no new source event is discovered, and a new dancer must reconsider prior
unmatched entries without depending on an existing candidate relationship.

The proposed fair host allocation, maximum eligible-work age, and isolated failed
work are Swingset-specific design recommendations. The cited automation interface
does not itself establish those guarantees. Demonstrate them under sustained
live polling and injected failures before calling them reliable.

### HTTP validation supports a separate freshness record

RFC 9111 explains how a successful 304 validation updates an appropriate stored
response for reuse. A full response, a validation failure, and reuse of an
existing representation are different cases. This supplies protocol grounding
for refreshing verification state without claiming the body changed.
[14: HTTP caching, sections 4.3.3–4.3.4](https://www.rfc-editor.org/rfc/rfc9111.html#section-4.3.3)

Swingset must add its own interpretation condition: the verified representation
must be intact and successfully interpreted under the required recipe. A 304 is
not proof that a parser understood it, nor that the source's facts are true.
This directly supports the registry freshness repair and avoids renewing trust
because a request merely returned a successful transport status.

### Provenance and quality have useful standard vocabularies

W3C PROV-DM models entities, activities, agents, derivation, and invalidation.
It is a 2013 Recommendation, useful here as a stable conceptual reference rather
than a requirement to adopt a graph database.
[15: PROV data model](https://www.w3.org/TR/prov-dm/)

Map an archived response or accepted assertion to an entity, a parse or review
to an activity, and the executable recipe or responsible reviewer to attribution.
Make the relationships queryable: a consumer or maintainer should be able to
answer which evidence and policy produced a join and which later action revoked
it. The relational representation can remain small and fit existing SQLite work.

W3C's Data Quality Vocabulary supplies concepts for quality dimensions, metrics,
measurements, and annotations. It describes how to communicate fitness for a
purpose rather than defining a single universal quality score. It is a 2016
Working Group Note, not an accuracy certification.
[16: Data Quality Vocabulary](https://www.w3.org/TR/vocab-dqv/)

Publish separate measurements for known-page acquisition, interpretation coverage,
mapping coverage, successful-verification age, withheld assertions, and reviewed
identity accuracy. Every ratio needs its population, time cutoff, and method.
An unknown event universe stays unknown; it must not be conflated with completion
of the URLs already discovered. This is especially important when users join
tables without reading the dataset card.

### Data tests are necessary but do not repair semantics

The Deequ production research describes declarative data constraints, custom
checks, incremental validation, and anomaly detection over quality metrics.
This is established production practice from SIGMOD 2019, not evidence of a new
2026 autonomous repair capability.
[17: Data unit tests](https://www.amazon.science/publications/unit-testing-data-with-deequ)

For Swingset, retain hard consistency checks and add histories of domain-aware
measurements. A parser can emit internally consistent but wrong roles. A linker
can create valid foreign keys to the wrong person. Checks must include source
counterexamples and evidence acceptance, not just null percentages and uniqueness.
Metrics should cause a bounded repair or review action; automatically satisfying
a schema by filling an unknown value is not a legitimate correction.

## Current research on entity matching and review

### Match scores require reviewed evaluation

Splink documents evaluation against clerical labels across thresholds, including
precision, recall, and false-positive/false-negative analysis. This is a concrete
tooling precedent for measuring threshold behavior instead of treating a score
such as 0.90 as a demonstrated probability.
[18: Evaluation from labels](https://moj-analytical-services.github.io/splink/charts/accuracy_analysis_from_labels_table.html)

Use three separate evaluation sets in Swingset. A probability sample of accepted
links estimates false acceptance. A sample of unresolved entrants investigates
missed matching opportunities. A deliberately enriched regression set preserves
known failures such as paired names, judges, unrestricted divisions, and delayed
first-point IDs. An enriched failure set cannot, by itself, estimate the error
rate of the published population.

For scale intuition, zero observed errors in independent representative Bernoulli
trials gives a one-sided 95% upper error bound of `1 - 0.05 ** (1/n)`: about 1%
for 300 observations and 0.1% for 3,000. This is a mathematical illustration, not
a measured Swingset accuracy estimate. Repeated appearances of one person are
correlated; cohort sampling, unequal selection probabilities, and uncertain
labels require an appropriate estimation design rather than blindly applying
this formula to entry rows.

### Recent LLM results support experiments, not automatic factual promotion

The August 25, 2026 revision of _OpenSanctions Pairs_ reports up to 98.95% F1 on
its pairwise benchmark. Its scope excludes end-to-end clustering. The corpus
includes 33.6% automatically propagated relational matches, evaluation sample
sizes vary, and the authors note uncertain labels and possible training-data
overlap. These limitations matter when interpreting the headline score.
[19: Pairwise benchmark, v2](https://arxiv.org/html/2603.11051v2)

**Assessment:** this supports evaluating model-assisted candidate review. It does
not establish accuracy for sparse dancer records or authorize replacing the
acceptance policy. A system can classify proposed pairs well while candidate
generation misses the correct dancer entirely. Pairwise success also says little
about whether an event-level assignment produces the right aggregate result.

Use models to draft evidence comparisons and review proposals, retain their
inputs, and evaluate on held-out Swingset cases. Permit abstention. Neither a
model's successful execution nor the absence of contradictory evidence establishes
an identity. Human decisions also need evidence and correction paths.

### Review budgets should reflect risk and sampling uncertainty

An August 2026 preprint by Lam and colleagues proposes sampling clerical review
across match score, comparison pattern, ambiguity, and demographic strata. Its
reported budget reduction trades away some estimation precision and
representativeness. This is early methodological evidence; the abstract supports
the design tradeoff, not a validated deployment prescription for Swingset.
[20: Stratified review proposal](https://arxiv.org/abs/2608.01401v1)

Prioritize review using both likely error and consequence: a person assignment
reused across many contests may deserve attention before an isolated optional
metadata field. Maintain a representative audit stream alongside risk-focused
review. Track `insufficient_evidence` rather than forcing binary judgments and
counting them as certain labels. Keep selection probabilities and denominators
so the audit can support an honest population estimate.

An April 2026 evaluation of six data-quality tools reports that the versions
examined use LLMs primarily for creating rules rather than directly validating
data. This is a dated, bounded study, not evidence about every vendor or every
September 2026 release. It reinforces the need to distinguish assistance with
checks from proof that automatic semantic repair works.
[21: Quality-tool evaluation](https://arxiv.org/html/2604.09163v1)

## Refinements to the Swingset design

The following requirements extend the [self-healing proposal](../../../docs/reference/recovery.md).
They are engineering recommendations informed by the preceding sources; exact
policies need implementation and acceptance evidence.

### A. Accept bounded source generations before inferring absence

A source generation is a finite acquisition/interpretation unit: one complete
paginated index, one event listing, or one round sheet. Record its manifest,
coverage evidence, interpretation recipe, and guard outcomes. Archive partial
or failed acquisitions, but do not let them masquerade as complete enumerations.

Promotion requires the unit's coverage and interpretation contract to pass.
Absence-based removal is permitted only inside that accepted scope and only when
the source contract says the enumeration is authoritative for that purpose.
Otherwise the status is unresolved or unavailable. This provides a precise test
for when an omitted record can be treated as a correction.

A last accepted generation may remain usable with stale status while a new one
is blocked. It cannot justify a claim already contradicted by stronger evidence.
Unrelated source scopes continue. Source acceptance and release consistency are
separate checks; this design does not weaken the existing requirement that all
published joins use compatible generations.

### B. Give source contracts ownership and review history

Each adapter declares scope, supported categories, coverage witnesses, stable
change sentinels, interpretation guards, deletion authority, freshness policy,
and a maintainer/runbook. Critical unknown categories block the affected scope;
noncritical unknowns open requirements with enough evidence for diagnosis.

Changes to assertions and source sentinels are reviewed policy changes. A repair
can produce a proposed patch and demonstrate output differences, but cannot
quietly accept a new hash, lower a minimum, or suppress a validator. The same
change should carry the evidence explaining why the new behavior is appropriate.

### C. Make correction memory explicit

Store source claims separately from accepted assertions. Retain supporting and
contradicting evidence, validity time where known, acceptance time, and revocation
time. Keep negative pair decisions and the evidence to which they apply.

Replaying identical evidence under unrelated code changes must not resurrect a
reviewed false link. Material new evidence may reopen a decision. An explicit
replacement decision must explain whether it supersedes the prior interpretation
or reflects a later change in the source. A reviewed correction is not just an
ephemeral deletion from today's canonical table.

### D. Keep data recovery and semantic adjudication distinct

Automatic actions include scheduled verification, bounded refetch, digest-verified
restore, replay under an accepted recipe, and dependency reconciliation. These
actions recover or recompute evidence-backed state.

Unknown categories, disputed aliases, ambiguous people, and conflicting source
claims may require an accepted rule or reviewed decision. The requirement remains
durable and ages toward escalation. A model may help prepare that decision;
successful execution of the model is not its acceptance condition.

### E. Audit accepted output and the gaps outside candidate generation

Add reviewed samples of published person joins and source facts to release
quality work. Keep person/event cohorts separate enough to detect leakage and
correlation. Inspect noncandidate examples when estimating missed identities;
evaluating only generated candidate pairs cannot measure failures in blocking.

Keep quality results versioned with the sampled release, sampling method,
selection probability where applicable, reviewer outcome, and unresolved-label
count. A higher linked percentage is not a sufficient deployment criterion.

### F. Verify recovery as a measurable behavior

Each automated requirement class needs a supported remedy, a postcondition, a
retry/blocked policy, a service-age objective, and an acceptance scenario. Measure
time to detect, time to recover, recurrence, and outstanding age separately.
Queue length and HTTP success remain operational metrics, not the outcome.

Test that a successful unchanged response advances freshness; a malformed new
source generation does not delete old data; an explicit correction revokes
dependent joins; deleted work is recreated; and historical scans revisit an event
whose missing identity becomes resolvable long after its intensive polling window.

## Concrete implementation shape

The existing SQLite design can hold the additional durable records without a
new distributed system. The following are logical records, not a final migration
schema:

| Record              | Minimum purpose                                                                  |
| ------------------- | -------------------------------------------------------------------------------- |
| Source check        | Attempt outcome and successful verification of a representation.                 |
| Source generation   | Bounded input manifest, completeness evidence, recipe, acceptance state.         |
| Requirement         | Desired condition, current gap, next action, retry time, and resolution witness. |
| Materialization     | Scope input fingerprint and the generation actually produced.                    |
| Assertion support   | Evidence and policy behind an accepted or withheld consequential claim.          |
| Review decision     | Same/different/insufficient judgment with evidence and supersession history.     |
| Quality measurement | Metric, population, method, release cutoff, result, and uncertainty.             |

Keep the interfaces small. Acquisition records verified evidence; reconciliation
determines unmet conditions; derivation produces candidate state; resolution
decides which assertions are supported; publication validates and exports a
consistent selection. Failed attempts change attempt state, not the definition
of completion. A worker cannot mark a requirement satisfied for an obsolete
input generation.

## Adoption order and acceptance examples

| Priority                              | Deliverable                                                 | Specific acceptance example                                                                                                                    |
| ------------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Immediate                             | Registry freshness and known identity counterexamples       | Two identical valid probe results advance the cursor; the judge-score repair does not automatically authorize name-only factual joins.         |
| First architectural change            | Source contracts and accepted bounded generations           | Remove a required date selector or a pagination page in a fixture; the affected generation is rejected and absence is not treated as deletion. |
| In parallel, observation-only         | Requirement inventory and historical reconciliation scan    | Delete a synthetic work record; the next scan restores it from the unmet condition.                                                            |
| Next                                  | Durable review decisions and assertion revocation           | Reject a mixed-person match, replay unrelated changes, and confirm the rejected link stays excluded.                                           |
| Next                                  | Fair repair scheduling and recipe fingerprints              | Continuous current-event activity cannot starve first acquisitions; code changes cannot leave supposedly current output under an older recipe. |
| Before broadening identity acceptance | Representative accuracy review and shadow comparisons       | Evaluate accepted joins, missed candidates, and difficult cohorts separately; retain abstention and quantify sampling limits.                  |
| Release hardening                     | Coverage measurements and compatible-generation publication | An omitted scope appears in coverage; all retained joins resolve inside the same accepted release selection.                                   |

This ordering moves source-loss detection and correction memory earlier than a
large orchestration rewrite. The most useful near-term architecture is a small
set of enforceable contracts around known failure modes, followed by broader
automated repair as each remedy is demonstrated.

## Evidence limits and design judgment

Documentation establishes that a project describes or supports a mechanism;
it does not establish its measured error rate or uninterrupted operation. Several
comparators rely on manual curation. Their domain-specific identifiers, source
authority, data volume, and false-match costs differ from Swingset's.

The research supports strong provenance, guarded acceptance, explicit correction,
state-based orchestration, and independent quality evaluation. It does not provide
a published end-to-end benchmark comparing self-healing accuracy across these
projects, nor establish the right polling shares, freshness windows, or identity
thresholds for WSDC data. Those values require shadow operation, source constraints,
and reviewed Swingset evidence.

A successful architecture should converge when supported evidence and rules
stabilize, keep unresolved claims visible, and avoid reintroducing corrected
errors. An unobtainable result or ambiguous person can remain unknown without
violating that contract. Filling the field without support would violate it.

## Sources

All online documentation below was consulted on September 12, 2026. Undated
pages are not assigned an invented publication date. Numbers identify the inline
source notes; the [source inventory](../../evidence/quality/curated-datasets-2026-09-12/sources.json)
records type, version/date, and principal applicability limits.

1. OpenSanctions. [Statement data model](https://www.opensanctions.org/docs/statements/). Maintainer documentation, undated.
2. OpenSanctions / zavod. [Strict interpretation](https://zavod.opensanctions.org/best_practices/strict_interpretation/). Maintainer documentation, undated.
3. OpenSanctions / zavod. [Dataset metadata](https://zavod.opensanctions.org/metadata/). Maintainer documentation, undated; assertions and validators sections.
4. OpenSanctions / zavod. [Change detection](https://zavod.opensanctions.org/best_practices/change_detection/). Maintainer documentation, undated.
5. OpenSanctions. [Identifiers and deduplication](https://www.opensanctions.org/docs/identifiers/). Maintainer documentation, undated.
6. OpenSanctions. [How we deduplicate companies and people across data sources](https://www.opensanctions.org/articles/2021-11-11-deduplication/). November 11, 2021; historical account.
7. OpenSanctions. [nomenklatura README](https://github.com/opensanctions/nomenklatura/blob/main/README.md). Maintained project documentation, undated.
8. Our World in Data. [ETL steps](https://docs.owid.io/projects/etl/architecture/workflow/). Maintainer documentation, undated.
9. Our World in Data. [Features constraints](https://docs.owid.io/projects/etl/architecture/design/features-constraints/). Maintainer documentation, undated.
10. Our World in Data. [New data](https://docs.owid.io/projects/etl/guides/data-work/add-data/). Maintainer guide, undated.
11. OpenAlex. [Sync](https://help.openalex.org/access/sync/). Current help documentation, undated; supersedes assumptions based on the legacy layout and merge-ID feeds.
12. Wikidata contributors. [Help:Ranking](https://www.wikidata.org/wiki/Help:Ranking). Revision shown August 11, 2026.
13. Dagster Labs. [Declarative Automation](https://docs.dagster.io/guides/automate/declarative-automation). Documentation shown for version 1.13.22.
14. R. Fielding, M. Nottingham, J. Reschke. [RFC 9111: HTTP Caching](https://www.rfc-editor.org/rfc/rfc9111.html). June 2022.
15. L. Moreau and P. Missier, editors. [PROV-DM](https://www.w3.org/TR/prov-dm/). W3C Recommendation, April 30, 2013.
16. W3C Data on the Web Best Practices Working Group. [Data Quality Vocabulary](https://www.w3.org/TR/vocab-dqv/). Working Group Note, 2016.
17. S. Schelter and colleagues. [Unit testing data with Deequ](https://www.amazon.science/publications/unit-testing-data-with-deequ). SIGMOD 2019.
18. UK Ministry of Justice / Splink contributors. [Accuracy analysis from labels](https://moj-analytical-services.github.io/splink/charts/accuracy_analysis_from_labels_table.html). Maintainer documentation, undated.
19. C. Smith, M. Sesodia, F. Lindenberg, C. Schroeder de Witt. [OpenSanctions Pairs: A Large-Scale Dataset for Pairwise Entity Matching](https://arxiv.org/html/2603.11051v2). arXiv preprint, version 2, August 25, 2026. Version-specific HTML supplies the revised title and author list.
20. J. Lam and colleagues. [Designing Ambiguity-Aware Clerical Review](https://arxiv.org/abs/2608.01401v1). arXiv preprint, August 2, 2026; abstract-level methodological assessment.
21. T. Rehberger, T. Hütter, L. Ehrlinger, W. Wöß. [Evaluating Data Quality Tools: Measurement Capabilities and LLM Integration](https://arxiv.org/html/2604.09163v1). arXiv preprint, April 10, 2026.
