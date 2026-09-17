# H16 event-preservation source preparation

Prepared on 2026-09-16. No assembly, Nix build, VM job, replay, deployment,
source acquisition or publication has run as part of this preparation.
The coordinator must review the allowlist before assembly.

## Exact base and permitted changes

Use only `/nix/store/4c4q8rkiz6jqizcy93bid6cz8mfvp20b-source`.
Its `h16-source.json` SHA-256 is
`71dc14101ca19880f3addfa2dc0073dbe50bce0e4e2be27d7bc91811b6d1e338`.
The assembler verifies every base file against that receipt and keeps the
receipt at `provenance/h16-before-event-preservation.json`. Existing provenance
and original investigation evidence remain unchanged.

Runtime overlay, and nothing else:

- `src/swingset/project/map.py`: preserve inventory-backed event collisions.
- `src/swingset/build/closure_rows.py`: retain valid event support alternatives.

Test overlay:

- `tests/test_map_event_retention.py` from the reviewed main workspace.
- `tests/build/test_closure_event_alternatives.py` from reviewed main.
- `tests/build/test_event_preservation_release.py` from this evidence folder.
  This standalone test imports the unchanged frozen `release_state` and
  `candidate_tables` helpers. It extracts only the new collision regression;
  it does not copy the current `test_h16_acceptance.py` or extension fixtures.

The only pyproject change adds
`norecursedirs = [".*", "__pycache__", "fixtures"]` to pytest options.
The base already has `pythonpath = ["src", ".", "tests"]`. The assembler
checks parsed TOML equality after this one change. Dependencies, version labels,
lockfiles, Nix files, config, overrides and schema 14 remain the base's bytes.
`project/process.py` is explicitly unchanged. The source-event completion,
pressure and schema 15–20 extensions are excluded.

## Prepared scripts

`assemble-source.py` accepts `--reviewed-files` with the exact destination-name
hash map in `reviewed-input-hashes.json`. Its worktree default is
`/Users/skeswa/repos/skeswa/swingset`; its new output default is
`/var/tmp/swingset-h16-event-preservation-release-source`. The standalone test
comes from this script's sibling file. It refuses existing output, unexpected
input hashes, extra changes and a different base. No build or activation is
part of assembly.

After root authorizes assembly and creates the immutable Nix source,
`verify-source.py` requires the actual new source path and receipt digest. It
independently checks the entire base/source difference, preserved prior receipt,
reviewed input hashes and dependency preservation. Optional `--mirror` copies
that exact source into a new host test mirror. Suggested mirror:
`/Users/skeswa/.cache/swingset-h16-event-preservation-tests-20260916`.
The verification receipt must be outside the source and mirror and is created
exclusively.

`validate-frozen.py` requires the new source/receipt pin, mirror, and the verified
source/mirror receipt with its actual hash. It executes host tools only. It first
collects all `tests`, then requires collection of the original completion and
proof-cache tests plus both new build regressions. It runs the true full suite,
Ruff and mypy, and verifies every frozen file again afterwards. All logs and the
validation receipt are new exclusive files in a supplied evidence folder.
Test counts are measured, not assumed equal to the earlier 1,199-test run:
pytest's former default excluded directories named `build`.

These scripts have no production-state, service-control or network operation.
The validation script does not perform a Nix build; root owns the separate
system build and service-source binding checks.

## Fresh replay binding after validation

The runtime recipe captures executable source bytes. Both runtime fixes and the
pyproject change therefore require a new accepted runtime bundle and normal
rederivation. Do not transplant or relabel old generations.

After the exact frozen suite and Nix checks pass, bind a new replay supervisor
and final verification to the actual new source path and `h16-source.json` hash.
Do not reuse the old bundle digest. Prepare a new scratch from the verified
original checkpoint `/var/lib/swingset/checkpoints/h15-before-h16-20260913`:

- Checkpoint manifest SHA-256:
  `700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444`.
- Original database SHA-256:
  `d81b2e459471c25c430a57c2a4d096180f1094e57c1b8b1e896d66774f12e8dc`.
- New scratch: `/var/tmp/swingset-h16-event-preservation-replay`.
- New receipts: `/var/tmp/swingset-h16-event-preservation-replay-001.json` onward.

Use the new source's unchanged `research/replay_derivations.py`, config and
overrides; pin `PYTHONPATH=SOURCE/src:SOURCE`,
`SWINGSET_REVISION=uncommitted:SOURCE_BASENAME`, the existing native library
paths, and `PYTHONDONTWRITEBYTECODE=1`. Retain the first actual bundle and marker
hashes. Continue only bounded project/link work with the existing 45-second
atomic limit, no explicit retries, and exclusive receipts. Production remains
held. Preserve all old scratches and failed build/audit evidence.

Only after authoritative currentness and a released writer lock may root create
`/var/tmp/swingset-h16-event-preservation-build` as a separate build clone.
Candidate reconstruction, full original acceptance audit, resource monitoring,
and independent verification remain required. A green test suite is not replay,
release, deployment or publication acceptance.
