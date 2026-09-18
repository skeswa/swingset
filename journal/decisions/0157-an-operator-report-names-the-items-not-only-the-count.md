# D-0157: An operator report names the items, not only the count

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

Two retention reports name what they are counting.

`swingset doctor` gains `retention.unknown_items`: the payload digests, the
generation ids and the file paths behind each `unknown` count, cut to the same
detail limit as the rest of the report. The whole counts stay next to them.

A `gc --apply` receipt accounts for every file its plan named, in exactly one
list. `removed_files` is what went, `skipped_files` is what the fresh plan no
longer calls removable, and `already_gone_files` is what this apply found was
not there. The receipt of a note a crashed apply left behind counts a file the
crashed run had already unlinked as removed by that apply, because that is what
happened to it.

## Why

Nothing removes an unknown item. Every one of them is work for a person: a
payload nobody owns, a reference whose label is missing, a file nothing
declares. A count tells an operator that the work exists and nothing else, so
the next step was to write a plan file out and read it, in a state directory
where a plan names every artifact file and runs to megabytes. The items are
already computed; not printing them was the only thing in the way.

The receipt is the operator's account of one apply. A resumed note's receipt
listed only the files this apply unlinked, so a file the crashed apply had
already removed appeared under `planned_files` and in no other list: gone from
the disk, gone from the fresh plan, and unexplained in the one record of the
apply that removed it. Plan section 11 says evidence is never rewritten to make
a failed step look successful; a receipt that quietly loses a removal is the
same failure in the other direction.

## Alternatives

- Print the unknown items only in the JSON form of doctor. Rejected: the limit
  already differs between the two forms, and an operator reading the text form
  is the one who has to go and look at the item.
- Print every unknown item with no limit. Rejected: doctor cuts every other
  list, and an unbounded list of orphan digests would bury the rest of the
  report.
- Count a file the crashed apply removed as `skipped`. Rejected: it says the
  opposite of what happened.
- Add `already_gone_files` to the resumed note's receipt too, rather than
  counting those files as removed. Rejected: for that note, the crashed apply is
  the apply the receipt describes, and those files are what it removed. The key
  belongs to the apply doing the removing, which is where it is.

## Consequences

Doctor's result grows by the unknown items, bounded by the detail limit. Receipt
readers get one more key, empty on every ordinary apply. The counts and the
existing keys are unchanged, so anything already reading them keeps working.

## Links

- [Implementation plan, section 7](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [A resumed note removes only what the fresh plan still names](0151-a-resumed-note-removes-only-what-the-fresh-plan-still-names.md)
- [A receipt is the operator copy of a note that lives in the database](0148-a-receipt-is-the-operator-copy-of-a-note-that-lives-in-the-database.md)
