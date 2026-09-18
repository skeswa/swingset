# D-0156: Capture the retention limits as values, and keep them out of every recipe

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

Every input bundle carries `policy/retention.json`: the retention limits that
were in force when the bundle was captured, written as values. It holds all six
fields of `RetentionConfig`, including the size cap and the recent window that
plan section 9 requires to be captured.

The limits stay operating policy. No stage recipe selects them, so changing one
invalidates no work and recomputes no generation. `swingset doctor` and the
plan's own `knobs` block report the values in force now; the bundle records the
values a past run used.

## Why

Plan section 9 says the two limits are "captured in the input bundle". The
bundle captured the bytes of `config/sources.toml`, which is not the same thing:
the `[retention]` table is optional, and an absent table means the defaults in
`config.py`. A reader of a year-old bundle could see the file and still not know
what the size cap was, because the answer was in the code of the day, not in the
bundle. Capturing the effective values answers it from the bundle alone.

The second half is a promise an operator needs before touching the knobs. When
the cap is hit, the operation guide tells the operator to add a `[retention]`
table to `config/sources.toml` and deploy. That changes a captured file's digest.
If any stage's recipe selected that file, raising the cap would recompute
project work and grow the very database the cap defends. It does not: the
project and link recipes select the named override and version inputs, never the
whole file, and `affected_work` has no rule for either name. `gc --plan`,
`gc --apply` and the cap pause read the limits from the live configuration at
the moment they run, which is what an operator raising a cap expects.

That was true before this change and untested. It is now a test:
`tests/test_retention_policy_inputs.py` raises the cap exactly as the guide says
to, and asserts that the project generation fingerprint does not move and that
no work is queued.

## Alternatives

- Leave the limits as file bytes only. Rejected: it does not meet section 9, and
  the absent-table default makes an old bundle unreadable on this point.
- Move the `[retention]` table into its own configuration file. Rejected: a
  second file to deploy and capture, for a problem the bundle entry solves; and
  the table sits next to the other operating policy in `sources.toml`.
- Capture only `max_database_bytes` and `recent_window`, the two the plan names.
  Rejected: the other four are the same kind of policy, read from the same table,
  and a reader asking what the collector's age floor was has the same problem.
- Make the limits a recipe input so a change is visible in derivation identity.
  Rejected: that is exactly the recomputation the cap exists to avoid, and
  retention has no effect on what a computation produces.

## Consequences

Bundle digests change once, when this lands, because every bundle gains a file.
`policy/retention.json` becomes an accepted input with no consumers, so it is
recorded and invalidates nothing. A bundle now says what the limits were; the
database still says nothing about what they were at the time of an older
generation, which is the bundle's job, not the generation's.

## Links

- [Implementation plan, section 9](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [Operating the worker](../../docs/guides/operation.md)
- [Two retention limits and the collector age become policy](0141-two-retention-limits-and-the-collector-age-become-policy.md)
