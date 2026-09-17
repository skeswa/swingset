# Research tools

Choose a task, then follow its guide to the relevant script and evidence.
Run maintained tools as Python modules from the repository root, inside the
[development environment](../../docs/guides/development.md):

```sh
uv run python -m journal.tools.collection.wayback_coverage
uv run python -m journal.tools.releases.audit_candidate --help
```

The first example reads saved archive indexes offline. Other tools may write
local state, contact websites, or prepare production work. Read their docstring,
arguments, and linked investigation before using them. Existing operating holds
and source request rules still apply.

## Choose a purpose

- [Collection and historical sources](collection/README.md): Build event inventories, examine archived pages, and prepare bounded source collection.
- [Dataset quality](quality/README.md): Inspect a saved dataset and explain missing or conflicting results.
- [Person matches](identity/README.md): Prepare judge evidence and representative identity reviews.
- [Source interpretation checks](admission/README.md): Review a parser’s interpretation before it can replace saved evidence.
- [Worker state and recovery](runtime/README.md): Check migrations, controls, scheduling, checkpoints, and isolated replay.
- [Builds and releases](releases/README.md): Check candidates and correction releases against their supporting evidence.

## Maintained tools and retained scripts

Run `mise run evidence-size` to inspect tracked evidence size and large exact
duplicates. It rejects any tracked file over 1 MiB and writes no report files; follow the
[retention rules](../evidence/README.md#keep-new-evidence-small) before adding output.

These tools use the current repository layout. Captured scripts under
[evidence](../evidence/README.md) preserve exactly what ran before and may name
old source paths or pins. Their old receipts do not certify a changed tool.

Inputs are grouped under `journal/evidence/`. Choose a new output path when
repeating an investigation; do not overwrite retained reports. The two original
CSV builders keep their historical input/output conventions, described in the
[collection guide](collection/README.md).
