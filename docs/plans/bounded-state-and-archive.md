# Bounded operational state and durable history

Status: proposed, 2026-09-17. Steps 2 and 3 are implemented and tested offline
as of 2026-09-18; step 1's measurement ran the same day and its decision
([D-0166](../../journal/decisions/0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md))
is accepted: interning waits for rows that repeat, the migrations were
reordered on 2026-09-18 so step 3 can deploy alone
([D-0167](../../journal/decisions/0167-intern-derivation-payloads-in-the-last-migration.md)),
and step 4 has not started. The status line of each step below says what exists. This plan does not
allow deleting anything in production, deploying anything, or changing the
current retention rules.
[D-0119](../../journal/decisions/0119-bound-state-by-interning-and-one-closure.md)
records the direction. It replaces the earlier design in
[D-0117](../../journal/decisions/0117-bound-operational-state-with-durable-archives.md).

This work follows the [history and recovery plan](history-and-recovery.md) and
the [recovery rollout](recovery/README.md). It does not change their gates. It
must keep the promises in [state](../reference/state.md),
[build](../reference/build.md), and [publishing](../reference/publishing.md).

## 1. The problem

The worker keeps a database of everything it has computed. Every time it
recomputes a batch of results, it saves a complete new copy of them in the
`derivation_rows` table, one JSON blob per row, even if almost nothing
changed. It never throws old copies away. The history of decisions and the
history of operations only ever grow too. The artifact collector can remove
files nothing points to, but nothing removes database history, and every
backup copies all of it.

The only numbers we have are for the whole database and the whole backup:

| Item                | Bytes         |
| ------------------- | ------------- |
| Specimen database   | 5,016,920,064 |
| Verified checkpoint | 8,770,615,161 |

Nobody knows which tables are big. This plan does not guess.

## 2. Why this plan helps

- **It attacks the real cause.** Storing each unique row once means a
  recomputation that changes ten rows out of ten thousand costs ten rows.
  Growth then tracks how much actually changed.
- **It makes "safe to remove" a fact, not a guess.** Something is needed if
  you can reach it from a thing we must keep, by following the real "built
  from" links. That question has one answer. Anything nobody can explain is
  never touched.
- **It puts a ceiling on backups.** Once old results live in the archive,
  backups carry only what the pipeline needs to run and recover.
- **It keeps every existing promise.** Source pages, decisions, published
  proofs, and the record of every computation stay forever. Old results are
  copied to two places before the local copy goes, and can be brought back.

## 3. Three pieces of a computation

Today a saved computation is one thing. This plan treats it as three pieces,
because each has a different lifetime:

| Piece                 | What it holds                                                   | Lifetime                                        |
| --------------------- | --------------------------------------------------------------- | ----------------------------------------------- |
| The label             | Who computed it, from which inputs, a fingerprint of the result | Forever. This plan never deletes it             |
| The data              | The result rows themselves                                      | Kept locally, or archived in two places         |
| The "current" pointer | Which computation each part of the pipeline is using right now  | Owned by the pipeline; cleanup never touches it |

The label is tiny, so keeping it forever is cheap. Keeping it also avoids two
problems. The `previous_generation_id` column is a foreign key, so deleting
labels at any cutoff would break the chain. And rebuilding an old result
needs the full chain of labels to explain it. In database terms the label is
the header row in `derivation_generations`, the list of things it was built
from, and its row references. The data is the row bytes. Cleanup only ever
decides where the data lives.

## 4. Safety rules

Every step must keep these rules:

- Source pages, extracts, input bundles, and manual evidence stay stored by
  hash and checkable by hash. Nothing in this plan deletes them.
- A published dataset can always be rebuilt from its inputs, its runtime,
  the decisions, and the list of everything it was built from.
- Accepted identity decisions and their history are never changed or removed.
- Labels are never deleted.
- Restoring a backup works with no network for everything current and
  everything kept locally. Archived data needs the archive, and the backup
  contract says so.
- No step removes the only checked copy of any data. If a step fails halfway,
  there are extra copies, never fewer.
- Holds, in-progress publications, open investigations, and recovery markers
  keep everything they need, including everything those things were built
  from.
- A new "must keep" marker is accepted only when everything it needs is
  already in the live database. A marker alone never claims data that is not
  there.
- Removal happens only from a written plan, under the pipeline's own locks.
  Being old never makes something safe to remove.
- Evidence is never rewritten to make a failed step look successful.

Never deleted by this plan: blobs, extracts, input bundles, manual evidence,
release closures, candidate manifests, identity decisions and their journal,
accepted source generations, and labels.

## 5. Step 1: measure

This is an investigation, not a code change. Use a throwaway copy of a held
backup. Never use production.

Write down:

- bytes per table and index, from SQLite `dbstat`;
- how many computations, rows, and data bytes each stage and area has;
- how many distinct rows there are compared to total rows, per stage; and
- how long a backup and a restore take, and how much free disk they need.

The distinct-to-total ratio predicts how much step 2 will save before any
code is written. Keep one small receipt under `journal/evidence/`. Leave big
reports in scratch storage.

The tool is
[`journal/tools/runtime/measure_state_storage.py`](../../journal/tools/runtime/measure_state_storage.py).
The [investigation](../../journal/investigations/2026/state-storage-measurement-2026-09-18.md)
has the numbers. The measurement ran on 2026-09-18 on a scratch restore of
held checkpoint 004: rows do not repeat yet, `derivation_rows` and its indexes
are 27% of the file, and the step 2 migration makes the file 6% larger on that
copy. [D-0166](../../journal/decisions/0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md)
is the accepted decision this step asks for. Steps 2 and 3
were built ahead of the numbers, recorded in
[D-0158](../../journal/decisions/0158-build-steps-2-and-3-before-the-measurement-and-gate-the-rest-on-it.md).

**Done when:** the counts match the copy, every table is accounted for, and a
decision record says which later steps to do and in what order. If result
rows are not the biggest cost, that record says what is and adds a step for
it instead of guessing here.

## 6. Step 2: store each unique row once

One migration replaces the inline data column with two tables:

- `derivation_payloads(payload_sha256, payload_json)`: one row per distinct
  piece of row data; and
- `derivation_row_refs(generation_id, ordinal, table_name, record_key,
payload_sha256)`: which computation uses which row data, in what order.

A view named `derivation_rows` joins them, so the code that reads results in
`build/closure_rows.py`, `build/generations.py`, and `state/derivations.py`
does not change. The fingerprint is the same `sha256` of the canonical row
data already computed in `state/derivation_dependencies.py`.

Writing rows moves out of the completion code into one small function,
`retain_output`. It inserts any missing row data and the row references for
one computation, then checks the result against the label's `output_digest`
and `row_count`. It never touches a "current" pointer or scheduling state.
Completion calls it after its existing checks and then moves the pointer as
it does today. Step 4 uses the same function to bring data back. Completion
gets no special "historical" mode.

Row references are permanent, like labels. The no-delete triggers move to the
reference table. The row data table gets a delete trigger that allows only a
transaction carrying a permission row, the same pattern as
`removal_authority` on `source_generations`. That row cannot commit: a deferred
foreign key to an always-empty table makes a transaction that still holds it
fail, so the permission can never be left switched on
([D-0136](../../journal/decisions/0136-make-a-payload-removal-grant-impossible-to-commit.md)).

The migration runs last, after step 3's two, so step 3 deploys on its own. It
fills both tables, recomputes every `output_digest` through the
view, and drops the old table only when every fingerprint matches. Until that
drop it can be undone. Dropping the old table frees pages inside the file but
does not shrink the file, so the reclaim step in section 7 runs right after.

After this, a recomputation that changes little costs little. This is the
whole of what "chunk sharing" and "deltas" meant in the old plan, with no
chains to manage.

Identity and operating history are not touched here. If step 1 shows they
matter, the same trick applies to their data columns under a new decision.

**Done when:** every `output_digest` is unchanged; the current published
dataset rebuilds to the same manifest hash; all derivation, closure, build,
backup, and restore tests pass; the file is shrunk after the drop; and both
the bytes in use and the file size are measured before and after on the same
copy.

**What exists as of 2026-09-18.** This step is implemented and tested offline
and is the **last** migration, number 32, so step 3's schemas deploy without it
([D-0167](../../journal/decisions/0167-intern-derivation-payloads-in-the-last-migration.md)).
Migration 32 fills the two tables, recomputes every `output_digest` through the
view, and drops the old table only when all of them match; a migrated fixture
keeps every digest and row count; derivation, closure, build, backup and restore
tests pass; and bytes in use and file size are measured before and after on the
same copy. Shrinking the file is `gc --reclaim`, which section 7 added. The
published dataset has not been rebuilt, because nothing is deployed.

## 6a. Step 2a: attribute row indexes and source-generation JSON

[D-0166](../../journal/decisions/0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md)
adds this measurement before any redesign of the next largest storage costs.
Use the same closed disposable copy as step 1. Report each derivation-row index
with its exact `dbstat` bytes and key definition. For the four JSON columns in
`source_generations`, report logical UTF-8 bytes and row-size distributions
without retaining any body. Logical column bytes are not physical page bytes;
do not claim that removing a column would save the same number of bytes.

The 2026-09-18 follow-up found that the two inline `derivation_rows` indexes use
492,802,048 bytes. The uniqueness index on generation, table and record key is
342,245,376 bytes; the generation-and-ordinal primary-key index is 150,556,672
bytes. The four source-generation JSON columns carry 686,890,362 logical bytes
inside a 720,551,936-byte table. `report_json` is largest at 386,379,672 logical
bytes, followed by `result_json` at 173,890,464, `recipe_json` at 109,815,446,
and `manifest_json` at 16,804,780. The table's indexes total only 10,866,688
bytes. See the [measurement](../../journal/investigations/2026/state-storage-measurement-2026-09-18.md#storage-driver-follow-up-2026-09-18).

No schema rewrite follows from those figures alone. Before proposing one,
inventory the queries and integrity rules served by both derivation indexes,
and explain whether any source-generation JSON can be reconstructed, shared or
stored more compactly without weakening retained evidence. Prototype a concrete
layout on a disposable copy and compare integrity, query plans, migration time,
file size and backup size. Put an agent recommendation in an investigation; a
lasting schema choice needs an owner decision record.

**Done when:** the physical index and logical JSON measurements are retained;
the relevant query and integrity uses are accounted for; at least one concrete
layout is measured on a disposable copy; and an owner decision selects a change
or records that the current layout stays.

**What exists as of 2026-09-18.** The attribution tool and its focused tests are
implemented, and its run against the step 1 schema-29 copy passed every gate.
The query inventory, layout prototype and owner decision have not started.

## 7. Step 3: work out what is needed, then remove the rest carefully

Move `_artifact_closure` and `garbage_collect` out of `backup/checkpoint.py`
into their own module. That module does one walk over the "built from" links
and produces two lists from it.

**Starting points.** These are read from state that already exists, so there
is no separate list that could drift out of date:

- the baseline link and any pending candidate, together with the lists of
  everything they were built from;
- every "current" pointer;
- open findings, through the fingerprints they declare;
- the restore-pending marker and the operator hold;
- explicit hold files under `state/holds/`, one per hold, with who, why, and
  when, created under the control lock; and
- the recent window: the last N successful computations per area.

**The walk.** From every starting computation, follow the list of things it
was built from, add each of those, and keep going. Reuse the code that reads
those lists in `build/closure_manifest.py`. Do not reuse the release
selector in `build/closure.py`. That selector refuses two computations for
one area, and a window with N above 1 needs exactly that. Even N of 1 can
pair a current result with an older baseline result for the same area. The
walk collects by computation ID and allows many per area. The one-per-area
check stays where it is, in release validation. Do not add a "cleanup mode"
to the release selector.

The window only picks starting points. It never replaces the walk. So a
recent linking result keeps the older project result it was built from, even
if that area has moved on since.

**Two lists.**

- **Must be kept somewhere:** the data of every computation that has a label.
  The label and row references say who owns the data, so ownership is never
  in doubt. The data must exist in at least one checked place, local or
  archived, forever. This includes ordinary superseded computations that no
  release ever used. Once they leave the window they can be archived. They
  are not "unknown".
- **Must be kept locally:** everything the walk reaches from a starting
  point. Every starting point has to restore with no network: current
  pointers, pending candidates, the baseline, open findings, holds, recovery
  markers, and the recent window. This data stays in the live database, so
  every backup carries it. Older releases are not on this list.

Data on the first list but not the second can be archived and then removed
locally in step 4. "Unknown" is kept for records whose owner cannot be
worked out: row data that no row reference names, a file that nothing
declares, or a reference whose label is missing. Doctor reports those and
nothing removes them until someone works out what they are.

**Older releases.** Every release's "built from" list and candidate manifest
is kept forever as proof. Nothing here deletes one. But the computations an
old release used are not kept locally once that release is no longer the
baseline or a pending candidate. If they were, ordinary publishing would pin
every result ever released, and the plan would achieve nothing. An old
release can still be rebuilt: `gc --restore --release <candidate id>` reads
its list, brings back every archived computation on it, and only then does
the byte-for-byte rebuild promise in [publishing](../reference/publishing.md)
apply. A test must rebuild a release whose data was archived. That test belongs
to step 4: it needs `gc --restore`, which does not exist, and data that has been
archived, which nothing does yet. It is the "old release's data is archived"
row of section 10.

**Accepting a new starting point.** Creating a hold, or naming a
computation in a finding, runs the walk for that item first. A hold takes the
control lock; a finding is written in the caller's write transaction, under the
writer lock. Apply holds both, so neither can appear between its final check and
its removal. If any data it reaches is archived rather than local, the request is
refused, and the refusal lists the computation IDs to bring back first with
`gc --restore`. A marker is never written for data that is not there. The
baseline and pending candidates need no such check, because they are always
on the local list and never archived.

**Plan.** `gc --plan` writes a JSON file naming every piece of row data and
every file, which list it is on, and why, plus a fingerprint of the whole
plan. It removes nothing. On unchanged state it produces the identical file.

**Apply.** `gc --apply <plan fingerprint>` does one thing, in this order:

1. Take the writer lock, then the control lock, in that documented order.
   Creating a hold and admitting a candidate take the control lock too, so no
   new starting point can appear between the final check and the removal.
2. Recompute the plan while holding the locks. Stop if the fingerprint
   differs.
3. In one database transaction: write a note of this apply keyed by the plan
   fingerprint, write the permission row, remove the eligible row data, remove
   the permission row, commit. The permission row has to go before the commit,
   or the commit fails.
4. Still holding both locks, remove the eligible files. A waiting hold
   creator cannot take the control lock and pin a file that is about to go.
   This part is safe to rerun. If it crashes, the note says which files were
   planned, and the next apply finishes them from a fresh plan.
5. Release the locks. Write one receipt from the note.

Running apply again with the same fingerprint returns the recorded receipt
and changes nothing. Running it with an old fingerprint after a successful
apply stops, because the state has changed. The database cannot undo a file
removal, so the note commits before any file goes.

Until step 4 exists, apply removes no row data. It removes only the files a
plan calls removable, with a plan, locks, and a receipt. That is the disposable
candidate directories the collector used to drop on age. A file nothing declares
is not one of them: it is unknown, and unknown is never eligible.

**Shrinking the file.** Deleting rows does not shrink the database file.
SQLite keeps the freed pages on a free list and reuses them later, so a file
that is already over the cap stays over it. Dropping the old table at the end
of step 2 does the same. The plan therefore tracks two numbers:

- **bytes in use:** pages that hold data, from `dbstat`; and
- **file size:** the size of `state.sqlite` on disk, which is what the cap in
  section 9 and every backup actually measure.

Doctor reports both, plus the free-list bytes that could be given back. A
separate command, `gc --reclaim`, closes the gap:

1. Check free disk. Rewriting the file in place needs about twice the current
   file size free, because SQLite builds a temporary copy and keeps a journal
   of the rewrite. Refuse before touching anything if the space is not there.
2. Take the writer lock, then the control lock. Run
   `PRAGMA wal_checkpoint(TRUNCATE)`, as backup creation already does, so the
   rewrite starts from a file with no pending changes.
3. Run `VACUUM` in place on the one writer connection. No second database
   file, no swap, no second install path. SQLite's own journal makes the
   rewrite all-or-nothing: a crash or kill at any point rolls back to the
   original file the next time it is opened. Readers such as doctor keep
   reading a consistent view. A pending write from another connection makes
   `VACUUM` fail; the command reports that and stops.
4. Run `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and the schema
   check from `verify_checkpoint`. Run `wal_checkpoint(TRUNCATE)` again so the
   write-ahead log does not hold the old size. Release the locks.
5. Write one receipt with bytes in use, file size, write-ahead log size, and
   free-list bytes, before and after.

Automatic vacuuming stays off. It changes the page layout and still needs a
full `VACUUM` to turn on. Reclaim runs after the step 2 drop and after any
apply whose receipt shows enough free-list bytes to matter.

**Doctor** runs the planner in report mode and shows unknown row data, by
count and by item, bytes that could be given back, and why each local
computation stays.

**Declared references.** The fingerprints a finding relies on move into rows
in `finding_support` instead of being found by pattern-matching the
evidence JSON.

The one-day `older_than` value moves from a constant in `cli.py` to a policy
value.

**Done when:** the plan is the same every time on unchanged state; removing
each kind of starting point frees only what depended on it alone; a walk
with N of 2 accepts two computations for one area; a superseded computation
outside the window can be archived and is not "unknown"; a test shows the
locks are held from the final recompute through the last file removal, so a
hold arriving after the database commit waits and then sees a fresh plan; a
starting point added between plan and apply makes apply stop; a hold that
needs archived data is refused with the computation IDs; rerunning with the
applied fingerprint returns the same receipt; a crash after commit but before
file removal is finished by the next apply; every backup, verify, and restore
scenario passes.

**What exists as of 2026-09-18.** This step is implemented and tested offline.
`state/retention.py` owns the file closure and the walk; `gc --plan` writes the
plan; doctor reports both lists, the unknown items, and usage against both
knobs; `hold add`, `hold list` and `hold remove` place and read holds under the
control lock, with the residency check; findings declare their support in schema
30 instead of being scanned; and the two knobs plus the collector's age floor
are policy values read from an optional `[retention]` table in
`config/sources.toml`, with the defaults in `config.py`.

A review on 2026-09-18 found seven defects in this step, and all are fixed. The
rules they settled: a declaration replaces a finding's references and an empty
one leaves them alone
([D-0160](../../journal/decisions/0160-an-empty-declaration-never-clears-a-findings-recorded-references.md));
the residency check covers what a write newly declares
([D-0162](../../journal/decisions/0162-the-residency-check-covers-only-newly-declared-generations.md));
a requirement declares nothing and relies on the snapshot pin
([D-0161](../../journal/decisions/0161-a-requirement-finding-relies-on-the-snapshot-pin.md));
`create_checkpoint` takes the control lock across its closure and copy, so a
hold cannot be written between them
([D-0164](../../journal/decisions/0164-a-checkpoint-holds-the-control-lock-across-its-closure-and-copy.md));
an expired pause is not an existing pause
([D-0159](../../journal/decisions/0159-an-expired-pause-is-not-an-existing-pause.md));
and every value of the `[retention]` table is checked where it is read
([D-0163](../../journal/decisions/0163-every-retention-value-is-checked-where-the-table-is-read.md)).

`state/retention_apply.py` owns removal. `gc --apply <digest>` takes the writer
lock and then the control lock, recomputes the plan under both, stops if the
digest moved, commits a note in `retention_applies` (schema 31), removes the
eligible files, and writes one receipt under `state/gc/receipts/`. A note whose
files never went is finished only through the fresh plan: the apply removes the
files that plan still calls removable and leaves the rest, which its receipt
lists ([D-0151](../../journal/decisions/0151-a-resumed-note-removes-only-what-the-fresh-plan-still-names.md)).
`gc --reclaim` rewrites the file in place and writes its own receipt. The old
direct collector is gone: `gc` without a flag prints the plan summary and points
at `gc --plan`. Nothing inside the retention closure is removed without a written
plan, its digest and both locks. Two things outside it still clean up after
themselves: writing a plan drops all but the ten newest written plans under
`state/gc/plans/`, and the backup command prunes its own checkpoints under
`state/checkpoints/`. Both directories are excluded from the closure, so neither
is ever a root and neither holds anything a plan names
([D-0143](../../journal/decisions/0143-an-undeclared-file-is-unknown-in-the-plan.md),
[D-0152](../../journal/decisions/0152-the-backup-command-prunes-its-own-checkpoints.md)).
Removing a file nothing declares went with the collector; those are unknown, and
unknown is never eligible. A receipt accounts for every file its plan named:
removed, skipped, or already gone.

No payload bytes are ever removed yet. The gate is there and is shut: no
generation is eligible until the `archived_generations` table of step 4 exists
and names it. The plan says so per generation, so the digest an operator reviews
covers it and archiving anything after that review stops the apply
([D-0155](../../journal/decisions/0155-the-plan-digest-covers-what-is-archived.md)).
`gc --restore` is still to come. Nothing here is deployed.

## 8. Step 4: archive, remove locally, and bring back

Not started as of 2026-09-18. It waits on step 1's numbers and on the two object
stores, which do not exist yet. Step 3 built the gate it will open:
`archived_generations` is the table the eligibility check looks for, and finding
nothing there is why no payload byte is ever removed today.

Do this step only if the numbers from steps 1 and 2 are still above the
accepted database size cap.

**The archive object.** One object per computation: its row references and
the row data they name, written in canonical form and gzipped, named by its
`sha256`. Objects are written with the existing store and check helpers in
`fetch/archive.py`, but into `state/archive/`, not `state/blobs/`. That
directory is added to the backup exclusion list, so backups never copy
archived history and the file walk never pins it.

**Two copies.** Two separate object stores, neither of which is a backup.
A backup leaves archive objects out, so it cannot count as a copy. The first
store is the off-site bucket recommended by the
[object storage investigation](../../journal/investigations/2026/dokploy-object-storage-2026-09-14.md).
The second is a separate place that fails on its own; the step 1 decision
names it.

**Finding an object again.** Neither a computation's ID nor its
`output_digest` tells you the hash of its archive object, so two tables record
it before any data leaves:

- `archived_generations(generation_id, object_sha256, archived_at)`: one
  permanent row per archived computation; and
- `archive_replicas(object_sha256, store, locator, verified_at)`: one row per
  checked copy. The locator must be enough to fetch the object without
  listing the store.

Both tables live in the database, so a restored backup carries them.

**Order of removal.** For each eligible computation: write the object to
`state/archive/`, upload it to both stores, read both back and check the
hash, then in one transaction write the `archived_generations` row and both
copy rows. Only then may the next plan mark it eligible. Apply removes row
data only for computations with an `archived_generations` row and two
checked copy rows, and removes the staging copy only after that removal
commits. A crash at any point leaves at least the local copy.

**Bringing data back.** `gc --restore <computation id>` reads the
`archived_generations` row for the object hash, then the copy rows for where
to fetch it, fetches from either store, checks the object hash, and calls
`retain_output` from step 2. The label and row references are still there, so
the function checks the restored data against the label's `output_digest`
and `row_count`. No "current" pointer and no scheduling state changes.
Restore must work from a restored backup's tables and the two stores alone,
with `state/archive/` absent.

The backup contract in [state](../reference/state.md) gains one sentence: a
backup restores current state and everything kept locally, with no network;
archived data needs the archive.

**Done when:** losing either store still allows restore from the other;
corruption, truncation, a wrong object, and an interrupted write are all
detected without removing anything; a representative archived computation
from each stage restores and matches its `output_digest`, using only a
restored backup's tables and the two stores; a restore leaves every pointer
unchanged; a hold requested for an archived computation is refused, restore
brings the data back, the hold is then accepted, and a backup taken after
that restores with no network and the held data present; the current
published dataset rebuilds byte for byte.

## 9. Two knobs

Two policy values, captured in the input bundle:

- the maximum size of the live database file on disk, in bytes; and
- how many recent successful computations to keep per area, the window N.

They are read from an optional `[retention]` table in `config/sources.toml`, and
an absent table means the defaults. So the bundle captures the values in force,
as `policy/retention.json`, not only the file they were read from: a reader of an
old bundle can then say what the cap was. Changing either one recomputes
nothing. No stage's recipe selects the file or the values, so raising the cap and
deploying never recomputes the history the cap is there to bound
([D-0156](../../journal/decisions/0156-capture-the-retention-limits-as-values.md)).

Both defaults are provisional until step 1 runs
([D-0158](../../journal/decisions/0158-build-steps-2-and-3-before-the-measurement-and-gate-the-rest-on-it.md)).

Doctor reports usage against both, showing bytes in use, file size, and
free-list bytes separately. Crossing the size cap sets an operator pause
through the existing controls. Reading, controls, and recovery keep working.
Shrinking the file needs free disk of about twice the current file size, so
the cap must be set with that headroom in mind.

## 10. Tests that must exist

The work is not finished until each of these has been tried:

| What happens                                                          | What must be true afterwards                                                   |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| The step 2 migration fails after filling the new tables               | Old table intact; every fingerprint still checks                               |
| The plan is recomputed on unchanged state                             | Same file, same fingerprint                                                    |
| A starting point is added between plan and apply                      | Apply stops; nothing changed                                                   |
| A starting point is added while apply holds the locks                 | It waits until apply finishes; the next plan includes it                       |
| A hold arrives after the database commit, before file removal         | It waits; apply finishes its files; the hold then sees a fresh plan            |
| A superseded computation leaves the window with no dependents         | It can be archived; it is not "unknown"                                        |
| The window is 2 and one area has two recent computations              | The walk keeps both; release validation is unchanged                           |
| A hold is requested for a computation whose data is archived          | Refused with the computation IDs; nothing written                              |
| Archive, then restore, then hold, then backup, then restore offline   | The held data is present after the offline restore                             |
| Restore runs from a restored backup with the staging directory absent | The object is found through the two tables and fetched from a store            |
| An old release's data is archived, then the release is rebuilt        | Restore brings back its whole list; the rebuild is byte for byte               |
| Row data is removed and reclaim has not run                           | File size unchanged; doctor shows the bytes as free-list                       |
| Reclaim is killed partway through the rewrite                         | Next open rolls back; integrity passes; bytes unchanged; next reclaim succeeds |
| Reclaim runs while doctor holds a read connection                     | Doctor reads a consistent view; reclaim completes                              |
| Reclaim runs while another connection has a pending write             | Reclaim reports it and stops; nothing changes                                  |
| Reclaim completes                                                     | File and write-ahead log shrink to bytes in use; next backup too; checks pass  |
| Reclaim starts with too little free disk                              | It refuses before writing anything                                             |
| A recent linking result depends on an old project result              | The project result stays local because the walk reached it                     |
| Apply crashes before the transaction commits                          | No row data removed, no file removed, no note written                          |
| Apply crashes after commit, before file removal                       | Receipt available; the next apply removes the files                            |
| Apply is rerun with the applied plan fingerprint                      | The recorded receipt is returned; nothing changes                              |
| Apply is rerun with an old fingerprint                                | It stops                                                                       |
| An unexplained piece of row data or reference appears                 | Doctor reports it; it is never eligible                                        |
| An archive upload stops midway                                        | No copy row; the computation stays ineligible                                  |
| One store is missing or corrupt                                       | Removal stops; restore uses the other checked store                            |
| Data is restored after removal                                        | Row data matches the label fingerprint; pointers unchanged                     |
| A backup is taken after removal                                       | It restores with no network, leaves archive objects out, and checks out        |
| The database file reaches the size cap                                | An operator pause is set; controls and recovery still work                     |

Every row above is covered by an offline test except the seven that need an
archive: the two restore scenarios, rebuilding an archived release, an
interrupted upload, a missing or corrupt store, and the two that follow a
removal of row data. Those are step 4's, and step 4 has not started.

## 11. Rollout and evidence

No step may claim it is deployed or accepted because local tests pass. Record
implemented, tested, deployed, and operating separately in
[current status](../status.md).

Production rollout uses the same hold, backup, source, and restore controls
as other state migrations. Before the first removal of row data on
production:

- steps 1 through 4 are complete and both stores are checked;
- a backup taken just before is verified;
- the exact plan has been reviewed and its fingerprint written down;
- a restore from that backup passes; and
- rollback space is reserved outside the production state directory.

Keep one small receipt per apply and per reclaim. Point at existing manifests
and evidence by hash instead of copying them. Files over 1 MiB follow the
[evidence archive rules](../../journal/evidence/README.md).

## 12. PostgreSQL

This plan does not depend on the [PostgreSQL migration](postgres-migration/README.md).
The new tables are ordinary tables the importer copies. Starting points are
read from existing markers, so the importer has nothing new to preserve. Do
not turn on removal of row data as part of a database-engine cutover.

## 13. What is not promised

- Step 2 helps only if rows really do repeat between computations. Step 1
  finds out.
- The window N is a judgment call. Nothing can prove how many recent
  computations an investigation will need. The walk guarantees that what is
  kept is consistent, not that N is enough.
- The two stores do not exist yet.
  [D-0106](../../journal/decisions/0106-archive-large-files-and-scrub-local-history.md)
  records that the external archive is checked locally and not copied
  off-site. Step 4 cannot start until both exist.
- Labels and row references still grow, at a few hundred bytes per row. Step
  1 measures whether that matters.
- Shrinking the file needs free disk of about twice the current file size
  while it runs, and holds both locks for the whole rewrite.
- The planner walks all reachable history on every backup and every doctor
  run, as the file walk does today.

## 14. Finished when

Three of these cannot be reached from a machine with no production data and
nothing deployed. They are marked, so nothing here is mistaken for done.

- step 1 has a receipt and a decision naming which steps ran; **met on
  2026-09-18**
  ([D-0166](../../journal/decisions/0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md));
- step 2a accounts for the row indexes and source-generation JSON, then an
  owner decision records whether their layout changes; attribution is met,
  while the query inventory, prototype and decision remain open;
- each unique row is stored once and every fingerprint checks;
- one walk produces both lists, with a plan, a locked apply, a note of each
  apply, and a receipt;
- doctor explains local, archivable, and unknown bytes, and bytes in use
  against file size;
- the live database file, not just its rows, stays under the cap through
  sustained replay; **deferred to deployment.** Only a running system produces
  sustained replay. Offline, one test drives the file over a cap set to one byte
  and checks the pause;
- if step 4 ran, both stores check out and archived data comes back without
  moving a pointer; and
- scheduled restore drills pass; **deferred to deployment**, like the drills
  themselves.
