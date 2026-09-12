# Self-healing audit evidence

Companion to [the systemic diagnosis](../../../self-healing-2026-09-12.md).

- `offline-checks.json`: output of `research/self_healing_checks.py`, using
  temporary state, checked-in registry fixtures, mocked HTTP, and a synthetic
  exact-name judge candidate. Confirms an identical successful probe response
  fails to advance its cursor, an unchanged profile is selected for stale
  refresh the next day, and the judge score is 0.875.
- `judge-confidence-ceiling.json`: counts from the pinned missing-data SQLite
  capture, plus the default-weight formula. All 1,292 eligible judge candidates
  have leader/follower roles; none match the unknown judge role. All 3,791
  judge links fall below the 0.90 probable threshold. The highest observed score
  is 0.875.

The pinned production capture predates the reproduced registry freshness effects.
There were zero registry watches with a successful snapshot pointer different
from their current observation's snapshot. The reproduction demonstrates code
behavior; it is not a report of a live stalled probe.

Regenerate the offline output from the repository root:

```sh
nix develop --command uv run python research/self_healing_checks.py \
  > research/verification/2026-09-12/self-healing/offline-checks.json
```

These are diagnostic outputs for the audited revision, not assertions that the
defects should persist after a repair. No source requests or production writes
are involved. See the [missing-data evidence](../missing-data/README.md) for
publication identifiers, capture provenance, and the broader inventories.
