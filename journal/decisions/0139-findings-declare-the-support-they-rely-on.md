# D-0139: Findings declare the support they rely on

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

Schema 31 adds `finding_support_references(finding_id, kind, sha256)`. `kind` is
`body` for a file under `blobs/`, `extract` for one under `extracts/`, or
`generation` for a derivation generation. `state.findings.Finding` gains a
`references` field and `replace_findings` writes those rows with the finding.

The file closure reads these rows for open findings instead of scanning evidence
JSON for strings shaped like a digest. The migration backfills the table from
every finding's evidence using that same scan once, so no file that was pinned
before the migration stops being pinned by it. A checkpoint of an older schema
has no such table, and only there does the closure still scan.

Declaring a generation makes that finding a retention root, so
`replace_findings` runs the same residency check `hold add` runs and refuses to
write anything if the generation's output is not completely local. Nothing in
the pipeline declares a generation today; the registry crosscheck findings
declare the body digest they already recorded in their evidence.

## Why

Pattern-matching guesses in both directions. A digest recorded for any other
reason pinned a file forever, and a digest a finding really needed would be
missed if it were written in a shape the scan did not recognise, for example
inside a longer string. Retention has to be able to say why each file is kept,
and "some string in this JSON looked like a hash" is not a reason anyone can
check.

The scan also cost a full read of every open finding's evidence on every
checkpoint, every collector run, and now every plan.

Keeping the scan for older checkpoints is not a second source of truth: those
databases are frozen, and the rule they were written under is the rule that
still applies to them.

## Alternatives

- Reuse `finding_support`, which already holds a JSON copy of the finding.
  Rejected: it is one payload blob per finding, so retention would be back to
  reading JSON to find digests.
- Keep the scan and add declarations alongside it. Rejected: two rules, and the
  loose one wins, so nothing improves.
- Refuse a generation reference outright until step 4 exists. Rejected: the
  check is what makes the reference safe, and writing it now means step 4 has
  nothing to add.

## Consequences

A declared file that is missing is reported, not raised. The scan could not tell
a body from an extract, so it kept whichever file existed and silently kept
neither when both were gone; a declaration says which, so the planner can name
the gap. It names it: the closure still keeps only files that are on disk, and
the plan lists the rest as `missing_declared_references` for doctor to show. One
wrong declaration therefore cannot stop a backup that is otherwise complete
([D-0144](0144-unowned-bytes-are-unknown-and-a-wrong-declaration-is-reported.md)).

Every caller that wants a file kept from now on has to say so. A caller that
forgets leaves its evidence unpinned, and that is visible rather than silent:
the file becomes an undeclared file in the plan's unknown list, which nothing
removes and doctor reports
([D-0143](0143-an-undeclared-file-is-unknown-in-the-plan.md)). Nothing removes
it on age either; the age floor belongs to disposable candidate directories, and
the direct collector that used age alone is gone
([D-0147](0147-only-a-planned-locked-apply-removes-anything.md)).

Forgetting does not unpin what was already recorded. A caller that declares
nothing leaves the finding's rows alone, including the ones this migration
backfilled, and an empty declaration is not a change
([D-0160](0160-an-empty-declaration-never-clears-a-findings-recorded-references.md)).
The residency check on a declared generation covers what a write newly declares,
not every declaration in the batch
([D-0162](0162-the-residency-check-covers-only-newly-declared-generations.md)).

The schema bump moves the step 2 payload tests, which pin themselves to schema
30 under D-0013 so they keep testing the migration they were written for.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [Schema history](../../docs/reference/schema-history.md)
- [State contract](../../docs/reference/state.md)
- [D-0013](0013-preserve-historical-migration-tests.md)
- [D-0143](0143-an-undeclared-file-is-unknown-in-the-plan.md)
- [D-0144](0144-unowned-bytes-are-unknown-and-a-wrong-declaration-is-reported.md),
  which decides that a declared file that is not on disk is reported and not
  raised, and is what this record's first consequence states
- [D-0160](0160-an-empty-declaration-never-clears-a-findings-recorded-references.md)
- [D-0161](0161-a-requirement-finding-relies-on-the-snapshot-pin.md)
- [D-0162](0162-the-residency-check-covers-only-newly-declared-generations.md)
