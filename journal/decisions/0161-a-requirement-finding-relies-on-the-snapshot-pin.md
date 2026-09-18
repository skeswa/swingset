# D-0161: A requirement finding relies on the snapshot pin, not on a declaration

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

`state/requirements.py` keeps writing its `findings` row with its own
`INSERT`, and declares no `finding_support_references` row. A requirement never
pins a file on its own. The two requirement kinds whose evidence carries a
digest are covered by something else:

- an `archive_artifact` requirement is about a `body_sha256` that came from
  `snapshots`, and the file closure keeps every `snapshots.body_sha256` whether
  or not anything else names it; and
- the `retirement` branch copies the evidence of a finding that already exists
  and keeps whatever that finding declares.

The rule is written where a reader meets it: in the docstring of
`reconcile_requirement` and beside the `archive_artifact` branch, and a test
asserts both halves, that no reference row is written and that the file is in
the closure anyway.

## Why

A requirement is a findings row with columns `replace_findings` does not write:
its state, desired fingerprint, next action, blocking reason and next eligible
time, and an identity that is the requirement's own rather than a hash of the
summary. Routing it through `replace_findings` would mean either widening that
function to the requirement model or writing the row twice, for a declaration
that would add nothing: the file it would name is already kept, by the snapshot
the requirement is reporting on.

The risk this leaves is that a reader finds a raw `INSERT INTO findings` and
concludes that declarations are optional in general. A comment and a test are
what answer that, and they are cheaper and clearer than a declaration that
restates a pin which already exists.

## Alternatives

- Route requirements through `replace_findings`. Rejected: the function would
  have to grow the requirement's columns and identity rule, and the write is a
  reconciliation of one requirement, not a replacement of an owner's whole set.
- Write the reference rows directly from `reconcile_requirement`. Rejected: it
  would be a second writer of that table with no reader that needs it, and
  removing the snapshot is what would really free the file.

## Consequences

Doctor's unknown list is unaffected by requirements, and no requirement can
cause a `missing_declared_references` line. If a future requirement is ever
about a file that no snapshot pins, this record is what says that requirement
has to declare it, and the test is what fails when it does not.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0139](0139-findings-declare-the-support-they-rely-on.md)
- [D-0160](0160-an-empty-declaration-never-clears-a-findings-recorded-references.md)
- [State contract](../../docs/reference/state.md)
