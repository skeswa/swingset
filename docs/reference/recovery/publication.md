# Publishing a dataset with consistent supporting evidence

A release must account for the evidence behind its rows. These rules define publication checks, correction releases, and visible coverage gaps.

[Overview](../recovery.md) · [Current status](../../status.md)

## 7. Publication: coherent evidence, safe defaults, visible degradation

Preserve atomic publication and immutable candidates. Add a release validator
that checks evidence support and required generations, as well as hashes,
schema, referential integrity, and point consistency.

A release pins an evidence cutoff, interpretation and acceptance recipes,
selected scope generations, and coverage inventory. Every join must resolve
against compatible selected generations. New evidence arriving after the cutoff
belongs to the next release. Unrelated continuous collection need not prevent a
coherent release: a newer source generation alone does not invalidate a
candidate whose selected generations remain compatible and supported. Changed
correction inputs or revoked support still require the safety checks below. The
rollout tests incompatible selections and harmless post-cutoff arrivals
separately.

Initially retain a conservative barrier for affected derivations while allowing
collection to continue. Introduce partial-scope releases only after dependency
closure checks exist. Never remove the global barrier without its replacement:
otherwise an old link can be published against a new event or dancer
generation. An alias move includes both old and new scopes in one consistency
group.

### Correction releases

Every proposed default identity is checked against the current decision
journal, suppression inputs, and acceptance policy. Pin those digests in the
release candidate and recheck them immediately before the atomic Hub update; if
they changed, rebuild the candidate. A completed old build cannot republish a
revoked join, and a last-good copy is not an acceptable fallback for a known
wrong identity. Serialize journal acceptance with the publication commit
boundary. An uncertain network outcome uses the existing publication receipt
reconciliation; decisions accepted after that boundary go into the next
correction release.

For a blocked scope, retain prior facts only if their support remains
admissible under the release policy, marked with their actual age. If support
is revoked, null the affected links or exclude the unsupported scope and its
dependent rows, preserving referential integrity and recording the omission.

Do not let an unrelated stuck parse delay removal of a known wrong published
join. Provide a correction-only build from a pinned published baseline: apply
current revocations and suppressions, null or omit affected links, and
recompute or omit every dependent value. Until dependency closure exists,
rebuild all identity-dependent outputs from that baseline and withhold outputs
whose support cannot be checked. Validate the resulting foreign keys, evidence
selection, and correction log. This build admits no new source generation and
does not permit arbitrary mixed-generation releases.

Record detection-to-corrected-release latency. Previously downloaded releases
remain unchanged, with their correction discoverable in the new release
history. Public correction records respect suppression and never expose private
review notes or suppressed identifiers.

### Coverage as data

Publish coverage and freshness as data, not just prose in the card. For each
supported source, year, and event scope, distinguish discovered, acquired,
interpreted, mapped, resolved, withheld, and unavailable evidence. State
denominators; an unknown discovery universe is not 100% coverage. Include scope
status and evidence time on commonly used tables so an omitted join and a stale
fact are visible.

Coverage regressions require an explanation, not a universal row-count gate:
legitimate retractions and corrections can reduce rows. Reject unsupported
links and inconsistent releases, while permitting disclosed incomplete
coverage. Publish link revocations in the changelog. Update health and
freshness at a bounded cadence and at material status changes even if fact
bytes are quiet; this changes the current quiet-publication rule in
[publishing](../publishing.md#commit-strategy).

Represent each quality result with a metric, population, method, evidence
cutoff, and uncertainty where estimated. Keep acquisition, interpretation,
linkage, and reviewed accuracy separate. The
[W3C Data Quality Vocabulary](https://www.w3.org/TR/vocab-dqv/) provides
conceptual guidance; these measurements can be ordinary Parquet tables.
