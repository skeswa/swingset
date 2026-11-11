# Developing Swingset

On the `postgres-migration` bookmark, mise pins Python and uv and manages
native Postgres 17.11 containers. Production still runs the Nix deployment
from `main` until the migration switch. Start from the repository root:

```sh
mise install
mise run dev
mise run test
mise run test -- tests/test_project_registry.py
mise run test-watch -- tests/test_project_registry.py
mise run check
```

`dev` prepares the development, test and durable physical-test services,
and creates the default bounded fixture seed. Tests get a fresh private
database per case. The ordinary core reuses the separate physical service;
lifecycle fault tests and CI use fresh clusters. Unexpected physical-service
identity or database inventory blocks reuse until its retained owners are
checked; the command never resets unknown state. A pure parser test does not start Docker. A schema,
role, image or template-fixture change invalidates the matching template;
source edits run directly from the checkout.

Restarting Docker can clear the temporary TEST container's data while its
ownership receipt survives. A role-signature refusal then needs an empty-state
check before recovery; do not reset the durable physical profile. A guarded
recovery command is [maintenance item 45](../plans/README.md).

Replay changed code on a disposable copy of the seed:

```sh
mise exec -- uv run swingset dev-replay default --event 2026-09-example-swing
mise exec -- uv run swingset dev-replay default --unit project/dancer/1
mise exec -- uv run swingset dev-seed correction --scenario identity-correction
mise exec -- uv run swingset dev-drop correction
```

Replay leaves the seed unchanged and enforces its unit and time limits.
The named scenarios cover source parsing, event projection, identity correction
and a release-gate comparison. Development databases store their `dev` identity
inside Postgres. Real HTTP transports and publication refuse that identity;
the seeds use explicit fixture transports. Unsetting a shell flag cannot
turn a development database into production.

The default test command runs the reviewed core: one isolated deadline
recovery case, then the remaining cases in four clients with four workers
each and file affinity. Reports check that every selected case runs exactly
once, within the same 300-second budget. Focused selections and full runs stay serial unless you
explicitly supply worker flags. Run the files you changed
while iterating, then `mise run test -- --full-suite` for a migration, recovery,
backup or publication change. The [testing guide](testing.md) owns selection
rules and the measured runtime goal. Tests keep temporary data under `/tmp`,
where pytest prunes its old runs.

For a measured CPU investigation, set `SWINGSET_CPU_IMAGE` to the immutable
test-client image digest from an existing core report, then prepare a new
private scratch packet:

```sh
mise exec -- uv run swingset dev-diagnostic prepare /tmp/swingset-cpu-profile --image "$SWINGSET_CPU_IMAGE"
```

Review its `plan.json` before `dev-diagnostic run /tmp/swingset-cpu-profile`.
Preparation starts no actor. The default profiles the existing archived
scoring pipeline case, using the normal test service, client limits and
owned cleanup. It refuses source drift and packet reuse. Inspect the
structured report with `dev-diagnostic result /tmp/swingset-cpu-profile`.
The CPU ranking excludes suspended generators and overlapping cumulative
times; it is diagnostic evidence, not a normal-core timing result. The
scratch volume remains for reviewed cleanup.

For an elapsed-time investigation of the allowlisted uncertain-publication
case, add `--case uncertain-publication --mode waits` to preparation.
This mode records body elapsed and owner-thread CPU time and outermost
Postgres driver waits, with no SQL or arguments. Other-thread calls are
counted separately without clocks. Both modes retain the same 30-second
measurement and 120-second cleanup limits.

Use `--case publication-fixture --mode waits` to measure the full setup
of the changed-artifact publication case. Its ordinary test call and
assertions still run. The packet and result bind the measured `setup`
phase; a call-body measurement cannot satisfy that binding. The existing
named cases measure their call phase. Limits and cleanup stay the same.

Use `--case failed-build --mode cpu` for the failed-build retention case's
call phase. Both deliberate build failures and their retention assertions
still run; the profile does not measure successful candidate assembly.

Use `--case unlanded-publication --mode cpu` for the safety case that rejects
an obsolete publication intent, rebuilds and publishes through its offline
fixture. It measures the full call, including its required local backup checks.

Use `--case sql-guard --mode cpu` for the output-pack SQL architecture guard.
Its bounded parsing and naming aggregates overlap; never sum them. Every
source read and architecture assertion still runs.

Use `--case checkpoint --mode waits` or `--mode cpu` for the public
verification checkpoint case. The full call and activation assertions run.
Both modes require the same five major-phase observations and the capture
and backup-check children of checkpoint creation. Parent spans include their
children; never add their times together. CPU rankings cover the whole call,
not a particular child. Limits and owned cleanup stay the same.

## Format and review changes

Formatting only needs mise. Versions are pinned in `mise.toml`:

```sh
mise install
mise run fmt
jj status
jj diff
```

Use Jujutsu (`jj`) for version control. Commit or push only when requested.
See [repository instructions](../../AGENTS.md) for the workflow.

## Find the right module

The [code map](../how-it-works/code-map.md) links tasks to their entry points.
Fixtures live beside source adapters and under `tests/fixtures/`. Captured
fixtures and generated evidence must not be reformatted.

Parser and projector behavior changes need their declared version updates.
Source bytes and dependency identities are captured separately for audit;
a Python edit alone does not change a derivation recipe. Update the relevant reference page
when a change alters a contract; [writing guidance](../writing.md) explains where.

To regenerate enum documentation, run `uv run swingset enums --write` from the
repository root. The output is `docs/reference/enums.md`.
For installation on a worker, use the [deployment guide](deployment.md).
