# Manual registry replay routing diagnosis, 2026-09-17

## Finding

The `unit_exception` for snapshot
`snap_20260909T153428Z_ae7f2b9d688b` is an accidental work-routing error, not
an expected unsupported source page. The retained blocker records
`KeyError: 'unknown page kind: registry_crosscheck'` while parsing that
snapshot. Its parser value is a sentinel for a manually archived registry
comparison dump, whose supported execution path is `schedule.registry`'s
`replay_crosscheck()` / `_crosscheck_body()` logic. It is not a normal page
parser and should not enter generic `parse_snapshot()` work.

The trigger is also retained: the input rehearsal accepted `recipe/runtime`.
`state.inputs.accept()` sends changed inputs through `state.work.affected_work()`;
the `recipe/runtime` branch currently enumerates every row in `snapshots` as a
parse unit. That includes the manual registry cross-check snapshot inserted by
`schedule.registry._record_crosscheck_snapshot()`. The next generic parse calls
`get_page_kind(w.parser)`, which correctly rejects the sentinel. Thus the
failure is downstream of an overbroad invalidation, not a missing page-kind
registration. Registering `registry_crosscheck` as an ordinary page kind would
blur the intentional boundary between a manual dump replay and source-page
extraction.

The rehearsal record supports this chain: `inputs-001/accept.json` includes
`recipe/runtime`; `inputs-review-001/drain-002-blockers.json` records this exact
snapshot and exception; and the schema-rehearsal investigation describes it as
a manual `registry_crosscheck` snapshot. The exact snapshot-to-watch row is not
included in those receipts, so this audit relies on the coordinator-provided
identification and the retained source code path rather than claiming an
independent database join.

## Domain contract and fix boundary

`recipe/runtime` invalidation means “re-extract parser-managed page snapshots
whose extraction recipe may have changed.” It does not mean “replay every body
stored in the archive.” Manual registry dump evidence is replayed by its
dedicated registry cross-check operation and is not derived from the ordinary
extract/parser recipe. It should stay archived and available for that operation
without being enqueued as a `parse/snapshot` unit.

The minimal fix belongs in `src/swingset/state/work.py`, in the
`affected_work(conn, "recipe/runtime")` branch: restrict both the
`extract_version` invalidation and returned parse work to parser-managed
snapshots, excluding the manual registry cross-check sentinel. Keep ordinary
known parser snapshots in the returned work. Do not register the sentinel in
`sources`, make its manual watch look like a normal watch, or change
`derive_one()` to swallow arbitrary unknown page kinds. An unknown parser on a
genuine parse work item should remain a visible blocked error until its source
contract is implemented.

Prefer an explicit, named predicate for the manual replay artifact (matching
the current `source='crosscheck'`, `method='MANUAL'`, and
`parser='registry_crosscheck'` binding) over a broad “ignore all unknown kinds”
catch. If the project later formalizes a general artifact-kind discriminator,
the invalidation query can use that shared contract. The immediate evidence
only establishes this exact manual sentinel; it does not justify excluding
other kinds of snapshots.

## Implemented fix and regression

`state.work.affected_work()` now excludes only the exact registry dump sentinel
(`source='crosscheck'`, `kind='registry_dump'`, `method='MANUAL'`,
`parser='registry_crosscheck'`) from runtime-recipe extractor invalidation and
parse enqueue. It leaves ordinary registered parser work in scope. It does not
hide other unrecognized page kinds, and does not remove any existing pending
work or failure record.

The new offline regression uses real `state.inputs.accept()` calls with
deterministic captured runtime bytes. It creates the manual artifact through
`replay_crosscheck()` and ordinary/unknown parser snapshots in a local database.
It verifies the recipe change queues and invalidates both ordinary and
unrecognized parser snapshots, skips the manual artifact, retains its parser
bookkeeping, and leaves direct registry replay working. It routes the unknown
parser through `derive_one()` and verifies the expected durable blocked attempt
and requirement finding remain byte-for-byte unchanged across a later recipe
acceptance. It also inserts a pre-existing stale manual parse token and verifies
recipe acceptance does not delete or rewrite it; any such token still needs
separate historical remediation. The unchanged-recipe acceptance remains
idempotent.

Focused validation after the regression strengthening: the new test passes
(`1 passed`), Ruff lint passes, and mypy passes for the source and test files.
Ruff's format check requests routine reflow in the expanded test assertions;
coordinator formatting is pending. No production database, rehearsal packet,
or network source was used for implementation or validation.

## State classification

The manual dump remains archived evidence with a dedicated direct replay path.
It is not a page-kind implementation, canonical source admission, or a
production collection event. The invalidation fix is **implemented** and its
focused regression is **tested** locally. It is not deployed or published. This
work did not change source-005 or any runtime/production state and used no
network.
