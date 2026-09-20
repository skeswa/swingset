# Research tools

Maintained scripts that answer a question about the dataset or the worker
without changing production. Run them as Python modules from the repository
root, inside the [development environment](../../docs/guides/development.md):

```sh
uv run python -m journal.tools.collection.wayback_coverage
uv run python -m journal.tools.releases.audit_candidate --help
```

Read a tool's docstring and linked investigation before using it. Existing
holds and source request rules still apply to anything that fetches.

## What lives here

A script that runs once belongs in scratch, not here. A script that runs
twice becomes a `swingset` subcommand with a test. Every tool kept here is
maintained and listed in its purpose README ([D-0177](../decisions/0177-keep-only-maintained-tools-in-the-journal.md)).
The one-off operation runners that used to live here, such as the H11 to H16
acceptance helpers, the schema-29 rehearsal and migration helpers, and the
exact DCN and Step Right fixture runners, were removed on 2026-09-20. Their
receipts under [evidence](../evidence/README.md) still describe what ran, and
the sources remain in history at commit `e136d95`.

- [Collection and historical sources](collection/README.md): build event inventories, examine archived pages, and run the finite phase-one intake.
- [Dataset quality](quality/README.md): inspect a saved dataset, explain missing results, and run the Jes Test.
- [Source interpretation checks](admission/README.md): run admission contracts offline and acquire one reviewed fixture.
- [Worker state and recovery](runtime/README.md): measure state storage, benchmark layouts on a disposable copy, and replay derivations on scratch.
- [Builds and releases](releases/README.md): audit a built candidate offline.

## Repository checks

- `mise run evidence-size` (`evidence_size.py`): tracked evidence sizes, the
  1 MiB per-file limit and the 200 KiB new-evidence budget per change. It
  writes no files.
- `mise run session-start -- --label <you>` and `mise run session-end`
  (`agent_session.py`): take and release the one agent-session lock for this
  checkout.

Inputs are grouped under `journal/evidence/`. Choose a new output path when
repeating an investigation; do not overwrite retained reports.
