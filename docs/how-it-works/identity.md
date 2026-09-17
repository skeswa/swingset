# Connecting a result to a person

A name or bib number alone may not identify a registry dancer. Two people can
share a name, and bib numbers are reused. Swingset keeps candidate matches
separate from the confirmed identities used in the published result tables.

## What counts as evidence?

A result page might print a WSDC number. A registry placement might confirm a
result. A person reviewing the evidence might confirm or reject a match.
Name similarity helps find candidates, but it is not proof by itself.

For the fictional bib 42 example, a plausible name match can remain in the
candidate table while the entry's published WSDC number stays empty. The
[identity reference](../reference/identity-linking.md#link-status) owns the exact rules.

## Corrections must survive rebuilding

Reviewed identity decisions are stored in an append-only data file. Appending
a new decision preserves the history of earlier decisions. Rebuilding results
must respect those corrections instead of recreating a rejected match.

This _identity decision journal_ is application data in
`overrides/identity_overrides.csv`. The [project journal](../../journal/README.md)
contains human-readable research and engineering decisions. They have different jobs.

## How the code reaches a conclusion

`link_event` has three phases:

1. Load the subjects, retained source evidence, registry facts, and reviewed
   decisions. Remember which input versions were used.
2. Resolve the whole event in memory. Apply restrictions, detect competing
   identity claims, score candidates, assign within each contest and role, and
   choose a conclusion for each subject.
3. Check that the inputs are still current and commit the resolutions, history,
   findings, placement updates, and confirmation watches together.

A **reviewed decision** is human evidence in the identity journal. A
**resolution** is the linker's computed answer from that evidence and the other
facts. Each subject resolution contains its evidence, candidate assessments,
review restrictions, conclusion, and findings.

The conclusion is confirmed, tentative, unmatched, or withheld. Withheld means
that a hold, contradiction, or unresolved ownership prevents the identity join.
Unmatched means that no identity was selected. Both retain the existing stored
status `unmatched`; their reasons distinguish them. A tentative conclusion can
have a perfect name score and still provide no default-join identity.

Start with `resolve_event` in [resolution.py](../../src/swingset/link/resolution.py)
for the workflow and `_conclude` there for evidence precedence. The
[result types](../../src/swingset/link/model.py) explain what the answer contains.
The resolver has no database or clock dependency, so tests can describe an event
with ordinary values and inspect its conclusions directly.

## Go deeper

- [Identity linking](../reference/identity-linking.md): evidence, scoring, and corrections.
- [Data corrections guide](../guides/data-corrections.md): review and removal tasks.
- [Identity policy decision](../../journal/decisions/0002-confirmed-public-identities.md): historical basis and tradeoff.
- Code: [public operation](../../src/swingset/link/service.py),
  [event resolution](../../src/swingset/link/resolution.py), and
  [shared review policy](../../src/swingset/link/policy.py).
