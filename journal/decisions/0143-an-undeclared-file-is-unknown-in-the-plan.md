# D-0143: An undeclared file is unknown in the plan, and the plan is named by its digest

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

`gc --plan` writes one canonical JSON file to
`state/gc/plans/<plan digest>.json`. It holds the knobs in force, every root
with the generations it names, one row per labelled generation (its scope, row
and payload counts, payload bytes, whether it is `local` or `archivable`, and
which root kinds reached it), one row per artifact file and candidate directory,
the unknown items, and the digest of everything else in the file. It carries no
timestamp, so the same state produces the same bytes and the same name. It
removes nothing, and rewriting an existing plan file is skipped rather than
repeated.

Five things are unknown: payload bytes no reference names, payload bytes only a
reference with no label names, a reference whose label is missing, a file under
`blobs/`, `extracts/` or `inputs/` that nothing declares, and a file an open
finding declares that is not on disk. Unknown is reported and never eligible,
and unknown bytes are never counted as bytes archiving would give back
(D-0144).

Candidate directories are not unknown. The collector's existing rule keeps the
baseline, every pending candidate, and the five newest built ones, and the plan
marks the rest `removable` with the reason it applied and with `eligible_after`:
the directory's own modification time plus the collector's age floor. That floor
is the only thing between a build that is still writing its candidate and a
removal, so the plan has to carry it and apply compares it against its own
clock. It comes from the directory and not from the clock, so an unchanged state
still plans to the same bytes. A candidate with no `BUILT` marker is never
called a superseded release; its reason is `unbuilt_candidate`.

Written plans stay out of checkpoints: `gc` joins the excluded top-level names.
The newest ten are kept; writing a new plan removes the rest.

## Why

A plan that changes between two runs on unchanged state cannot be reviewed
before an apply, which is what the plan's rollout requires. Timestamps and a
record order that depends on SQLite's row order are the two ways that happens,
so both are excluded: every list is sorted by content.

Naming the file by its digest means an operator can write the digest down, and
`gc --apply <digest>` can find that exact plan without a second index.

Calling an undeclared file unknown rather than removable is the plan's own rule:
nothing is removed because nobody can explain it. While this record was written
the old collector still removed such files on age alone, which was a difference
the removal half of step 3 had to settle. It settled it by deleting the
collector: `gc --apply` removes only what a plan names, so an undeclared file is
now reported and kept
([D-0147](0147-only-a-planned-locked-apply-removes-anything.md)).

## Alternatives

- Put the plan in the database. Rejected: it has to be reviewable and diffable
  by an operator, and it must survive the database being unopenable.
- Name plans by time. Rejected: two identical plans would get two names, and an
  apply could not tell whether the state had changed.
- Mark undeclared files removable, matching the collector. Rejected: the plan
  says unknown is never eligible, and an undeclared file is exactly the case
  where someone should look before anything is deleted.
- Leave the age floor out of the plan and let apply re-derive it. Rejected: the
  plan is what apply acts on, and a rule that lives only in the collector is a
  rule an apply can forget. An in-flight build would be the first casualty.
- Keep every written plan. Rejected: each one lists every artifact file, so on a
  large state directory repeated planning during an investigation grows the
  state directory the cap is meant to bound.

## Consequences

A large state directory makes a large plan, since every artifact file gets a
row, and every change of state gives a new digest and a new file. Keeping the
newest ten stops the plans directory growing without bound inside the very state
the size cap defends, while leaving the plan an operator or `gc --apply` just
asked for. Step 3b's receipts point at plans by name, so a receipt can outlive
the plan it names; the apply recomputes the plan under the locks anyway, so the
file is a record and not the authority. The same walk runs for doctor without
writing anything.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0137](0137-one-module-owns-the-closure-the-collector-and-the-walk.md)
- [D-0147](0147-only-a-planned-locked-apply-removes-anything.md)
- [State contract](../../docs/reference/state.md)
