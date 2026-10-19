# Stage 0: prove a change instead of re-deriving under it

Today a code or environment change makes every derived result look out of
date, and the worker re-runs tens of thousands of units to find out that
almost all of them produce the same bytes. Stage 0 changes the rule: a
result is out of date only when the **transform** that made it carries a
new **declared version**, or when one of its inputs changed. Whether a code
change needs a new version is settled before the deploy, by running the new
code over the stored inputs on a copy and comparing the output with what is
stored. That run is a **differential proof**. A deploy that changes a
transform's output without a version bump is refused, so a forgotten bump
cannot reach production.

Status: design, written 2026-10-19 at the owner's request ("greatly reduce
re-derivation, which is very costly"). Not started. Decision:
[D-0264](../../../journal/decisions/0264-declared-transform-versions-and-differential-proofs-decide-re-derivation.md).
Words in **bold** are in the [plan's glossary](README.md#words-used-in-this-plan).

## Why this comes first

- **The migration pays for three full re-derivations** in the plan as it
  stands: the batched deploy on production (step 1.8), the server's first
  import (step 3.4, because ARM64 to AMD64 changes the environment
  fingerprint), and the switch (stage 4, which pauses releases for one to
  three days). Each is 10 to 14 hours of work on today's VM
  ([survey](../../../journal/investigations/2026/platform-survey-2026-10-19.md)).
- **Almost all of that work changes nothing.** 99.98% of replaced
  derivation generations and 95.65% of replaced parse generations were
  byte-identical to what they replaced
  ([investigation](../../../journal/investigations/2026/derivation-invalidation-2026-09-23.md)).
  Output cutoff (D-0205) now keeps the generation in that case, so the cost
  is CPU, held fetching and database churn, not new rows.
- **It happens often.** Of the 28 scoped input bundles production accepted
  between 2026-09-29 and 2026-10-19, the project and link code scopes changed
  in 9 of 27 transitions and the build scope in 12; the parse scopes changed
  2 to 3 times, each for a real parser or contract change. In the same
  period the hand-written version labels changed for three new source kinds
  and one parser bump. So nine times in three weeks every project and link
  unit (about 36,500) re-ran for a change that touched no projector
  (measured read-only on the worker, 2026-10-19).
- **The scopes cannot get much narrower.** Every project and link scope
  holds 178 files, because the storage, projection and link modules import
  each other (D-0207, D-0261). Splitting them helps at the margin; the
  rule itself is the problem.

With stage 0 in place, the three migration re-derivations become proofs:
runs on a copy, in parallel, that write nothing to production and hold no
release. Where a proof finds a real difference, only that transform re-runs.

## The idea in one paragraph

Each transform already has a hand-written version: `EXTRACT_VERSION` and
`PARSER_VERSION` per page kind, `VERSIONS[kind]` per admission contract,
`PROJECTOR_VERSION`, `LINKER_VERSION`. Today those labels queue work but the
code digest decides identity, so the labels are decorative. Stage 0 makes
the labels decide, keeps the code and environment digests as an audit trail,
and adds the one thing that makes labels safe: a tool that proves, before a
deploy, that the new code gives the stored output for every stored input of
every transform whose code changed. If it does, the version stays and
nothing re-runs. If it does not, the author bumps that transform's version,
and the deploy re-runs only that transform's units, with cutoff keeping
each identical result. The deploy is refused if the proof is missing or
shows a difference the version does not acknowledge.

## Design

### What a unit's recipe key holds

A unit's identity (`desired()` in `state/derivations.py`, `stage()` in
`admission/generations.py`) is a hash over its scope, its **recipe** and its
dependencies. The recipe is a map from accepted input names to digests
(`state/recipes.py`). Stage 0 changes the value of two kinds of recipe
input and nothing else:

| Recipe input                                              | Today                                                             | After stage 0                                                        |
| --------------------------------------------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------- |
| `recipe/code/<stage>/<kind>`                              | digest of every file in the scope's import closure                | digest of the scope's declared version (below)                       |
| `recipe/env`                                              | digest of the interpreter, its ABI, every wheel's RECORD, project | leaves the recipe; recorded as an audit input instead                |
| `policy/*`, `overrides/*.csv`, `link/weights.toml`        | content digests                                                   | unchanged: a policy or override edit still invalidates its consumers |
| dependencies (snapshots, selected generations, read sets) | as today                                                          | unchanged                                                            |

The input names do not change, so `recipe_names()`, `affected_work()`, the
certificates and the stored recipes keep their shape. Only the accepted
digest for each `recipe/code/*` name changes, once, at the stage 0 deploy.

**A scope's declared version** is the sorted map of `module: VERSION` over
the scope's deep entry modules in `recipe_scope.entries()`, read from the
captured bytes by AST, the way `admission/kinds.py` tables are read today:

| Scope                 | Entry modules and their version constants                                                                                                                                       |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extract/<kind>`      | the adapter's `EXTRACT_VERSION` for that page class                                                                                                                             |
| `parse/<kind>`        | the adapter's `PARSER_VERSION`; the contract module's `VERSIONS[kind]`; a new `PARSE_CORE_VERSION` in `admission/contracts.py` for the shared core, report, staging and writers |
| `project/<unit_kind>` | `PROJECTOR_VERSION` in `project/process.py`; a new `VERSION` in `project/materialization.py`; a new `VERSION` in the kind's projector module                                    |
| `link`                | `LINKER_VERSION` in `link/service.py`; a new `VERSION` in `link/persistence.py`                                                                                                 |
| `build`               | stays bytes-based (below)                                                                                                                                                       |

The version is declared on the transform, not on every file it imports. A
change to a shared module (`state/`, `model/`, `config.py`,
`identity_policy.py`, a migration) changes the audit digest of every scope
that reaches it, which demands a proof for each, and the proof says which
transforms, if any, need a bump.

**The audit digests** are the digests the recipe holds today: each scope's
closure digest and the environment identity. They move to a second accepted
consumer (`audit`, beside `pipeline`) so they are stored, compared and
reported at every acceptance, but enter no recipe. A change in an audit
digest is what triggers the proof requirement.

### What a proof compares

A proof runs the new code and environment over a **clone** of a recent
production checkpoint and compares, unit by unit, with what the clone
already stores. It never runs old code first: production's stored output is
the old code's output. Only **current** units are compared, meaning units
whose pointer is up to date with their desired fingerprint; a stale unit
re-runs anyway. On 2026-10-19 every unit was current: 36,544 derivation
units and 35,647 accepted parse units.

| Transform     | Runs                                                           | Compares with the stored                                                                         | Mechanism that exists today                               |
| ------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| extract       | `page.extract(body)` over every retained snapshot of the kind  | `snapshots.extract_sha256`                                                                       | `admission/corpus.py` re-extracts from read-only archives |
| parse         | `page.parse`, `declare`, `inspect` over the stored extract     | the accepted generation's `result_json`, `report_json`, `contract_version`                       | parse cutoff's equality test (`admission/generations.py`) |
| project, link | the unit's real write inside a transaction that is rolled back | the pointer generation's `(output_digest, row_count)`, with moved columns left out (D-0212)      | `state/verify.py` `verify_unit`, today a weekly 1% sample |
| build         | one build on the clone                                         | the published baseline's partition artifacts, except named volatile columns (the stage 1.9 rule) | the publication comparison                                |

"Identical" means the same bytes under the comparison the cutoff already
uses for that transform, so a proof passes exactly where a production re-run
would have cut off. Findings, admission side effects and watch clocks are
not compared, as cutoff does not compare them (D-0205).

A unit whose new output differs is run once more with the **old code**: the
accepted bundle keeps every package file under `inputs/<digest>/runtime/`,
so the old tree needs no git access on the worker. If the old code also
differs from the stored output, the unit is not evidence about the change.
It is a read-set miss or nondeterminism, today's `[read-set-miss]` alarm.
The proof reports it, queues the unit for a real re-run, and leaves it out
of the verdict. If only the new code differs, the change changed that
transform's output.

### Where and how the proof runs

- **Target environment.** A proof is valid only for the environment it ran
  in: the interpreter, the wheels and the CPU type the deploy will use. The
  Mac's own Python is not that environment, so a proof for today's
  production runs on the worker VM (an ARM64 Linux container on the Mac
  gave the same outputs, measurement 5, and may serve once the simplified
  identity matches), in the new tree's venv (`uv sync
--frozen` from the new tree, as the development guide does for Linux). In
  stages 3 and 4 it runs on the server, inside the same image the services
  will run.
- **A clone, not production.** The runner takes a reflink copy of the newest
  checkpoint (`cp --reflink=always`; the worker's `/var/lib/swingset` is
  btrfs, 147 GB free on 2026-10-19). A copy costs nothing until written,
  and rolled-back transactions write only to the copy's log. On Postgres
  the clone is a restored scratch database in its own cluster, the one the
  weekly drill already makes, with log archiving and full-page writes off:
  a rolled-back proof still writes 2 to 3 GB of log (measurement 6).
- **In parallel.** Parse and extract are pure functions of stored bytes and
  shard across processes with read-only connections. Project and link units
  need a write transaction, so each process gets its own reflink clone and
  its own writer; on Postgres each process gets its own connection and
  transaction. The worker has 4 cores and the server 8.
- **Only what changed.** A proof covers the transforms whose audit digest
  differs from the accepted one. A parser edit proves one page kind (37
  snapshots for `wdr.rounds`, 31,652 for the registry). An edit inside the
  import cycle proves every project and link unit. An environment change
  proves everything.
- **Fixed clock.** The runner pins the clock to the checkpoint's time, so
  `policy/inventory_year` and any date arithmetic match what production saw.
- **Output.** One JSON receipt: the checkpoint, the environment's audit
  digest, and per transform the old and new audit digests, the declared
  version before and after, units compared, units identical, units
  differing (listed, capped), units excluded as pre-existing mismatches,
  wall time and peak memory. The receipt is committed with the change under
  `journal/evidence/`, so it is also the deploy's evidence.

### The deploy rule

`state/inputs.py` `accept()` already runs on every tick and refuses nothing
today. After stage 0 it refuses a bundle, with an alert and no work queued,
unless for every scope whose audit digest changed one of these holds:

1. the scope's declared version changed too (a bump), or
2. a proof receipt in the bundle names the old and new audit digests of that
   scope, with zero differing units, a coverage share at or above the
   configured floor (proposed: every current unit), and an environment
   digest equal to the one the worker is running.

A bump with a clean proof is allowed and reported as a wasted re-run. A
changed environment digest with no receipt covering every transform is
refused. A refused bundle leaves the previous accepted inputs in place, so
the loops keep running the old recipe on the new code, as a deploy under
hold does today; the fix is to roll the tree back or to add the receipt.

CI cannot run production data. It runs the same comparison over the
fixtures and the 33-page cohort as a test, so an unbumped change that alters
fixture output fails before review. The worker's refusal is the enforcement;
the test is the early warning.

### Re-keying what is already derived

Changing the accepted digest of `recipe/code/*` changes every unit's
fingerprint once. Without help that would re-run the whole graph one last
time. Instead, the stage 0 deploy **re-keys**: for every current unit it
computes the fingerprint under the new recipe values, which needs the
dependency walk but no transform, and records an equivalence from the new
fingerprint to the unit's pointer (`derivation_equivalences`,
`source_equivalences`, both from migration 33). About 36,500 derivation rows
and 35,600 source rows, roughly 70 MB by the invalidation plan's figures.
Nothing re-runs.

A re-key is honest only if the deployed code gives the stored output. So the
stage 0 deploy is the first deploy the proof runner proves: old code is
production's accepted bundle, new code is the stage 0 tree. The runner is
written first and proves its own deploy.

The same tool serves any later "same bytes, new digest" switch: the one-time
hash map when overrides move into `gov` (after the switch, step 1) and the
approval tables' hash migrations (step 3) can re-key instead of re-running.

### Environment changes

`recipe/env` leaves the recipe. The environment identity stays an audit
input, simplified as D-0263 decided: the lockfile, the Python version and
the CPU type. The interpreter's own hash and the wheels' script lines are
left out, because they differ between builds of the same Python without
changing any output (measurement 5). A new lockfile, Python version or CPU
type then demands a proof over every transform, run in that environment,
and nothing more. A
transform whose output changes under the new environment (a Unicode table
in a new Python, a library that rounds differently on AMD64) gets a bump,
and only its units re-run. That is the finding worth a record; the proof is
how it is found, instead of being hidden inside a full re-derivation.

### What stays bytes-based, and what still re-runs

- **Build** keeps its closure digest in the recipe. A build runs daily
  anyway, takes about an hour, and its output is whole-dataset files that
  the release gate already compares with the baseline. A declared version
  would buy little and cost a second comparison path. Revisit if builds get
  expensive.
- **A real bump** re-runs that transform's units on production, with
  cutoff keeping each identical result. A parser bump for the registry is
  still about 31,600 parses and 29,200 dancer projections.
- **Policy and override edits** re-run their consumers as today:
  `policy/inventory_year` every January (all project units), link weights
  (all links), an alias file (its mappings).
- **Pre-existing mismatches** the proof finds are queued for a real re-run.

## Rules

1. A derived result is out of date when a declared version it was made under
   changed, or an input it reads changed. Never because code bytes changed.
2. Code and environment digests are recorded at every acceptance and never
   enter a recipe.
3. A bundle whose audit digest changed for a scope is accepted only with a
   bump of that scope's version or a proof receipt for that scope. Missing
   both, the worker refuses it and alerts.
4. A proof runs in the environment the deploy will run in, on a clone, never
   on production, and compares with stored output under the cutoff's own
   equality for that transform.
5. A unit that differs under both old and new code is excluded from the
   verdict, reported, and queued for a real re-run.
6. A re-key is allowed only after a clean proof of the deployed code, and
   only adds equivalence rows.
7. Every proof and every re-key leaves one receipt under `journal/evidence/`.
8. Build keeps its bytes-based recipe until a measurement says otherwise.

## Measurements, taken 2026-10-19

All six ran on 2026-10-19, on a clone or read-only; details in
[the measurements](../../../journal/investigations/2026/stage-0-measurements-2026-10-19.md). Production paused for 4 seconds to take the clone.

1. **Where a re-derivation's time goes.** Over 1,282 derive ticks and
   408,084 units, a unit's own work (transform, writes, completion) is 21%
   of tick time; choosing and admitting the next unit and finishing the last
   one take 57%. A live profile puts 53% of derive CPU in dependency
   fingerprints (`desired()`, called about four times per unit). Commits
   and fsync take 1.5%. So batching commits would gain little; the lever is
   calling `desired()` less often or making it cheaper (step 6).
2. **A verify-style run on a clone,** 1,491 units: 1,485 of 1,486 current
   units gave their stored output, two runs agreed unit for unit, and there
   were no failures. The one difference, `project/calendar/wsdc`, is by
   design: the inventory rewrites a calendar's rows later, so the proof must
   leave current calendars out or compare them another way. Pace: 4.6 units
   a second with one process, 7.9 with two; peak memory 828 MB. The clone
   passed `quick_check`. `verify_unit` labels output twice more than a proof
   needs, 47% of a link unit's time. **A whole-graph proof takes about an
   hour at one process and about 35 minutes at two** (estimate; three or
   four processes not tried), against about 7.5 hours of re-derivation for
   the same units.
3. **Extract and parse over every stored input:** all 35,647 accepted parse
   units matched on manifest, result, report and contract version, in 72
   seconds with two processes. Of 40,129 snapshots, 39,982 re-extract to the
   same digest; the 133 that differ were extracted under an older label and
   are not needed, and the 14 without an extract still fail the same way.
4. **Old code side by side:** the bundles of 2026-10-15 and 2026-09-29 ran
   as-is under today's venv (same lockfile). The 10-15 code gave stored
   output for all 207 sampled units, although all 40 of its parse scope
   digests had changed: the waste stage 0 removes. The 09-29 code differed
   on 64 units, 61 in labels only and 3 in content.
5. **Mac versus worker:** Linux containers on the Mac, ARM64 and emulated
   AMD64, gave byte-identical extract, result and report for 303 sampled
   generations across 14 page kinds, and identical fixture outputs. Declared
   labels and scope digests matched production. The environment identity
   never matched: production's Python is a Nix build, five wheel records
   differ only by a script's first line (`python3` against `python`), and
   on AMD64 seven compiled wheels and the ABI differ too. The emulated
   AMD64 lacks AVX, which numpy and DuckDB choose code paths by, so it is
   not evidence for the server. `math.log` differs in the last bit between
   CPU types for 29 of 301,000 inputs, and the linker feeds `-log(score)`
   to its assignment solver, so a near-tie could flip there.
6. **Postgres log from rolled-back proof writes** (simulated with
   Postgres 18.6 and production's row shapes): rolling back saves nothing.
   A whole-graph proof writes 2.2 to 3.2 GB of log, against about 4.2 GB
   for a committed re-derivation; full-page images are 83 to 90% of it.
   Proofs therefore run on a separate scratch cluster with archiving off
   and full-page writes off, never inside the production cluster.

## Steps and gates

1. **The proof runner** (`swingset prove`), with the fixture-level test. It
   reads `recipe_scope` for what changed, shards units across processes on
   reflink clones, compares as the table above says, runs old code for
   differing units, and writes the receipt. The scratch runners behind
   measurements 2 to 4 are its starting point; it skips current calendar
   units, and labels output once rather than three times. _Gate:_ a whole-graph proof of production's own deployed
   tree against the newest checkpoint reports zero differing units, or every
   differing unit is a pre-existing mismatch; its wall time is recorded.
2. **Declared versions and the audit consumer.** The new `VERSION`
   constants, the AST reader, the `audit` consumer, the acceptance rule, and
   tests: an unbumped output change is refused; a bump is accepted; a clean
   receipt is accepted; a receipt for another environment is refused. The
   `recipe/env` and code scope rows in the state reference's invalidation
   table change accordingly. _Gate:_ core tests and the full suite pass;
   `test_recipe_scoping.py` still proves every package file is in a scope
   or excluded.
3. **The re-key command** (`swingset rekey`), one transaction per batch,
   with a receipt of rows written per kind. _Gate:_ on a clone, every unit is
   current after the re-key under the new recipe values and
   `requirement_report` counts zero unfinished units.
4. **The stage 0 deploy**, under a hold with a named owner and a 24-hour
   review date: prove the stage 0 tree against production's bundle (step 1's
   runner, first real use), deploy, re-key, lift the hold. _Gate:_ the
   post-check receipt shows zero parse, project or link units queued by the
   acceptance and the next release builds from unchanged pointers. This is
   the publish tier: an independent check with a written verdict, a
   preflight receipt and a post-check receipt.
5. **Scope splits, as maintenance.** Give `project.process` a dispatch
   table for projectors (the pattern of `admission/kinds.py`), so a
   projector edit changes one project scope's audit digest and proves one
   kind. Then look at `state/` versus `project/`. Each split is a proof's
   worth of work saved per deploy, not a correctness change. _Gate:_ the
   scoping test shows the intended scope alone changing.
6. **Production throughput**, from measurement 1 and a written trigger,
   per AGENTS.md. The measurement names the lever: compute each unit's
   dependency fingerprints once instead of about four times, and make
   choosing the next unit cheaper. Batched commits would gain at most 1.5%. Project and link
   parallelism waits for Postgres and its own measurement; target design
   rule 3 keeps one writer at a time.

Steps 1 to 4 are stage 0 proper. Steps 5 and 6 can run alongside stage 1.

## Rollback

- Before step 4 nothing on production changes.
- The stage 0 deploy rolls back by switching to the previous system
  generation. The old code accepts its old bundle, whose `recipe/code/*`
  values are the closure digests the stored generations already carry, so
  nothing re-runs. The re-key's equivalence rows are extra rows that name
  real generations; they are harmless and collectable under the invalidation
  plan's step 3.
- The `audit` consumer's rows are new rows in an existing table
  (`accepted_inputs`); the proof receipts table, if one is added, follows the
  migration rule: add before remove.
- A proof that finds a difference nobody expected is a finding, not a
  rollback: the author bumps or fixes, and the deploy waits.

## What stage 0 changes in stages 1 to 4

| Step in the plan                                   | Before stage 0                                                                  | With stage 0                                                                                                                                                                                     |
| -------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1.7 run the pipeline on Postgres locally           | expect one full re-derivation; record its time and memory                       | run a whole-graph proof on the Postgres import against the imported labels; record its time and memory. A re-derivation measurement is no longer needed for stage 4 planning                     |
| 1.8 one batched deploy to production               | one full re-derivation on production                                            | a proof of the batched tree; a re-derivation only for a transform the proof shows changed, which the database port should not. The environment change D-0263 decided becomes audit-only          |
| 1.9 compare SQLite and Postgres                    | re-derive on both, compare every payload hash and every published table         | the proof on Postgres is the per-unit comparison; one build on each side keeps the published-table comparison                                                                                    |
| 3.4 import on the server                           | the server re-derives because the CPU type changed; `VACUUM`; fresh base backup | a whole-graph proof on the server, in the production image, with the checkpoint as the clone; no `VACUUM` for re-derivation bloat. A transform that differs on AMD64 is bumped and re-runs alone |
| 3.5 seven-day run                                  | derive processes a few days of real pages                                       | unchanged, plus the proof receipt is part of gate 3                                                                                                                                              |
| 4 switch                                           | no release for one to three days: import, audit, re-derivation                  | import and audit only, with an incremental proof over units newer than the stage 3 proof's checkpoint while fetching resumes. Gap: hours, not days (a guess until gate 3 measures the import)    |
| after the switch, overrides into `gov`             | a recorded old-to-new hash map makes one re-run of their consumers              | the map is a re-key; no re-run                                                                                                                                                                   |
| after the switch, approval tables' hash migrations | planned hash migrations under a hold                                            | a re-key each, under the same holds and receipts                                                                                                                                                 |

What still re-derives, honestly: any transform the proof shows changed on
the server's CPU type; every legitimate bump during the migration; the
daily build; and `policy/inventory_year` on 2027-01-01 wherever production
is by then. The import and its audit at stages 3 and 4 remain, and gate 3
measures them.

The rule "count every re-derivation" in [the stages](stages.md) stays, and
each gate's report adds the proofs it ran and what they found.

## Risks and open questions

- **A proof covers stored inputs only.** A change that alters output for a
  page shape no stored snapshot has passes the proof, stays unbumped, and
  parses new snapshots with the new code. That is correct: the stored
  outputs are exactly what the new code gives for the stored inputs. The
  loss is narrative, not correctness: the version no longer says "the parser
  changed".
- **Transforms that read beyond their declared inputs.** The proof runs the
  real unit on a full clone, so it sees whatever the unit reads. Invalidation
  is unchanged, so a read-set miss stays a read-set miss; the proof now
  finds them in bulk instead of 1% a week.
- **Environment-dependent output.** Float repr, sort order and JSON
  canonicalization are the same on ARM64 and AMD64 CPython, but Unicode
  tables follow the Python version. On 303 sampled parses and the fixture
  suite, ARM64 and emulated AMD64 gave identical output, but emulation hid
  AVX, and `math.log` differs in the last bit for a few inputs, which can
  flip a near-tie in the linker's assignment. The proof on the server is
  the test; rule 4 forbids proving on one machine for another.
- **Time.** The runner pins the clock. A transform that reads the wall clock
  into its output would already fail the cutoff today.
- **Whole-dataset transforms.** Build is left bytes-based. Inventory, map
  and history are single units whose proof is one run each (the history
  run is long and memory-heavy; it runs alone).
- **Link's global state.** A link unit reads the registry through read keys
  (F20) and its pool version. On a clone those are frozen with the
  checkpoint, so the proof is exact for that checkpoint.
- **Stored approvals keyed by digests** (`admission_reviews.report_digest`,
  `history_acceptance.inventory_digest`, the identity journal digest) hash
  stored generations and their contents, which the re-key does not touch.
  New parses keep their accepted generation through cutoff. No approval
  needs re-doing. To be confirmed by step 3's gate.
- **Proof wall time.** Measured: about an hour at one process and about
  35 minutes at two on the worker (measurement 2), against 10 to 14 hours of
  re-derivation. Reads and the dependency walk are 61% of it, Python 36%,
  writes 3%. The server's time is not measured. An edit inside the import cycle still costs a
  whole-graph proof until step 5 splits the cycle.
- **The Mac sleeps.** A proof on the worker VM stops with it. Run proofs
  with the Mac awake (`caffeinate`), or wait for the server.
- **Open: coverage floor.** Should a proof pass with a few stale units
  uncovered, or must every unit be current first? Proposed: every current
  unit must compare and the stale ones are listed; the operator decides with
  the list in hand.
- **Open: the receipt's home.** In the tree under `journal/evidence/` so it
  deploys with the code, or in a state table written by `swingset prove`?
  The tree is proposed: it is the evidence either way, and the bundle
  captures it.
- **Open: who bumps.** The author of the change, in the same commit, after
  reading the proof's differing list. The proof is also a preview: "this
  bump changes 1,234 of 29,216 dancers".

## How long it may take

Guesses from the pace of recent work, not measurements.

| Step                                      | Guess                  |
| ----------------------------------------- | ---------------------- |
| 1. the proof runner and its measurements  | 3 to 5 days            |
| 2. declared versions and the deploy rule  | 3 to 4 days            |
| 3. the re-key command                     | 1 to 2 days            |
| 4. the stage 0 deploy and its checks      | 1 day plus the review  |
| 5. scope splits (alongside stage 1)       | 1 to 2 days each       |
| 6. production throughput (from a receipt) | 2 to 4 days (estimate) |

About two to three weeks of calendar time for steps 1 to 4, which stage 1
then gets back in avoided re-derivations and release gaps.

## Where this differs from the idea as approved

The owner's idea had four parts. This design keeps the first and changes
the other three.

1. **Declared versions with a differential check: kept**, with two
   refinements. The proof compares new code with _stored_ output, not with
   a second run of old code; the old code runs only for differing units.
   And the proof is enforced at acceptance on the worker, where a refusal
   is a real refusal, with CI as an early warning only.
2. **Splitting the scopes: demoted to maintenance.** Under the new rule a
   broad scope costs a longer proof, not a re-derivation. The splits are
   worth doing (nine whole-graph proofs in three weeks would have been nine
   one-kind proofs), but they are not needed before stage 1.
3. **A parallel executor for real re-runs: measure first.** The proof runner
   is parallel from the start, because it runs on clones. For production,
   measurement 1 found the time goes to dependency fingerprints and picking
   the next unit, not to compute or commits, so step 6 targets those. On Postgres
   the target design keeps one writer at a time (rule 3), so parallel
   writes are not available there either without a later decision.
4. **A "verified under version X" row instead of a new generation: already
   there.** Output cutoff records an equivalence row for an identical
   re-run and no generation (D-0205; 379,000 such rows today). Under the
   new rule an unbumped change writes nothing at all, not even that row. The
   open piece is collecting dead equivalences, the invalidation plan's step
   3, which is unchanged.
