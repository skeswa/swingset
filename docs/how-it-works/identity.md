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

## Go deeper

- [Identity linking](../reference/identity-linking.md): evidence, scoring, and corrections.
- [Data corrections guide](../guides/data-corrections.md): review and removal tasks.
- [Identity policy decision](../../journal/decisions/0002-confirmed-public-identities.md): historical basis and tradeoff.
- Code: [linking service](../../src/swingset/link/service.py) and [decision resolver](../../src/swingset/link/decisions.py).
