# D-0135: Fence every path the measurement tool writes, and make its gates the exit status

Recorded: 2026-09-18  
Decided by: agent  
Topic: State retention and recovery cost  
Supersedes: —  
Superseded by: —

## Decision

Four rules are added to
[`journal/tools/runtime/measure_state_storage.py`](../tools/runtime/measure_state_storage.py),
the step 1 tool designed in
[D-0127](0127-measure-state-storage-read-only-on-a-copy.md).

- **Every destination is fenced the same way, before the measurement runs.**
  `--output`, `--receipt`, and `--scratch` all pass through one check: not under
  `/var/lib/swingset`, not inside the copy being measured, not containing it,
  and not already there. The check runs before the database is opened, so a bad
  path costs nothing instead of costing the whole measurement.
- **The exit status is the gates.** The tool exits non-zero when page
  accounting does not close, when generations declare more output rows than can
  be read, when a row names no generation, or when `--time-backup` fails. The
  report and the receipt both carry a `gates` object naming each check, and the
  findings name the ones that failed.
- **Declared rows are compared with readable rows.** `derivation_generations`
  says how many rows each computation produced; the tool also counts the rows
  that actually stream out of `derivation_rows`. Both numbers go in the report,
  in the receipt, and into the gate above.
- **The receipt keeps the per-stage table.** Stage and unit-kind rows, payload
  bytes, distinct digests, and the distinct-to-total ratio are retained, capped
  at 200 scopes with the cap recorded. A shared-memory file is refused because
  it exists, not because it has bytes in it.

## Why

The tool's one claim is that it cannot touch what it measures, and the runbook
leans on it. That claim did not cover the paths the tool writes. `--scratch`
was checked against the measured copy but not against the live root, so
`--scratch /var/lib/swingset/timing` would have restored a held checkpoint and
written a fresh one inside the live state directory: roughly 17.5 GB on the
plan's section 1 figures, inside a tree every later backup copies whole. That
is the failure
[worker disk exhaustion](../investigations/2026/worker-disk-exhaustion-2026-09-18.md)
already documents. `--output` and `--receipt` were not checked at all, and
`--output "$WORK/copy/report.json"` — one path segment away from the documented
command — put a file inside the sealed copy, after which
`verify_checkpoint` fails with `checkpoint closure differs: extra=['report.json']`
and the copy can no longer be checked against its own manifest. Both reproduced
before the change; both are now refused, with tests.

Checking those paths after the measurement was its own fault. A rerun onto an
existing `--output` ran the whole measurement and then died in `open("x")`,
losing hours of reading on a 5 GB copy. The tool already refused a stale
`--scratch` up front; the other two now match.

Plan section 5's gate is "the counts match the copy", and the tool had both
halves of that comparison and made neither. A generation whose payload bytes
are not local declares rows that no longer stream — the state plan step 4
creates deliberately, and the 0030 migration comment describes. Without the
comparison such a database measures as clean, and the distinct-to-total ratio,
the single number step 2 rests on, is quietly computed over fewer rows than
exist. Reproduced: a generation declaring 3 rows with one payload absent
reported 2 rows, a ratio of 1.0000 instead of 2/3, and no finding.

Exit status followed only the optional timing leg, so a failed accounting check
— a named done-when — exited 0. That is backwards for an operator running the
runbook in a script.

The receipt is what survives; the runbook deletes the scratch tree that holds
the full report. Keeping only a summed whole-database upper bound would throw
away exactly what the plan asks to write down, "how many distinct rows there
are compared to total rows, per stage". The per-scope table is a handful of
rows and well under a kilobyte.

Refusing a shared-memory file on existence closes a copy race: SQLite creates
the file and sizes it afterwards, so a `cp -a` can catch a zero-length one, and
a size check would let an immutable reader ignore whatever the holding
connection had pending.

## Alternatives

- Warn about a destination inside the measured copy instead of refusing.
  Rejected: the copy stops verifying against its own manifest the moment the
  file lands, and a later `--time-backup` on it fails, because restore verifies
  its source first.
- Let the report record a declared-versus-readable mismatch as a note only.
  Rejected: plan section 5 makes matching counts a done-when, and the same tool
  is the before-and-after instrument for section 6's digest gate.
- Exit non-zero only on accounting, leaving the row comparison advisory.
  Rejected: both are counts that must match the copy, and an operator reads one
  exit status, not four fields.
- Put the whole per-stage table in the report only, and summarise it in the
  receipt. Rejected: the summary is the part that cannot be recomputed once
  scratch is gone.
- Cap the receipt scope table by bytes rather than by count. Rejected: a count
  is predictable and the entries are fixed-shape; the cap exists so a receipt
  stays small whatever it measured, not to hit a byte target.

## Consequences

The tool writes nothing outside a fenced, new destination, and says so with
checks rather than with a sentence in a runbook. An operator who mistypes a
scratch or output path is refused in under a second instead of hours later, or
instead of never.

The report and receipt formats gain `gates`, a declared row count, and a
per-stage table, so both are versioned `v2` and the tool version is `v3`.
Nothing retained reads the `v1` shapes; no evidence bundle exists yet.

A held backup with archived payloads will now fail a gate and exit non-zero.
That is intended: the counts do not match the copy, and step 1 is not done
until it is understood why.

Unverified until the measurement runs: that the worker's held checkpoint
declares exactly as many output rows as read back.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md), section 5
- [Tool design](0127-measure-state-storage-read-only-on-a-copy.md)
- [Timing order for a held checkpoint](0132-time-a-held-checkpoint-by-restoring-it-first.md)
- [References outlive payload bytes](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)
- [Investigation](../investigations/2026/state-storage-measurement-2026-09-18.md)
- [Worker disk exhaustion](../investigations/2026/worker-disk-exhaustion-2026-09-18.md)
- [Evidence rules](../evidence/README.md)
- Renumbered: the interning migration is 0032, not 0030, since [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md).
