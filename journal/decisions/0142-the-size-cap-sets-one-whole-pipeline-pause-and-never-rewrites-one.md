# D-0142: The size cap sets one whole-pipeline pause and never rewrites one

Recorded: 2026-09-18  
Decided by: agent  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

When `state.sqlite` on disk is larger than `retention.max_database_bytes`, the
worker sets one operator pause on the `all`/`all` selector through the existing
`controls.change_control`, with the fixed reason "state database file is over
its retention size cap". This happens at cycle start, right after inputs are
accepted, and when `gc --plan` runs.

If any `all`/`all` pause already exists, whatever its reason, nothing is
written and the existing reason is reported instead. If a restore is in
progress, marked by `RESTORE_PENDING`, nothing is written at all.

Nothing clears the pause automatically. An operator resumes with
`swingset resume --all` after `gc --apply` and `gc --reclaim` have brought the
file back under the cap.

Until those exist, nothing shipped can shrink the file, so resuming on its own
pauses again on the next cycle. The pause result therefore carries a `remedy`:
raise `retention.max_database_bytes` in `config/sources.toml`, then resume.
`gc --plan` prints it, and both operator documents say the same.

## Why

The pause has to stop new work without stopping the work that fixes the
problem. Admission is exactly that line: fetching and derivation go through it,
while reading, doctor, controls, `gc`, backup and restore do not. An `all`
pause is therefore the whole of "stop writing more" and none of "stop
recovering".

The reason string is fixed so that repeating the check every cycle is a no-op:
`change_control` rewrites a pause when its reason, actor or expiry differ, so a
reason carrying the current byte count would rewrite the pause and add a control
event every cycle. The extra check for an existing pause protects an operator's
own words: a cap crossing must never overwrite the reason someone else paused
for.

Automatic resume was rejected outright. The file only shrinks after a deliberate
apply and reclaim, and an operator should see what happened before work starts
again.

The re-pause is the cap working, not a bug, but in this step it is a trap unless
the way out is written down: an operator who resumes and watches the pipeline
pause again on the next cycle has no way to tell whether that is intended. The
default cap of 8,000,000,000 bytes against the 5,016,920,064-byte specimen
database in section 1 of the plan means this is one setting away from happening,
so the remedy is reported at the moment the pause is set rather than left to be
discovered.

## Alternatives

- Pause only the offline stages. Rejected: acquisition is what grows the
  database fastest, and the selectors are host, source, kind or all.
- Refuse to open the database over the cap. Rejected: that stops recovery, which
  the safety rules forbid.
- Include the measured size in the reason. Rejected: it rewrites the pause every
  cycle; doctor reports the numbers instead. The remedy rides in the result and
  the printed output, not in the stored reason, for the same reason.
- Remember that an operator resumed and stop re-pausing. Rejected: the cap would
  then hold only once, and a database that kept growing past it would run on
  unchecked. Raising the knob is a deliberate act with a record; a remembered
  resume is not.

## Consequences

An operator who has paused everything for another reason gets no cap pause,
only a doctor report saying the cap is crossed, which is the right order of
authority but means the pause is not a reliable signal on its own. The check
costs one `stat` per cycle.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [Operations contract](../../docs/reference/operations.md)
- [Durable controls](../../docs/reference/state.md)
