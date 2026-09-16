# Recovering from changes and failures

Saved pages and a record of unfinished work let Swingset retry much of its
pipeline without starting over. Recovery must also preserve corrections,
operator pauses, and evidence that needs human review.

## Remember inputs and unfinished work

A work unit describes a bounded task, such as rebuilding one event. Its
output is committed with its completion record. If the task fails inside
that transaction, its unfinished work remains available for a later attempt.

A _generation_ is a saved version of a task's output and its inputs. When
those inputs change, dependent work can become out of date. Reusing an output
requires checking that its supporting evidence still applies.

## Explain gaps without hiding them

A missing page, unclear identity, or rejected interpretation is a different
problem from a crashed process. Retrying everything cannot fix all of them.
Swingset records requirements and findings so it can explain what is missing
and whether a person must act.

Some event-completion and automatic-repair guarantees remain planned.
Use [current status](../status.md) to distinguish those plans from running behavior.

## Go deeper

- [State](../reference/state.md): durable work and stored generations.
- [Recovery contract](../reference/recovery.md): guarantees and remaining target behavior.
- [Recovery rollout](../plans/recovery/README.md): implementation order and acceptance cases.
- [Backup and restore](../guides/backup-and-restore.md): operator procedures.
- Code: [work records](../../src/swingset/state/work.py), [derivations](../../src/swingset/state/derivations.py), and [checkpoints](../../src/swingset/backup/checkpoint.py).
