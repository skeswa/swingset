# D-0163: Every retention value is checked where the table is read

Recorded: 2026-09-18  
Decided by: agent  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

`parse_retention` checks every value in the `[retention]` table, not two of
them. `collect_older_than` must be at least one second. `checkpoint_max_age` and
`checkpoint_incomplete_max_age` cannot be negative. `max_database_bytes` and
`recent_window` must be at least one, and `checkpoint_keep_recent` at least one,
as before. Each refusal names the setting.

## Why

`collect_older_than` is the collector's age floor, and the plan carries it so an
apply compares a candidate directory's own modification time against it. That
floor is the only thing between a build that is still writing its candidate
directory and a removal
([D-0143](0143-an-undeclared-file-is-unknown-in-the-plan.md)). Zero is not a
faster setting, it is the absence of the protection: an apply could remove a
candidate the moment it appeared. `duration` already refuses a negative value,
so zero was the one way in.

The checkpoint ages are reached through `duration` too, so a negative one cannot
arrive today. The check is written anyway, because the defaults are a dataclass
and the rule should be stated where the value is read rather than left to a
helper two calls away.

## Alternatives

- Validate in `RetentionConfig.__post_init__`. Rejected: the dataclass is also
  the defaults and is constructed in tests and in the input bundle; the file is
  where an operator's mistake arrives, and the message can name the file there.
- Let the collector treat zero as the default. Rejected: a policy value that
  silently becomes something else is worse than a refusal, and the plan is meant
  to carry the floor that was actually in force.

## Consequences

A configuration mistake stops the command at load, before anything reads state,
with the setting named. An operator who really wants a shorter floor can set one
second; there is no way to turn the floor off, which is the point.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0141](0141-two-retention-limits-and-the-collector-age-become-policy.md)
- [D-0143](0143-an-undeclared-file-is-unknown-in-the-plan.md)
- [D-0156](0156-capture-the-retention-limits-as-values.md)
- [State contract](../../docs/reference/state.md)
