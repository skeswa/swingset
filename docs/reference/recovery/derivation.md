# Tracking inputs and rebuilding affected results

A saved result is reusable only while its inputs still apply. These rules identify each output and the evidence it depends on.

[Overview](../recovery.md) · [Current status](../../status.md)

## 5. Derivation: recipes and dependency sets

Compute recipe identity from the built runtime artifact and captured policy,
schema, configuration, and dependency inputs. Human version numbers remain
labels, but forgetting a bump must not leave old results active. Start with
conservative replay on a changed runtime artifact. Narrow invalidation to
separately fingerprinted modules only once dependency declarations and
clean-rebuild comparisons prove that optimization safe.

Record dependency sets at scope granularity: source events to canonical events,
registry evidence and candidates to links, links to derived joins, and all of
these to releases. Include removed and previous mappings. Candidate indexes are
an optimization, not the complete dependency set: a newly seen dancer must
reconsider previously unmatched entries even if no candidate relationship
existed before. Retain broad relinking as the safe fallback.

New evidence can reduce certainty. A contradicted ID invalidates derived points
and participation joins; a corrected alias removes obsolete mappings. Retained
evidence grows, but accepted links need not grow monotonically.
