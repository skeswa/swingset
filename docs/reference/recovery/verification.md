# Checking whether source evidence is current

A request attempt, unchanged content, and a successful interpretation are different facts. These rules define which checks count as usable evidence.

[Overview](../recovery.md) · [Current status](../../status.md)

## 1. Verification: separate checking, content, and interpretation

Deepen the fetch, archive, and parse write path into an evidence module. Its
interface records a fetch attempt and a successful interpretation, then exposes
the best usable evidence for a watch under a specified interpretation recipe. It
owns the conditions under which freshness may advance.

| Fact                                 | Meaning                                                                                                    |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| Last attempted check                 | A request was attempted; errors may update this.                                                           |
| Last successful content verification | The source returned a recognized representation, or validated an intact cached one.                        |
| Content identity                     | Hash of the archived representation; unchanged checks reuse it.                                            |
| Last semantic change                 | The interpreted claims changed.                                                                            |
| Interpretation recipe                | Executable artifact, parser and extractor policy, and relevant dependencies used to interpret the content. |
| Last successful interpretation       | Which content was accepted under which recipe.                                                             |
| Source observation time              | When the source evidence describes the world; historical archive time differs from retrieval time.         |

A 304 renews verification only if its cached representation exists, passes its
hash check, and has a successful interpretation under the required recipe. A
recipe change can invalidate interpretation without another origin request. An
identical successful response renews freshness without replacing historical
claim provenance or relinking every event. An error never renews it. A
recognized not-found response is time-bounded negative evidence, not proof that
an identifier never existed or will never exist.

Keep verification records compact and local. Do not archive another full body
for every identical check. A public freshness summary can update at a bounded
cadence without rewriting fact tables on every poll. RFC 9111 grounds freshness
renewal for a validated cached representation; see
[HTTP validation](https://www.rfc-editor.org/rfc/rfc9111.html#section-4.3.3).

### Registry verification

Record each usable verification with its watch, check time, content hash,
interpretation recipe, and semantic outcome (`found` or recognized `not_found`).
Keep the snapshot that first supported an unchanged claim as its provenance.
`last_checked_at` and `dancers.registry_fetched_at` do not substitute for this
record.

| Result                                                                         | Freshness and follow-up                                                                                              |
| ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| Identical accepted 200                                                         | Renew verification; an active probe can consume this outcome without new observations.                               |
| 304 with intact cached content                                                 | Renew verification only when the cached content has a successful interpretation under the required recipe.           |
| Recognized registry not-found representation                                   | Record a dated negative lookup; advance the current probe and schedule a future recheck.                             |
| Generic 404, timeout, rate limit, malformed response, or failed interpretation | Record the attempt and failure; do not advance usable verification or infer an absent dancer.                        |
| New recipe with existing bytes                                                 | Reinterpret locally; preserve the actual content-check time rather than pretending the reparse contacted the source. |

A probe accepts an outcome only if its verification time meets the probe's
requested check time and its recipe is current. A recent failed attempt does
not satisfy that condition or defer the probe to the annual refresh. Select old
profiles by their latest usable verification so identical successful checks
rotate the refresh population. Negative rechecks have their own policy; they do
not inherit the annual interval because the response parsed.

Delayed identities are a requirement, not an evidence rule. The
[requirements table](requirements.md#3-requirements-one-inventory-for-gaps-findings-and-derivation-work)
keeps unresolved first-point results and source-printed IDs eligible after the
intensive confirmation window, and
[derivation](derivation.md#5-derivation-recipes-and-dependency-sets) reconsiders old subjects
when new registry evidence arrives.
