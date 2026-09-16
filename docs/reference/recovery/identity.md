# Preserving reviewed identity decisions

Person matches need explicit support. These rules keep reviewed corrections in force through rebuilding and publication.

[Overview](../recovery.md) · [Current status](../../status.md)

## 6. Accuracy: resolve links before populating default joins

**Change to published joins.** Default `wsdc_id` columns on `entries` and
`judges` are populated only for links accepted under the versioned acceptance
policy with sufficient evidence and no unresolved disqualifying contradiction.
`probable` links no longer populate them; they stay in `link_candidates` with
every signal, where consumers who want more recall already look. This will
reduce linked coverage at first. That reduction exposes existing uncertainty
and keeps unsupported links out of analyses. Names need no fabricated person ID
to retain results and judge marks.

Deepen identity acceptance into a resolution module whose interface returns
accepted links, unresolved candidates, and contradictions with their evidence
and policy version. Candidate generation and scoring remain internal. A link
can be accepted, revoked, or superseded; record why and when.

### The decision journal

`overrides/identity_overrides.csv` becomes the decision journal. It is already
a captured input with a content hash under
[invalidation](../state.md#invalidation); it gains columns and an append-only
rule. Columns: `decision_id`, the source reference (`source`, `source_event`,
`contest`, `round`, `participant`), `wsdc_id` or `NONE`, `decision`, `evidence`
(snapshot or fixture references), `reason`, `author`, `date`, and `supersedes`
(a prior `decision_id` or empty).

| Decision                | Meaning and effect                                                                                               |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `same_person`           | Reviewed support for one subject and candidate pair; still subject to suppression and unresolved contradictions. |
| `different_person`      | Reject this pair; exclude it from accepted joins even if a later linking recipe ranks it first.                  |
| `insufficient_evidence` | Abstain on the specified pair or subject pending review; this is not proof that two people differ.               |
| `hold_unlinked`         | Keep the subject's default ID null until an explicit superseding decision; used for existing `NONE` rows.        |

Input acceptance rejects a bundle whose journal removes or alters an existing
`decision_id`; a decision changes only through a new row that supersedes it.
Existing rows are converted once, in the repository, by a reviewed script:
a WSDC ID becomes a legacy `same_person` decision, `NONE` becomes
`hold_unlinked`, and each `entry_id` is mapped to its source reference. Rows
that cannot be mapped become `insufficient_evidence` decisions naming the
unmapped `entry_id`, and their joins are withheld until reviewed. The
conversion is idempotent and does not invent evidence or treat a legacy label
as newly verified truth. The journal digest is captured in link inputs and
release manifests and is backed up with the archive. A clean rebuild loads it
before resolving identities.

A bib is unique only within its declared scope and role. Names and canonical
`entry_id` values are not stable keys. Where the source lacks durable IDs,
retain the original locator and evidence and require an explicit migration when
continuity is ambiguous. Event remapping preserves the source reference.
Splits, merges, renumbering, and ambiguous source-row replacement require
recorded reference migrations; unresolved migrations withhold affected joins
rather than dropping old decisions. This adapts the durable decision model in
[nomenklatura](https://github.com/opensanctions/nomenklatura/blob/main/README.md)
and the correction memory illustrated by
[Wikidata](https://www.wikidata.org/wiki/Help:Ranking).

Every acceptance path, including a directly printed ID and a manual positive
decision, consults applicable decisions and contradictions. Suppression remains
the final publication veto. Conflicting active positive and negative decisions
open review and leave the join null; neither the latest timestamp nor the
largest score resolves that conflict. Pair rejections do not imply rejections
of unrelated candidates, and insufficient evidence is not a transitive
cannot-link constraint.

Freshness-only checks and unrelated recipe changes do not erase decisions.
Relevant new semantic evidence can open reconsideration, but the old
restriction stays effective until a reviewed supersession resolves it. A review
deadline schedules review; it does not turn a rejected pair into an accepted
one. Ambiguous identity repair therefore depends on review, and the convergence
guarantee includes those recorded review inputs. Accepting a new journal digest
and its downstream invalidations is one input-acceptance transaction, as for
any captured file. Recompute the link and all dependent joins, including
historical scopes, and retain accepted, revoked, and superseded link history
with the decision IDs that caused each transition. Publication rechecks the
journal digest before every release; see
[correction releases](publication.md#correction-releases).

### Claims, evidence, and acceptance

For consequential links, retain all material support and contradictions, not
just a winning snapshot. Separate the time a claim concerns, when its evidence
was observed, and when Swingset accepted or revoked it. Do not substitute
retrieval time for an unknown historical effective date. These are relational
records; adopting the [PROV concepts](https://www.w3.org/TR/prov-dm/) does not
require RDF.

Distinguish "the results source printed ID X," "the registry recognizes ID X,"
and "the evidence supports this entrant being person X." A registry lookup that
finds an ID does not verify the entrant. Source mistakes, reused bibs, paired
names, and contradictory role or placement evidence are considered before the
identity join is accepted.

- Keep direct source facts with their provenance and interpretation status.
- Preserve raw source IDs separately from accepted IDs, including rejected or
  unverified claims and their reasons.
- Model unavailable signals as unavailable. A judge's unknown dance role is not
  a role mismatch; an unrestricted division is not a low skill level.
- Treat current heuristic scores as ranking scores, not calibrated
  probabilities. A threshold alone never promotes a score to a default join.

During migration, withhold known-invalid identity cohorts as soon as they are
identified. Run new acceptance rules against stored evidence in shadow mode
before expanding accepted joins. Fixing the judge score ceiling is not, by
itself, evidence that the newly higher-scoring identities are correct.

### Reviewed evaluation

Maintain a reviewed identity evaluation set covering judges, common names,
unrestricted divisions, role switching, paired names, first-point dancers, and
historical events. Validate source-provided labels before treating them as
truth. Separate tuning and evaluation across people and events to reduce
leakage. Report false accepted links and abstentions by cohort, with sample
sizes and uncertainty; do not pick a precision target without enough evidence.
Monitor input-shape and cohort shifts after deployment.

Maintain three review streams: representative accepted-link samples,
unresolved-entry samples that can expose missing candidates, and enriched known
failure cases. Record sampling design and uncertainty; a difficult-case set
does not estimate population precision. Review-budget methodology remains an
active research topic; the
[2026 stratified-review preprint](https://arxiv.org/abs/2608.01401v1) supports
explicit tradeoffs rather than a universal sampling prescription.

Model-assisted review or extraction changes first produce proposals with stored
evidence and abstention. Expanding automatic acceptance requires Swingset
evaluation, not a benchmark score from another domain.

Apply independent accuracy checks to parsing and event mapping as well: review
samples against archived source pages, compare independently obtained result
totals where available, and retain counterexample fixtures. A clean rebuild can
repeat the same parser bug, so rebuild equivalence alone cannot certify
accuracy.
