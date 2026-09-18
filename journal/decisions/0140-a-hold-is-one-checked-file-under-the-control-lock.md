# D-0140: A hold is one checked file written under the control lock

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

A hold is one JSON file, `state/holds/<hold_id>.json`, holding a format tag, its
own id, who asked for it, why, when, and the generation ids and artifact digests
it keeps:

```json
{
  "format": "retention-hold-v1",
  "hold_id": "hold_<hex>",
  "who": "sandile",
  "why": "checking the 2019 rounds",
  "created_at": "2026-09-18T00:00:00+00:00",
  "generation_ids": ["dg_..."],
  "artifact_digests": ["<sha256>"]
}
```

`swingset hold add`, `hold list` and `hold remove` are the interface. Adding one
takes the control lock, walks the closure of every generation named, and refuses
if any generation it reaches is not completely local: references present, every
payload present, and the count matching the label. A named artifact digest has to
already be a file under `blobs/` or `extracts/`. The refusal lists the
generation ids to bring back with `gc --restore`, and nothing is written. A held
digest joins the file closure, so the collector keeps that file and every
checkpoint carries it. Holds go into checkpoints too, so a restored backup keeps
holding what it held. Written plans under `state/gc/` do not.

The command takes the control lock and never the whole-command writer lock, so
an operator can place a hold while a cycle runs.

## Why

A marker that claims data that is not there is a promise the backup cannot keep:
the safety rules say a restore works with no network for everything kept
locally. Checking at the moment of writing is the only point where the answer is
both known and actionable, and the control lock is what stops an apply from
removing the data between the check and the write.

Files rather than rows because a hold is operator state, like `operator-hold`
and the baseline symlink, and because an operator has to be able to read and
remove one with the database closed. One file per hold means removing a hold is
one unlink and never rewrites another operator's words.

## Alternatives

- A `holds` table in SQLite. Rejected: a hold has to be readable and removable
  during recovery, when the database may be mid-migration or unopenable.
- One `holds.json` listing every hold. Rejected: two operators writing at once
  would rewrite each other's entries, and a partial write would lose all holds.
- Accept the hold and mark it as needing a restore. Rejected by the plan's
  safety rules: a marker never claims data that is not there.

## Consequences

A hold on archived data is a two-step operation: restore, then hold. Hold files
are not validated by anything but the retention walk, so a hand-edited file
naming a missing generation is reported by the plan rather than silently
ignored. Because holds ride in checkpoints, a restored backup keeps holding
whatever it held when the checkpoint was taken.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [Operating guide](../../docs/guides/operation.md)
- [Operations contract](../../docs/reference/operations.md)
- [D-0164](0164-a-checkpoint-holds-the-control-lock-across-its-closure-and-copy.md),
  the other side of this lock: a checkpoint takes it too, so a hold cannot be
  written between the closure and the copy
