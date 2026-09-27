# D-0217: Large generations store in chunks, and the write deadline is an alarm

Recorded: 2026-09-27  
Decided by: agent, 2026-09-27, under D-0175; source: phase 2 of the [architecture plan](../../docs/plans/architecture.md) (invariant I2)  
Topic: Local state  
Supersedes: the 300-second history write allowance in [D-0198](0198-history-no-longer-gates-acquisition-and-deadline-overruns-retry.md)  
Superseded by: —

## Decision

A `project/history` unit, or a unit whose kept generation holds more than
5,000 rows, stores its generation in stages. Its processing transaction
writes the live rows and a `derivation_building` row. The output is then
labelled from a read snapshot, stored in transactions of at most 5,000 rows,
and one short transaction flips the pointer and finishes the attempt. A crash
between stages resumes from the building row, which keeps the first
capture's witness and a sticky flag for live rows moved, so a resumed cutoff
still moves the replay epoch. A unit with a building row is not current; its
next successful run deletes building rows of other inputs. A partial
generation stays (generations are immutable) and is never read as output:
the release closure and the retention window skip it, and the retention
plan lists it `incomplete`. A partial generation counts as still building
wherever it could be reused: a run whose inputs return to it finishes it by
ordinal, and the pointer flip counts its references, so no pointer selects
it with rows missing. Maintain counts partial generations that no building
row names, and the watchdog prints `[incomplete-generations]` above three.
Phase 4's mark-sweep collects those older than a day that no pointer,
equivalence or building row names; until then an operator decides. The
`accept-year` command stages history the same way. Other units keep one
transaction.

Every worker write has the same 60-second alarm (`WRITE_SECONDS`); no unit
gets a longer one. A firing still rolls the write back and the unit retries
on the work queue's clock (D-0216), but it is now an alarm: each firing is
counted, every run record carries `write_deadline_fired`,
`write_seconds_max`, `write_wait_seconds_max` and `write_lock_timeouts`, and
the watchdog prints `[write-deadline]` for any run in the last two hours
that fired. D-0201's containment stays: a bookkeeping stage that overruns is
skipped, not fatal.

## Why

History wrote ~201,000 rows in one transaction, 55 to 82 seconds on the
2026-09-24 copy. D-0198 gave it 300 seconds, a bound that grows with the data
and holds the writer for five minutes. Under I2 each step is small by
construction, so a deadline that fires means a step that is not; it must be
seen, not raised. The live-row write in the first stage is still one
transaction. On the 16:10 copy on the Mac (R5) it took 2.0 to 2.3 seconds,
each of 41 chunks at most 4.0 seconds, and the label equalled the kept
generation's. `accept-year`'s single-path write of the same 201,072
references took 61.35 seconds there, over the alarm, so it now stages too.
The release build's `complete` write (13.8 s alone, 35.8 s beside a second
build) is still one write; switch 2b is gated on its worker time.

## Links

- [State reference: derivation generations (H15)](../../docs/reference/state.md#derivation-generations-h15)
- [Phase 2 rehearsal receipt](../evidence/runtime/architecture-phase2-rehearsal-2026-09/receipt.json)
