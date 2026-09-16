# Back up and restore PostgreSQL state

Part of the [PostgreSQL migration plan](README.md). These are target
requirements, not evidence that migration has happened. Keep the numbered
steps in order and preserve the recorded operating holds.

## 8. M4 — Complete PostgreSQL checkpoints and restores

Introduce manifest format `swingset-checkpoint-v2` with `engine: postgresql`,
semantic schema, storage migration checksums, dataset identity, source restore
generation, runtime/input identities, dump file/path/hash/size, tool versions,
artifact entries, baseline/pending intent, and creation time. Retain legacy
format readers. Do not include role passwords or connection strings. Unknown
manifest formats fail closed. Keep HF repository defaults as today; make
repository injection available to tests and checkpoint rehearsal.

Checkpoint algorithm:

1. Take artifact lock and A. Start a dedicated reader/backup connection in
   Repeatable Read, Read Only; export its snapshot. Enumerate database-referenced
   artifacts in that same transaction. Capture marker files under the relevant
   short filesystem locks; controls may continue in the database.
2. Run PostgreSQL-18 `pg_dump --format=custom --compress=gzip:6 --no-acl`
   with the exported snapshot into a temporary checkpoint directory. Keep its
   exporting transaction open until dump completion. Use a private libpq
   service/password file; never log secrets in arguments or subprocess errors.
3. Copy/hash the immutable artifact closure while A protects it against GC and
   baseline mutation. Include holds and report state. Validate the manifest,
   fsync, and atomically finalize. Close the snapshot promptly after dump and
   database closure enumeration complete; retain A through the existing upload
   lifecycle. The existing HF transport packs an uncompressed tar; compression
   belongs to the dump. Preserve its 20 GiB archive limit and require the
   measured packed checkpoint to stay below 18 GiB at rehearsal and cutover.
   Exceeding that threshold fails capacity acceptance; do not lift the bound or
   omit artifacts. Supporting larger checkpoints requires a separately tested
   versioned transport extension before cutover can proceed.
4. Upload to the private archive and verify the acknowledged commit and manifest
   hash through the existing transport. Only then record remote success. Retry
   the same completed local checkpoint after an ambiguous upload, reconciling
   its manifest first; never declare success from a local file alone.

Custom-format ownership is handled at restore, not by assuming dump-time
`--no-owner` removed it. Exported snapshots give dump/read consistency, but
sequence allocation is not snapshot-isolated: a concurrently advanced sequence
may restore above the snapshot's largest row ID. That is an allowed gap, never
a reason to lower it. Validate restored sequences against all retained IDs.
[PostgreSQL dump options](https://www.postgresql.org/docs/18/app-pgdump.html).

Restore algorithm:

1. Verify the remote/local manifest and every byte before installation. Provision
   a fresh database and artifact volume. Put an external activation hold in the
   supervisor configuration and `RESTORE_PENDING` in the volume.
2. Restore the dump with `pg_restore --exit-on-error --single-transaction
--no-owner --no-acl --role=swingset_owner` under maintenance credentials into
   that empty database. Provision `swingset_owner` as the NOLOGIN owner before
   restore, permit only the maintenance role to SET ROLE to it, and apply the
   documented grants afterward. Ownership/ACLs must come from this provisioning.
   Restore failures leave the target inactive and restart in a fresh database.
3. Install artifact files, run catalog and logical checks, and bind the new
   restore generation on both resources. Keep a phase journal outside the
   replaced resources so a crash between them is detected.
4. Use existing publication reconciliation against the actual public head.
   This phase may recognize an already-completed publication and repair local
   pointers under maintenance ownership; it must never issue a new Hub write.
   An unexpected remote head blocks installation rather than overwriting it.
5. Clear `RESTORE_PENDING` atomically only after the database/file identity and
   public reconciliation pass. Clear the external restore hold only after that
   postcondition is verified. Preserve the separate `operator-hold` throughout.

Port local checkpoint artifact recovery, checkpoint enumeration, and GC to
format dispatch. GC takes A, never deletes referenced generations/artifacts,
never removes the last verified complete checkpoint, and cannot delete an
in-progress checkpoint closure. A backup while controls change must restore
the snapshot-consistent earlier controls. Summary cursor writes must be locked
while captured; restored cursors must not skip a committed report interval.

**M4 exit:** new-format round trip, legacy import/recovery, interrupted restore
at each step, changed controls during snapshot, pending publication, missing
artifact, corrupt dump, failed remote upload, connection loss, and GC exclusion
all pass. Record measured dump/archive sizes; no deduplication claim is assumed.
