# Import and audit a complete checkpoint

Part of the [PostgreSQL migration plan](README.md). These are target
requirements, not evidence that migration has happened. Keep the numbered
steps in order and preserve the recorded operating holds.

## 7. M3 — Immutable import and independent audit

Expose new `swingset-migrate inventory`, `import-sqlite`, and `audit` commands
through `migration/cli.py`. Each accepts `--run-dir`; inventory also takes
`--checkpoint`. Target secrets come from files, never command arguments.
Import requires an unused target identity and creates `RESTORE_PENDING` before
any database write. The supervisor always treats an absent identity or this
marker as inactive. Import never calls normal input acceptance or recovery.

### Source validation

Verify the legacy checkpoint manifest and remote commit, database file hash,
artifact closure, relative paths, sizes, and baseline symlink. Reject path
traversal and symlinks escaping the checkpoint. Require a completed standalone
SQLite backup with no unaccounted WAL; then open `mode=ro&immutable=1`.
Run integrity/FK checks. Verify both schema markers and the exact installed
schema/trigger fingerprint against the selected source profile. Do not trust
`user_version` alone or run migrations to make a mismatch disappear.

Inventory every column's actual storage classes and every maximum field size.
Reject NULL primary keys unsupported by the mapped target, embedded NUL/invalid
UTF-8 text, unsupported storage classes, out-of-range integers, non-finite
floats, and invalid JSON in fields whose contract requires JSON. Record a
bounded diagnostic locally; do not silently coerce, trim, replace, or skip.
Source type exceptions require a separate explicit contract change and recheck.
These are data incompatibilities, not automatic invitations to edit production.

### Loading and resumption

Create the destination database with no worker CONNECT grant. Install tables
and primary/unique keys from the selected baseline, excluding domain triggers
until after load. Defer attachment/validation of cyclic foreign keys until all
tables exist and rows are loaded. Preserve their final deferral/action rules.
Never set a global trigger-bypass mode on an activated database.

For each table, use an explicit column mapping and bounded reads. Ordinary
rows are copied with Psycopg COPY adaptation. Read large manifest payloads
with SQLite `blobopen`, emit bytea as hex through streamed COPY text blocks,
and escape other text according to COPY rules (`\\`, tabs, CR, LF, NULL).
Do not build the whole hex field in Python; bound output blocks to 256 KiB.
Test a literal `\\N`, empty text, NULL, mixed Unicode, and binary bytes. Fields
over 1 MiB use a bounded path; other rows fit a 4 MiB batch cap. Reject any
single value too large for the selected PostgreSQL representation before load.

Commit one table with its input fingerprint and import receipt in the same
transaction. A killed COPY rolls back that table; a rerun verifies completed
tables and resumes the incomplete table from the beginning. Never append
blindly after a lost commit response. If receipts/data disagree, keep the
attempt inactive and create a new target, retaining the failed one for diagnosis.

For each existing identity sequence set the next value above both the maximum
surviving ID and retained `sqlite_sequence` high-water mark. For empty tables
with no high-water mark use `setval(sequence, 1, false)`; otherwise use the
greatest mark with `is_called=true`. Apply the same rule to new rowid-based
ordering sequences. Check signed 64-bit exhaustion. PostgreSQL sequence gaps
after rollback are allowed; committed ordering/identity values must remain
monotonic and never collide.

After loading: create and verify triggers, remaining indexes, and foreign
keys; validate all constraints; provision grants; run ANALYZE; verify catalogs
against the DDL manifest. A numeric trigger count is insufficient. Only the
maintenance process may connect during this stage.

Copy all required artifacts to the new volume using the existing closure
contract. Preserve report cursors, pending publication files, input holds,
and `operator-hold`. Exclude venvs, dependency caches, old lock files, and
transient checkpoints already represented by retained archive references.
Do not remove the original immutable checkpoint. Track every included or
excluded path and its rule; unknown state files fail the inventory gate.

### Audit encoding and identity

The independent auditor reads source and target; it does not trust importer
counts or run importer conversion functions to decide equivalence. Use the
shared documented _format_, with separate source and target readers:

- Sort rows by declared primary-key columns with text byte ordering and
  explicit NULL placement. Tables without a unique key use a preserved source
  ordering field or an externally sorted full-row encoding; list the choice in
  the table mapping. Stream the sort to disk when necessary.
- Encode each field as a one-byte type tag, unsigned 8-byte big-endian length,
  and payload. NULL has a distinct tag and zero length. Integers use signed
  decimal ASCII; doubles use big-endian IEEE-754 binary64; text uses exact
  UTF-8; bytes are exact. Normalize negative zero to positive zero only if
  the source's observed storage/read contract requires it, with a golden test.
- Prefix each row with its column count and frame each field. Hash the ordered
  row stream with SHA-256. Record count and digest per table. New ordering
  columns are compared separately to source rowids. Infrastructure rows are
  excluded from domain hashes and validated against their own manifest.
- The source manifest text and target bytea are compared as exact UTF-8 bytes
  under the same declared payload tag. A second pass verifies dependency IDs
  against their content hash. Bounded reads apply in the auditor too.

Require zero unexplained table/count/hash differences and zero invalid
constraints. Independently inspect active admissions, control revisions,
budgets, next-allowed times, source/parse cursors, identity journal positions,
selected generations, report cursors, baseline, pending intent, and historical
unfinished runs. Do not “repair” these before comparing them.

Generate a persistent UUID `dataset_id` for legacy state during import and a
fresh UUID `restore_generation` for each installation. Write both to the DB
identity row and `state-identity.json` in the artifact root. Record their
binding to the source checkpoint. Every ordinary DB open compares both values;
an absent/mismatched pair fails before mutation. Across future restores preserve
dataset identity and regenerate the installation generation under hold.

**M3 exit:** fixture schemas 14/15 import exactly; large fields stay bounded;
interruptions resume safely; deliberately mismatched DB/volume, corrupt bytes,
invalid rows, stale receipts, and missing files prevent activation.
