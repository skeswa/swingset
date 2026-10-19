# Stages, gates and rollback

The owner set four stages on 2026-10-19, and on the same day asked for a
stage 0 that stops code and environment changes from forcing re-derivation.
A **gate** ends each stage: a written check, with a receipt, that must pass
before the next stage starts. Production keeps running on SQLite on the Mac
until stage 4, and keeps publishing every day except during the switch
itself (stage 4). [The target design](target.md) describes the end state.

| Stage                                   | Where it runs             | Changes production?                                        |
| --------------------------------------- | ------------------------- | ---------------------------------------------------------- |
| 0. Prove changes instead of re-deriving | the Mac and the worker VM | once, a deploy that re-keys results and re-derives nothing |
| 1. Refactor locally and verify          | the Mac, in Docker        | once, in one batched deploy (step 1.8)                     |
| 2. Move files and backups to the cloud  | production (still SQLite) | yes, storage only                                          |
| 3. Dry run on Dokploy                   | the owner's server        | no                                                         |
| 4. Switch to Dokploy                    | the server                | yes                                                        |
| After the switch                        | the server                | decisions move into `gov`; the catalog fills               |

## Rules for the whole project

- **Owner's exception.** The repo normally allows release work or one
  maintenance session a week. The owner made this move the plan of record on
  2026-10-19. Daily automatic releases continue, except for a planned gap
  during the switch: hours for the import and audit after stage 0, where it
  would have been one to three days with a re-derivation.
- **Count every re-derivation.** Production must re-derive as few times as
  possible. Each stage's gate reports how many full re-derivations it caused
  and why, and which **differential proofs** it ran and what they found
  ([stage 0](stage-0.md)). After stage 0, a code or environment change
  re-derives a transform only when its **declared version** is bumped; a
  deploy whose proof shows no output change re-derives nothing. Code that
  only Postgres uses, including its migrations and the `gov` and `catalog`
  code, still lives in a package the scopes do not read (listed as excluded
  in `recipe_scope.py`, proved by the scope test), so changing it needs no
  proof either.
- **What waits.** The calendar contract activation, row-level result holds
  and the danceconvention.net and SwingDirector activations wait until after
  gate 4, unless the owner says otherwise. Keeping the worker safe never
  waits.
- **Keep both databases in step.** Once the Postgres schema exists (step
  1.3), every new SQLite migration ships with its Postgres twin and a test
  that both give the same rows. The core test suite stays SQLite-only, so it
  keeps its 3-minute limit. Postgres tests run in the full suite and in CI,
  against a Postgres pinned in the dev shell.
- **Review levels** (from AGENTS.md):

  | Step                                                                                                                         | Review needed                      |
  | ---------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
  | code that only runs locally                                                                                                  | tests and formatting               |
  | the batched production deploy (1.8), the storage switch (2.4), the switch (4)                                                | before and after receipts          |
  | deleting local files (2.6), deleting the Mac copies (4.6), publishing from the server (4.4), turning automatic publishing on | an independent check plus receipts |
  | the egress check (3.6), which contacts other sites                                                                           | a decision record first            |

## Stage 0: prove changes instead of re-deriving

**Goal:** a code or environment change never forces a re-derivation by
itself. A transform's declared version and its inputs decide what is out of
date; a differential proof, run on a clone before the deploy, decides
whether a change needs a new version; the worker refuses a deploy that
changes output without one.

The full step, its design, rules, measurements and rollback are on
[its own page](stage-0.md). In short:

1. **The proof runner** (`swingset prove`): runs the changed transforms over
   every stored input on a reflink clone, in parallel, and compares with
   the stored output under the cutoff's own equality. Old code runs only for
   units that differ, from the accepted bundle's `runtime/` tree.
2. **Declared versions** become the `recipe/code/*` values; the code and
   environment digests move to an `audit` consumer. Acceptance refuses a
   bundle whose audit digest changed for a scope without a bump or a clean
   receipt for that scope.
3. **Re-key** every current unit to the new recipe values with equivalence
   rows, so the switch itself re-runs nothing.
4. **Deploy** under a hold: prove the stage 0 tree against production's
   bundle, deploy, re-key, lift. Publish tier: independent check plus
   receipts.
5. **Scope splits** and **production throughput** follow as maintenance,
   the second only from a measurement receipt.

**Gate 0:**

- a whole-graph proof of production's deployed tree reports zero differing
  units, with its wall time recorded;
- the acceptance rule is tested: unbumped output change refused, bump
  accepted, clean receipt accepted, foreign-environment receipt refused;
- the stage 0 deploy's post-check shows zero units queued by the acceptance
  and a release built from unchanged pointers;
- an independent check signs off.

**Rollback:** switch to the previous system generation; the old code accepts
its old bundle and nothing re-runs. The re-key's rows are harmless extras.

## Before stage 1: check the server

The plan assumes room on a shared server that nobody has measured since
2026-09-14. Check it first, with read-only commands:

- CPU type (AMD64 or ARM64), cores, memory and free disk;
- what else runs there and how much it uses;
- Dokploy's version, and whether its login page has two-factor login, an
  address allowlist and current updates;
- whether other services' containers can reach a database on Dokploy's
  shared network.

The [cutover page](cutover.md) asks for about 420 GB free during the move.
If the server cannot give that, the plan changes before any code does.

## Stage 1: refactor locally and verify

**Goal:** the whole pipeline runs on Postgres on the Mac and builds the same
dataset as SQLite from the same evidence.

**Owner decision needed first:** the Postgres major version (it must match
the server).

1. **One database interface.** Every query goes through `state/db.py`.
   Review the 23 direct `sqlite3.connect` calls and the 120 queries built
   from strings. Keep plain SQL. Add a Postgres version of the interface
   (psycopg 3) that returns the same Python values as SQLite (design rule 2).
2. **Pin golden hashes.** Take real rows from production and record the
   hashes the code computes over them (inventory, corpus, selection,
   policies, generation ids, payloads). Tests require Postgres to give the
   same hashes.
3. **The schema in Postgres.** One starting migration that creates today's
   tables as they are, plus the locks, the generated fence triggers and the
   trigger rules in [target design](target.md#porting-todays-tables) and the
   [database port page](database.md) (sections 5.1–5.3).
4. **Build `gov` and `catalog` now, switch them on later.** Write the tables
   and commands and test them locally. They are not used by the pipeline
   until after the switch.
5. **The local stack.** Docker Compose with Postgres and MinIO standing in
   for object storage. The worker images are built for the server's CPU type
   (`--platform linux/amd64` if it is AMD64), even though the Mac is ARM64.
6. **Import a production checkpoint** with the dedicated importer
   ([import and audit](import.md)). Row counts and a per-table hash must
   match, and every file a row points to must exist.
7. **Run it all.** The test suite on Postgres; then the loops on the import
   with fetching off. Nothing re-derives by itself after stage 0: the port
   changes code the scopes read, but the declared versions are unchanged.
   Instead, run a whole-graph **differential proof** on the Postgres import
   against the imported labels, with the runner working over Postgres.
   Record its time and peak memory.
8. **One batched deploy to production (on SQLite).** Ship together, so
   production needs one proof and one deploy:
   - the database interface from step 1;
   - the psycopg dependency;
   - the storage interface and the marker rows from stage 2, switched off
     (stage 2 turns them on with a setting that re-runs nothing);
   - the audit-only environment identity D-0263 decided: the lockfile, the
     Python version and the CPU type, instead of the Python binary and every
     installed file. It no longer triggers anything; it says when a proof is
     due.

   Prove the batched tree against production's bundle first. A database
   port should change no transform's output, so the expected re-derivation
   is zero; a transform the proof shows changed is bumped and re-runs alone.
   Record the proof and any re-run with a receipt.

9. **Compare.** The proof in step 7 is the per-unit comparison: every current
   unit's output on Postgres must match its label from SQLite. Then build
   one release on each side from the same checkpoint and the same code.
   Every published table must be byte-identical, except for a named list of
   columns such as run ids. Any other difference blocks the gate unless an
   independent check accepts it.

**Gate 1:**

- the test suite passes on both databases;
- the import audit balances;
- the comparison is clean;
- the proof's time and memory on Postgres are recorded;
- step 1.8 is deployed with a clean proof, and any re-run it caused has
  drained;
- an independent check signs off.

**Rollback:** local work is simply abandoned. Step 1.8 is an ordinary
production deploy with its own rollback (switch back to the previous tree).

## Stage 2: move files and backups to the cloud

**Goal:** large files and backups live in object storage, on today's
production. The worker stops depending on the Mac's disk.

**Owner decision needed first:** the storage provider.

1. **Price it first.** Estimate monthly storage, uploads (backups twice a
   day), the second copy, and the WAL archive. A full re-derivation would
   write an estimated 30 GB or more of log; after stage 0 only a bumped
   transform's re-run does, so estimate from a registry parser bump, the
   largest. Measure the Mac's upload speed.
2. **Buckets:** one for files and one for backups, plus a copy with a second
   provider, made by a job with its own keys. The worker's keys cannot delete
   anything in the copy. The copy job is the first service on the server,
   because the Mac sleeps.
3. **Turn on the storage interface** shipped in step 1.8. It covers page
   bodies, extracts, packed objects, partitions and candidates: write-once
   uploads, a checked read, and a local cache with a size limit. One code
   path serves SQLite and Postgres. Turning it on is a setting, so nothing
   re-derives.
4. **Copy and switch reads** (a production change under hold, with
   receipts). A resumable copier uploads about 150k files plus the candidates,
   recording each key and hash. Reads then go to the bucket, with local files
   as the cache.
5. **Backups leave the machine.** Today's backup sets upload to the backup
   bucket. The Hugging Face download cache gets a size limit.
6. **Delete local copies** only after gate 2, with an independent check.

The Mac must stay awake through the copy (step 2.4). Use `caffeinate`, or
check that the Mac is on mains power and its lid is open.

**Gate 2:**

- production reads and writes files in the bucket;
- the second copy is complete and checked;
- a restore drill from the buckets passes;
- one week of daily releases runs with no missing-file findings.

**Rollback:** reads fall back to the local files, which stay until gate 2.

## Stage 3: dry run on Dokploy

**Goal:** prove the server setup works, without touching other sites or the
published dataset.

1. **Check the server.** Confirm its CPU, memory and disk today (the specs on
   record are from 2026-09-14), and write the disk budget.
2. **Provision** the Postgres image with WAL archiving, the worker services,
   secrets and alert channels ([cutover](cutover.md), provisioning).
3. **Make fetching and publishing impossible**, not just switched off:
   - the only Hugging Face token writes to a separate rehearsal dataset;
   - the network allows only the buckets, Hugging Face, the alert channel
     and the dead-man's switch, not the sites Swingset collects from;
   - the server gets its own copy of the files (or keys that can only read
     production's bucket), so it cannot change production's files;
   - the out-of-band hold is set during the import.
4. **Import** a fresh production checkpoint with WAL archiving off. Run a
   whole-graph differential proof in the production image, on the server's
   8 cores, against the imported labels: it is a different CPU type, and the
   proof is the test of that. A transform whose output differs on AMD64 is
   bumped and re-runs alone; the rest re-derives nothing. Then take a fresh
   base backup and turn archiving on. Record how long the import, the audit
   and the proof take: stage 4 repeats the first two, and gate 3's proof is
   what lets stage 4 skip the third.
5. **Run for 7 days** with publishing off. Halfway through, import a newer
   checkpoint, so derive and release process a few days of real new pages.
   Also replay recorded pages offline through parse, project and link, as the
   [cutover page](cutover.md) describes. This does not test two loops writing
   at the same moment under real fetching; the single-writer locks (design
   rule 3) are what protect that, and stage 4 watches it closely.
6. **Test the hard cases:**
   - a hold and resume, using both the normal hold and the out-of-band flag;
   - a deploy under hold, and a rollback to the previous image;
   - killing a service mid-tick, and dropping its database connection;
   - a Postgres restart;
   - a point-in-time restore into a scratch database, timed against the
     4-hour target;
   - an alert reaching the owner, and the dead-man's switch firing when pings
     stop;
   - a full release build within its memory limit;
   - a Postgres minor upgrade, with the major-upgrade path written down.
7. **Egress check** (decision record first). For this test only, the network
   rule from step 3 lets the server reach one site at a time. Production
   pauses that same site. The server sends one conditional request for a page production
   already stored, with the same User-Agent. This shows whether the server's
   address draws blocks or challenges that the Mac's does not, without
   raising the total request rate.

**Gate 3:**

- 7 days with no failed tick;
- the proof on the server is clean, or every differing transform is bumped
  and its re-run has drained, with the receipt naming the image digest;
- every hard-case test passes;
- the restore meets the targets;
- a release built on the server matches one built on SQLite from the same
  checkpoint (the stage 1 comparison rules);
- the egress check is clean, or a fix is agreed;
- an independent check signs off.

**Rollback:** delete the Dokploy services. Production never changed.

## Stage 4: switch to Dokploy

**Goal:** the server fetches and publishes; the Mac stops.

**Planned gap:** no release while the import and audit run, expected in
hours rather than days (a guess until gate 3 measures the import). The
re-derivation that made the gap one to three days is gone: the image that
runs stage 4 is the one gate 3 proved, so only units derived since that
proof's checkpoint need an incremental proof, and it runs while fetching
resumes. If the image changed after gate 3, prove it again on a fresh
checkpoint before the freeze, with production still running on the Mac.

1. **Freeze override edits,** hold production, stop its loops, take a final
   checkpoint, and pull it from the server (so the Mac's sleep cannot
   interrupt the transfer).
2. **Import** with the out-of-band hold set and run the audit. Start the
   incremental proof over the units newer than gate 3's checkpoint.
   Overrides are still files at this point; deploy them with the server's
   code from the final tree.
3. **Start fetching.** Lift the hold with publishing off. Confirm requests
   and new pages within an hour.
4. **First release:** build by hand, independent check, publish. Then turn
   automatic publishing on with the service setting that replaces today's
   `dryRun` (it moves into `gov` after the switch).
5. **Retire the Mac worker.** Turn the service off in its Nix settings
   (`services.swingset.enable = false`), rebuild, and remove its token, so it
   stays off even after a reboot. It is retired, not held.
6. **After two weeks,** delete the Mac copies, the local checkpoints and the
   VM, with an independent check.

**Gate 4:**

- two weeks of daily releases from the server with no failed tick;
- a passing restore drill;
- backups in both providers.

**Rollback:** until the server's first fetch, restart the Mac (nothing has
changed there). After that, rollback is forward only: restore the server's
Postgres to an earlier point in time. No tool copies Postgres data back into
SQLite, and none should be improvised.

## After the switch

1. **Overrides move into `gov` together.** All eight override tables switch
   in one deploy. The one-time old-to-new hash map is a re-key (stage 0,
   step 3): the bytes are the same, so their consumers get equivalence rows
   and no re-run.
2. **Holds, settings and exceptions move next,** with their own gate.
   "Exceptions" means the decisions that let something through or keep it
   out: finding dismissals, admission and contract reviews, retention holds,
   approved release decreases and requeues.
3. **The approval tables move last:** contract policies, year acceptance and
   pauses. Their stored hashes are checked again on every run, so each gets a
   planned hash migration under a hold, with receipts; where the hashed
   bytes are unchanged, the migration is a re-key.
4. **The catalog fills** from published releases, starting with the newest.
5. **An API** is separate work, decided by the owner when the catalog is
   ready. On the day of the switch there is no public API; the owner can
   query every table with SQL through the SSH tunnel.
6. **Docs and tooling:** update the operation, deployment, backup and testing
   guides and AGENTS.md's watching rules; retire `nix/`; set log retention.

## How long it may take

These are guesses from the pace of recent work, not measurements.

| Stage | Main work                                                          | Guess                                 |
| ----- | ------------------------------------------------------------------ | ------------------------------------- |
| 0     | the proof runner, declared versions, the re-key, one deploy        | 2 to 3 weeks                          |
| 1     | the port, golden hashes, the proof on Postgres, the batched deploy | 3 to 5 weeks                          |
| 2     | pricing, storage interface, copy                                   | about 1 week                          |
| 3     | provisioning, 7-day run, hard-case tests                           | about 2 weeks                         |
| 4     | final import, first release                                        | about 1 day, then 2 weeks of watching |
| after | `gov`, catalog                                                     | 2 to 3 weeks                          |
