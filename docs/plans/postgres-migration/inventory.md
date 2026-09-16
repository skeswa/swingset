# Inventory the source and freeze the inputs

Part of the [PostgreSQL migration plan](README.md). These are target
requirements, not evidence that migration has happened. Keep the numbered
steps in order and preserve the recorded operating holds.

## 4. M0 — Freeze contracts and inventory

1. Record `jj status` and byte hashes for existing modifications. Read
   `AGENTS.md`, the design contract index, operating handoff, and this plan.
   Create status rows M0–M9 initially `not_started`.
2. Resolve stable Python 3.12, PostgreSQL 18, uv, and Psycopg build inputs once.
   Pin image manifest/platform digests, dependency resolution, and client major.
   Record image architecture. Native `linux/amd64` is required for production;
   ARM64 may run developer tests, with separate receipts. No floating tags in
   the rendered deployment. Do not reuse the research ARM64 digest by assumption.
   Resolve/install the controller's pinned CLI/Node/npm tooling and record the
   local help, package integrity, and command map under the CLI contract above.
   With access available, validate list and parameterized reads; this does not
   grant M0 permission to create or deploy applications.
3. Build empty legacy SQLite schemas 14 and 15 using isolated fixture helpers
   extracted from the current migration lifecycle, including dynamically
   installed triggers. Do not open a production path with that helper.
4. Generate a schema manifest listing every table, ordered column, declared
   type, observed affinity, default, nullability, primary/unique key, index,
   foreign key/action/deferral, trigger SQL, and sequence. Record SQL hashes.
   The research found 71/74 tables, 82/87 explicit indexes, and 290/299 triggers
   for schemas 14/15. Differences must be explained by source changes before
   updating fixtures; counts alone are not a parity test.
5. Generate a Python/SQL call-site ledger by AST and text inspection. Every
   persistence caller, script, test fixture, SQLite exception handler, PRAGMA,
   implicit transaction, `rowid`, `lastrowid`, JSON expression, date expression,
   and dynamically composed identifier gets an owner and replacement category.
   Use the ledger to close the port; do not rely on a blind global replacement.
6. With access available, inspect the actual source package, active/persistent
   Nix profiles, state path, all six unit definitions/states, hold files, and
   public head. Record exact source/runtime/schema identity. Inspect sandile.dev
   architecture, Docker/Compose/Dokploy versions, existing resource allocations,
   volumes, free memory, disk, and inodes. These are observations, not mutations.

**Schema rule:** implement PostgreSQL storage migration `0001` as semantic
schema 14 and `0002` as semantic schema 15. Ordinary connections accept either
supported profile and never migrate. The first target uses `0001` only. A
fresh schema-15 source may use `0002` only when its already-deployed identity
and existing H16 checkpoint prerequisite are verified; never advance a
schema-14 source as part of relocation. Other source versions return code 3.

Schema capabilities are a checked, immutable mapping loaded once at connect:
schema 14 has derivations/controls and lacks `origin_backfill`; schema 15 adds
that capability. Replace table-existence probes with capabilities. Origin
dispatch already checks for table presence; preserve its inactive behavior
on schema 14. Other origin-only commands must fail before mutation with
`UnsupportedSchemaCapability`. Run common runtime tests on both profiles;
run origin tests on 15 and explicit disabled-origin tests on 14. This is two
PostgreSQL schema profiles, not two production database implementations.

The current source has undeployed work. Replaying its entire runtime is not
evidence of equivalence to the deployed source. Freeze both identities; use the
deployed source as the import/storage oracle and the checkout's frozen legacy
implementation as the engine-port differential oracle. Keep those results
separate. Production input acceptance remains held.

**M0 exit:** manifests, source ownership, port ledger, immutable local source
reference, schema profiles, and resolved image/dependency identities exist.
Missing external access does not block M1–M7.
