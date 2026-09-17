# Initializer 002 downstream bindings

Prepared offline only. No new file has been staged in production or executed
there. The frozen schema14 source, bundle, checkpoint, old drivers and failed
preparation evidence remain unchanged.

The new chain uses `initialize-002.py` from this folder,
`../supervise-initialization-002.py`,
`../monitoring/guard-initialization-003.py`, and
`../final-verification/verify-initialization-002.py`.

`downstream-adaptation-001.json` records each predecessor and new hash, every
literal replacement, and exact byte comparison. The supervisor changes one
driver hash; the guard changes one supervisor hash; the verifier changes two
hashes and two private helper filenames. Five test copies change only imported
file paths. The existing production release driver remains byte-identical at
`f8cd9ec23953600d3c9445a765df97eb894fd5363cea3f1136446c67a4f902cc`:
its initializer reference and receipt checks already bind a dynamic path/hash.

`downstream-offline-checks-002.json` records **180 passing tests** against the
frozen host mirror and clean runtime Pyflakes checks. These reuse the initializer,
supervisor, interruption, memory-guard and final-verifier suites plus unchanged
release and activation tests. Original and adapted hashes stayed unchanged.
The initializer's separate real legacy-state regressions are recorded in
`offline-checks-001.json`.

The first downstream check could not start pytest because resolving the venv
symlink selected its base interpreter. That failed receipt remains retained.
The successful retry uses the venv entry point and explicit frozen pytest
configuration. The copied launch-review test retains an existing unused import;
it was not edited to satisfy lint because only path substitutions were reviewed.

Root must review and authorize staging. Stage initializer002 and supervisor002
at the operation-directory root, guard003 under `monitoring`, and verifier002
under `final-verification`. Pass the new supervisor path to the guard and the
new initializer path/hash to the supervisor. Use fresh gate002, marker002,
prepare002, anchor and supervision/output paths. Both anchor and final checks
must execute the same verifier002 bytes. The separate prepare launcher also
needs new reviewed hashes, filenames, unit and capture directory; its owner is
preparing those changes.

Future production build/publish gates must reference initializer002, gate002,
marker002 and the actual terminal run receipt. Build and publish must retain the
same initialization chain. This preparation authorizes none of those actions.
