# Stop re-deriving what has not changed

This plan fixes one defect with three faces: a code edit anywhere invalidates
every unit in every stage, the re-derivation replaces generations whose output
is identical, and nothing collects what it leaves behind. The
[2026-09-23 investigation](../../journal/investigations/2026/derivation-invalidation-2026-09-23.md)
measured it on the live worker.

The headline numbers it has to move:

- 328,169 of 328,233 replacing derivation generations (99.98%) have the same
  output digest as the generation they replaced; 174,866 of 182,819 replacing
  parse generations (95.65%) have a byte-identical result.
- 364,132 generations have 364,132 distinct input fingerprints, so the reuse
  check has never once succeeded.
- 80% of the 40.6 GB database is redundant: 20.7 GB of release-closure
  manifests stored twice and never collected, 7.8 GB of superseded row
  references, 4.1 GB of superseded parse evidence.
- Nothing in the tree can delete any of it. `gc --apply` may remove only
  `derivation_payloads` rows, and only once a generation is archived, which
  needs a table that does not exist.

This replaces maintenance queue items 4 and 8.

Status: steps 1, 2 and 5 shipped as phase 1 of the
[target architecture](architecture.md), deployed 2026-09-29
([receipt](../../journal/evidence/runtime/architecture-phase1-2026-09-29/receipt.json));
that phase lists what changed from the text below (F1 to F4), the deploy
steps and the rehearsal.
Step 3's collection of unnamed closure proofs shipped in candidate 018
(D-0200); phase 4 of the architecture plan collects every unnamed dependency
set (D-0231, offline, not deployed); equivalence collection is still open.
Step 4's cache per dependency set came with step 1.

**Next step (2026-10-19):** [stage 0 of the Postgres migration](postgres-migration/5-stage-0.md)
([D-0264](../../journal/decisions/0264-declared-transform-versions-and-differential-proofs-decide-re-derivation.md)).
Steps 1, 2 and 5 made a code change cost CPU instead of generations, but a
change inside the import cycle still re-runs every project and link unit:
nine times in the three weeks to 2026-10-19. Stage 0 replaces the code
digest in a unit's recipe with the transform's declared version, keeps the
digest as an audit trail, and proves each change on a clone before it
deploys. The open items here (equivalence collection in step 3, the
streaming planner in step 4) are unchanged by it.

## When this may start

Not before **the worker has run phase two cleanly for a full day**: 24 hours of
cycles with `failed=false`, no pause, no hold and no line in `ALERTS.log`.
Every deploy this week cost five hours of re-derivation, and the parse backlog
is still draining. Publication is off under
[D-0189](../../journal/decisions/0189-stop-publishing-until-identity-output-is-recovered.md)
and stays off until identity output is recovered; this plan does not turn it
back on.

Steps 1 and 2 may be written and tested offline before that, since neither
touches the worker until its own deploy step. Steps 1, 2 and 5 were written
that way; the gate, or an explicit waiver, still applies to their switch.

## How much checking

Every step here touches publication or restore, so each takes the full suite,
`nix develop -c uv run pytest -q --full-suite`, before it lands, and the
publish tier's independent check: one second agent with a written verdict, plus
a preflight receipt and a post-check receipt
([AGENTS.md](../../AGENTS.md)). On the worker, run the full suite with
`--basetemp` under `/var/tmp`; `/tmp` is a small tmpfs there.

A rehearsal on a scratch copy of the live database proves each change to the
agent running it; the production receipt is the evidence.

## Steps

### 1. Early cutoff at the derivation stages

When a unit re-runs and its output rows fingerprint to the same digest and row
count as the generation its scope already points at, keep that generation,
record that the new input fingerprint also produces it, and leave the pointer
alone. Otherwise write a new generation exactly as today.

The decision can only be taken in `state/derivations.py` `complete()`, because
`desired()` mints the id before any row exists, so `Selection.generation_id`
becomes provisional. `project/materialization.output_rows` is a generator over
SQL against already-written tables, so it can be replayed: fingerprint the rows
in one pass without storing, decide, then store only if they differ.

Migration 33 adds the mapping table. The generation row itself cannot be
amended: migration 14 installs `derivation_generation_no_update`. Call the new
records _equivalences_, not aliases — "alias" already means event and series
aliases here.

An equivalence row carries the scope, the new input fingerprint, the generation
it maps to, its own recipe, its own dependency set id and its own `run_id`.

Changes this forces, from the investigation's sweep:

- `desired()` reuse test and `_current()` slow path must consult the
  equivalence table, or a kept generation reads as pending forever.
- `complete()`'s existing-generation branch must still insert the re-run's
  dependency sets, and `retention.reachable()` must follow the equivalence
  row's dependency set. Without this the generations the re-run actually
  consumed become unreachable and therefore archivable. This is the one
  correctness hazard in the step.
- The cutoff must be forbidden when a `derivations.group()` contains inline
  children, because they capture the parent's reserved id before it commits.
  `project/materialization.py` enters a group for _every_ unit, even a lone
  one. Track whether the group nested or selected another unit, and allow the
  cutoff for a lone completed unit; forbidding every group would disable it.
- It must be ordered strictly after both existing input fences in `complete()`,
  so a source change during row iteration cannot be certified by a matching
  digest.
- The continuity path for `inventory`, `map` and `history` must not end up with
  a generation that is its own `previous_generation_id`.
- `project/process.py`'s calendar replay guard compares a provisional id to the
  pointer and needs a different test.
- `migrations/0029`'s history dispatch fence needs an equivalent trigger on the
  new table.
- `state/requirement_report.py` and the doctor and replay docs count unfinished
  work by raw fingerprint comparison and would over-report forever.

_Implemented offline_ ([D-0205](../../journal/decisions/0205-cut-off-identical-derivation-output.md),
[D-0206](../../journal/decisions/0206-add-migration-33-after-interning.md)).
Migration 33 adds `derivation_equivalences`, `derivation_recipes` (a
kind's recipes are shared, so a deploy writes about 10 MB, not 50) and
`source_equivalences`. `complete()` reads the rows once without writing,
checks both fences, then decides. It cuts off only a `project` or `link`
unit whose pointer is the one its selection saw, and only when all three
hold:

- **Closure-stable.** Its direct `derivation` entries and its `selected_*`,
  `source_observations` and `calendar_observations` groups equal the kept
  generation's. These are all that release closure and retention follow, so
  a kept id never names a stale dancer, and the retention hazard above cannot
  arise. A real upstream change still mints downstream. _Dropped in phase 3
  ([D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md)):
  releases take current pointers, so output alone decides._
- **Replay witnessed.** If the replay moved one of six live revisions, the
  kind's `replayed` epoch moves, and dependents, whose fingerprints and
  certificates include it, re-run.
- **Not pinned.** This replaces the lone-unit rule: only a group member whose
  provisional id a sibling captured is refused. A `source_event` nesting its
  event's reuse still cuts off.

A re-run whose output differs stores its rows from a second read, which must
give the same label. History owns `events.coverage_tier`, so map and calendar
output leave it out. Their generations from before phase 1 recorded it, so the
comparison takes that one column out of the kept rows first; without that the
first switch minted about 6,300 generations and withheld every link until the
links re-ran
([D-0212](../../journal/decisions/0212-cut-off-across-the-moved-column-and-held-parses.md)).

Excluded: `stage == "build"`. Build output is an artifact row naming a
filesystem path, so two builds never produce identical bytes, and reuse there
is already gated on `_artifact_valid`.

Also record, because they change and are correct: `build/reuse.trigger` stops
forcing a rebuild after a code change that alters no output, and
`ReleaseClosure.semantic_fingerprint` treats two releases with different
upstream recipes but identical output as the same. And the one real loss: the
published `first_seen_at`, `last_seen_at`, `asserted_at` and `run_id` come from
the selected generation's row, so they become "when these bytes were first
derived" rather than "when they were last confirmed".

**Decision records** (written: D-0205, D-0206, and
[D-0208](../../journal/decisions/0208-keep-the-64-gb-cap-until-closure-collection.md)
for D-0196). One record for the rule, carving it out of
[D-0022](../../journal/decisions/0022-preserve-supported-event-bases.md), which
says "Do not transplant or relabel old generations to avoid that replay": the
rule performs the replay in full and only declines to mint a new label
afterwards. One for migration 33, against
[D-0167](../../journal/decisions/0167-intern-derivation-payloads-in-the-last-migration.md),
which declared 32 the last migration. One amending
[D-0196](../../journal/decisions/0196-raise-the-database-size-cap-to-64-gb.md),
whose stated exit condition was the retention planner.

**Tests.** Invert the case in `tests/test_h15_acceptance.py` that changes one
runtime file, keeps the output identical and asserts a new generation id and
two generations; it is exactly this scenario with the old behaviour frozen in.
Fix `tests/build/test_closure_validation_scope.py`, which asserts
unconditionally that the provisional id was written, and the
`tests/test_retention_plan.py` fixtures that hard-code one fingerprint per
generation. Add, in `tests/test_h15_acceptance.py`: a re-run with identical
output writes no generation and moves no pointer; the scope reads as current
afterwards; a re-run with different output still writes a new generation; a
release closure built across such a re-run keeps its links instead of raising
`mixed_dependency_generations`; and the retention walk still reaches everything
the re-run consumed. Full suite.

**Receipts.** A rehearsal on a scratch copy that replays the 2026-09-21
regression and shows the links surviving. Then a preflight receipt naming the
migration and the generation count, and a post-check receipt after the first
cycle, with the generations written by a deploy that changes no output, which
should be zero.

### 2. Early cutoff at parse

The same rule for `source_generations`. Before `admission/generations.py`
`stage_generation` inserts, compare the candidate's `result_json`,
`report_json` and the manifest's body and extract digests against the unit's
accepted generation. If they match, record an equivalence for the new input
fingerprint and return the existing id; `admit_generation` then sees the unit's
own accepted generation and returns `accepted` without touching anything
downstream.

This matters beyond storage. A project unit's dependencies include a
`source_selection` entry naming the accepted source generation id, so keeping
that id means project units see no changed dependency at all, whatever their
own recipe says.

`admission/select.py` compares `source_units.desired_fingerprint` with the
generation's `input_fingerprint` to decide whether an attempt is current, so it
needs the same lookup as `_current()` in step 1.

_Implemented offline_ (D-0205). Cutoff needs the exact manifest (labels,
snapshot ids and times included), the result, the report and the contract
version to equal the kept generation's, current inputs, and no revoked
generation with the new fingerprint. So a label bump or a new snapshot with
the same body still writes a generation. The kept generation is the accepted
one, or, for a unit with no pointer, the newest undecided one its desired
fingerprint names. For an enforced kind it may also be the unit's newest
generation held in `needs_review` or `waiting_for_inputs`, when admission
would hold the repeat the same way (the contract unreviewed, or reviewed and
the report's failures decide); the parse reports that state with no new
generation or decision (D-0212). Admission side effects are not replayed for
an identical result. Migration 33 also stops a `desired_fingerprint` write from moving
the `watch` token.

Note for the measurement: five page kinds have a null `previous_generation_id`
on every row — `wsdc_calendar.events`, `swingdancecouncil.events`,
`wsdc_newsletter.events`, `wdr.awards`, `wsdc_newsletter.index` — so they are
outside the 95.65% figure and should be checked separately rather than assumed
to behave the same.

**Tests.** In `tests/test_admission.py`: a re-parse with an identical result
under a changed runtime recipe writes no new source generation, the unit stays
accepted, and no project unit is enqueued. A changed result still writes one
and still goes through admission. Revocation is unaffected. A held unit's
identical repeat keeps its generation; one admission would now decide
differently is staged. Full suite.

**Receipts.** Preflight with the `source_generations` count and bytes;
post-check after a cycle following a code-only switch, with the rows written,
which should be zero.

### 3. Store the release closure once, and collect the copies

Status (2026-10-03): set collection is done by phase 4 of the
[architecture plan](architecture.md), implemented offline and not deployed.
Its collector
([D-0231](../../journal/decisions/0231-collect-every-unnamed-dependency-set-and-unrooted-object.md),
[state: collector](../reference/state.md#collector)) removes every
dependency set no generation, equivalence, scope proof, recipe, candidate
or kept set names, and packs build recipes over 1 MB, so the 217 MB recipes
leave the file. It does not free the bulk the investigation counted: on the
2026-09-29 copy those sets are named through other sets (build labels'
`release_closure` members, old inventory labels), so it frees 0.10 GB, and
the rest waits on the architecture plan's open decision 4. Still open here:
collecting `derivation_equivalences`, `source_equivalences` and
`derivation_recipes` (the authority and liveness rules below).

The investigation found 547,121 unreferenced dependency-set manifests holding
18,984,563,859 bytes, 46.7% of the database. Eighty-nine of them are 16 MiB or
larger and hold 18.6 GB between them; none is referenced. They are release
closures: `build/service.py` commits `closure.retain()` before the file phase,
so every build attempt stores its whole private manifest permanently whether or
not it finishes. Separately, the eight `build/release` generations hold
1,740,446,395 bytes of `recipe_json`, 217 MB each, because a pinned build's
context carries the same manifest and `complete()` writes the context into the
recipe.

Steps 1 and 2 stop this growing, because a stable closure means
`INSERT OR IGNORE` deduplicates instead of writing a new manifest each attempt.
This step recovers what is there and stops the double storage.

- Keep the closure in `derivation_dependency_sets`, where `closure.hydrate`
  finds it by the `private_digest` a published manifest carries. Store a
  reference to it in the build generation's context, not the manifest itself.
- Teach the retention plan about dependency-set manifests. Today they are
  neither local, nor archivable, nor unknown, so 17.68 GiB appears in no plan
  total and no doctor report. A manifest is local when a reachable generation
  names it, when a retained candidate's public manifest names it by
  `private_digest`, or when a hold names it. Everything else is archivable.
- Let `gc --apply` remove an archivable manifest. This is the first thing other
  than a payload that an apply may delete, so it takes its own decision record
  and its own immutability carve-out.
- Collect the cutoff's rows too (schema 33, [D-0206](../../journal/decisions/0206-add-migration-33-after-interning.md)).
  `derivation_equivalences` has foreign keys to `derivation_generations` and
  `derivation_dependency_sets`, so a manifest or generation an equivalence
  names is not collectable while the equivalence lives. An equivalence is
  live while its fingerprint is its scope's `desired_fingerprint` or it is the
  newest naming the scope's pointer; a `source_equivalences` row while its
  fingerprint is its unit's `desired_fingerprint`; a `derivation_recipes` row
  while a live equivalence names it. The reachability walk counts live ones
  as roots, and the apply deletes the rest, one row per transaction, with
  the same trigger lift `closure_collection` uses. Migration 33's
  `*_no_delete` triggers refuse every delete today, so this step adds a
  migration that recreates them to allow a delete only while a
  `equivalence_collection` authority row exists (the pattern of
  `derivation_payload_removal_authority`), which the apply writes and removes
  in the same transaction. A code-only deploy adds about 35,800 derivation
  equivalences and about 39,600 dependency sets (about 32 MB, phase 1 R10) and
  no source equivalences; a `recipe/env` or all-parse-scope change adds up to
  33,900 source equivalences (about 35 MB). The plan and the post-check report
  their counts and bytes. When it starts is the owner's third open decision in
  the [architecture plan](architecture.md#open-decisions-for-the-owner): at
  the post-check's measured growth the file reaches the 64 GB cap within
  about two weeks of phase 1's switch. The watchdog's
  `[equivalences]` counts rows with `count(*)`, not the largest rowid, so the
  line clears once this collection deletes them; if that count grows too
  slow for the watchdog's read budget, keep a counter instead.

**Tests.** In `tests/test_retention_plan.py` and `tests/test_retention_apply.py`:
a manifest a retained candidate's `private_digest` names is local and survives
an apply; an unreferenced one is archivable and is removed; a published release
still hydrates its private closure after the apply; a checkpoint and restore
round trip afterwards. Full suite.

**Receipts.** A rehearsal on a scratch copy of the live database: the plan's
new totals, the apply, `gc --reclaim`, then a restore and a full
`PRAGMA foreign_key_check` on the restored copy. Then a production preflight
with the file size and a post-check with the size after reclaim. This step
deletes data, so it needs the independent check before the production apply,
not after.

### 4. A retention planner that fits in memory

`gc --plan` is killed at 8.3 GB resident. Two changes, no redesign:

- `retention.reachable()` caches children per generation id. Cache them per
  dependency-set id instead: set ids are content addresses, so one shared
  dancer cohort is decoded once rather than once for each of 6,024 link
  generations. Pass a shared `visited` set into `leaves()`, as
  `build/closure.py` already does.
- Stream `_generation_rows` and the plan bytes to the plan file instead of
  building the list and serializing it twice. `_payload_bytes` already streams
  and is the model.

Do this with step 3, which needs the planner to name what it collects. On its
own it frees nothing, and it must not be described as a fix for the size cap.

**Tests.** `tests/test_retention_plan.py`: same plan digest before and after the
change on a fixture with shared dependency sets; a resident-memory ceiling on a
generated graph with many generations sharing one large set. Full suite.

**Receipt.** `gc --plan` completing on the live database, with peak resident
memory and elapsed time.

### 5. Scoped recipes from the import graph

Replace the single `recipe/runtime` input with one per stage and page kind:

- `recipe/code/extract/<page_kind>`, `recipe/code/parse/<page_kind>`,
  `recipe/code/project/<unit_kind>`, `recipe/code/link`, `recipe/code/build`:
  the digest of the sorted file-to-hash map over that entry's transitive import
  closure inside the `swingset` package, plus every non-Python file in any
  package directory that closure touches, minus `**/fixtures/**`.
- `recipe/env`: interpreter identity, installed distribution RECORD digests,
  `pyproject.toml` and `uv.lock`. It joins every stage's recipe, so a
  dependency upgrade still invalidates everything.

Compute the closure statically over the AST, counting every `import` and `from`
statement wherever it appears, including inside functions — this codebase uses
function-local imports heavily. Over-approximation is the safe direction. The
package has two dynamic imports: `__import__("datetime")` in
`schedule/cycle.py`, naming a standard-library module, and the owner lookup in
`sources/registry.py`, which the entry table names by hand.

Before selecting per-kind parser closures, split the source registry in
`sources/__init__.py`: its function-local imports name every adapter. A literal
transitive walk from the current registry would put every adapter in every
parser recipe and re-queue all page kinds when one changes. The new lookup
must retain explicit ownership of each page kind and include the code that
dispatches to it in that kind's closure.
Done: `sources/registry.py` holds `ADAPTERS` and `KIND_OWNERS` and imports
only the owner. The registry contract and admission generations no longer
import an adapter or `history/acquisition.py`.

The entry table mapping a stage and kind to its module is written by hand, but
bytes still decide, so a forgotten version bump cannot hide a change. Keep the
whole package in the captured bundle for exact replay; only the selected inputs
narrow.

`state/work.py` already routes `version/extract/<kind>` and
`version/parser/<kind>` correctly: the extract branch nulls the watch's cached
extract version, the parser branch does not. The code digests take over from
the hand-written labels and use those same branches. So an extractor change
re-extracts and re-parses that page kind; a parser change re-parses from the
stored extract. Today neither is true, because `schedule/parse.py` re-extracts
whenever `recipe/runtime` changed.

`state/migrations/*.sql` stays in every stage's closure through `state/db.py`,
so a new migration still invalidates everything. That is deliberate and
tolerable once steps 1 and 2 are in: the cost is CPU, not new rows and not a
withheld release.

Note for scale: 29,059 of the 31,821 retained snapshots are registry dancer
pages, so a change under `sources/wsdc_registry` still costs most of a full
pass. The saving is on the changes that should cost nothing.

_Implemented offline_
([D-0207](../../journal/decisions/0207-scope-code-recipes-by-import-closure.md)).
`state/recipe_scope.py` holds the entry table and `EXCLUDED`. A parse scope
is 39 to 47 files and an extract scope 17 to 24. Project, link and build
scopes were about 214 files each, because the package is one import cycle.
Phase 2 (WP12) took the scheduler, the loops and acquisition out of them: 168
project and link files, 174 build. `schedule/parse.py` and the parse writers
(admission, observations, verification, findings, watch clocks) count by
their own bytes only. `recipe/runtime` stays as a constant, so old bundles reproduce. No
module is parse-only for a single kind, so "editing a parser" below means an
adapter.

**Tests.** A new `tests/test_recipe_scoping.py`: the declared closures union
with an explicitly listed excluded set to exactly the package, so nothing is
unattributed; a function-local import is in the closure; editing a fixture
changes no recipe; editing a parser changes that page kind's parse recipe and
no other; editing an extractor changes the extract recipe and enqueues a
re-extract; editing an unrelated module changes nothing. Extend
`tests/test_extract_recipe.py` and `tests/test_recipes.py`. Full suite.

**Receipts.** Preflight with the recipe inputs before and after. Post-check
after the first deploy that touches no parser: the parse units re-queued,
which should be zero, against the 32,519 of the 2026-09-23 05:45 UTC switch.

## Sequence and estimate

Written 2026-09-23, before any step started. Effort is agent time on this
repo, calibrated on the week's fixes: a single-file production fix took two to
four hours from diagnosis to deployed change, with the five-hour re-derivation
dominating the wall clock. Update this section when a step lands.

| Step                                       | Effort       | Deploy cost                                             | Status (2026-10-03)                                                                                 |
| ------------------------------------------ | ------------ | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 1. Early cutoff for derivations            | 2 to 3 days  | Python change: one full re-derivation, about five hours | Deployed with architecture phase 1 (2026-09-29)                                                     |
| 2. Early cutoff for parse                  | 1 day        | Python change: same                                     | Deployed with architecture phase 1 (2026-09-29)                                                     |
| 3. Store the release closure once, collect | 1 to 2 days  | Deletes data: rehearsal on a scratch copy, second agent | Unnamed proofs since 018 (D-0200); all unnamed sets in phase 4 (D-0231, offline); equivalences open |
| 4. Planner that fits in memory             | 0.5 to 1 day | Python change; may shrink after step 3                  | Cache per dependency set done with step 1; streaming open                                           |
| 5. Scoped recipes from the import graph    | 1 to 2 days  | Python change: the last re-derivation any deploy causes | Deployed with architecture phase 1 (2026-09-29)                                                     |

Six to nine working days of effort, about two calendar weeks with the gate
above, the full-suite runs (about ten minutes each), the second agent's
verdict per step, and two deploy windows. Add two days of buffer for the two
things the investigation named but did not run: the retention-edge hazard in
step 1, if it needs dependency sets recorded differently rather than a fix at
the walk, and the fences installed by earlier migrations.

**Order the work so the re-derivation is paid once.** Steps 1, 2 and 5 each
change Python, and on today's rules each would cost a five-hour re-derivation
on its own. Write and test all three offline while the gate is closed, which
the gate allows, and ship them in one switch. That switch is the last
whole-graph re-derivation this worker ever runs: from then on a deploy that
touches no parser re-queues nothing, and a re-run that changes no output writes
nothing. Step 3 follows as its own deploy, because it deletes data and needs
its own rehearsal and verdict; it is where the file shrinks. Step 4 goes with
step 3 or after it, once step 3 shows how much planner work is left.

Do not interleave other Python changes with this work. Every one of them
restarts the five-hour clock and adds a graph's worth of generations that step
3 then has to collect. Config and override edits are fine; they re-queue
nothing after step 5 and only their own projections before it.

## Done means

- A deploy that changes no parser re-queues zero parse units, shown by a
  receipt.
- A re-derivation that changes no output writes no generation and withholds no
  link, shown by a receipt and by a release closure that keeps its links.
- The database is under 25 GB after step 3 and a reclaim, from 40.6 GB, and
  stops growing per deploy. `retention.max_database_bytes` comes back down
  from 64 GB with its own record after step 3 (D-0208). Getting to about 8 GB needs the out-of-scope
  step below.
- `gc --plan` completes on the live database inside the memory limit.

## Estimated sizes

| After                                                      | File                           |
| ---------------------------------------------------------- | ------------------------------ |
| Today                                                      | 40.6 GB                        |
| Steps 1 and 2 only                                         | ~40.6 GB, but it stops growing |
| Plus step 3                                                | ~20 GB                         |
| Plus removing superseded row references and parse evidence | ~8 GB                          |

The last row is not in this plan. It needs either step 4 of the
[bounded state plan](bounded-state-and-archive.md), so the bytes are somewhere
else first, or a decision that a superseded generation no root reaches is not
durable. Raise it after step 3, with a measurement.

## Not in scope

Moving to PostgreSQL. It is orthogonal: the planner dies of Python heap, not of
SQLite, and the invalidation rule, the permanent immutable tables and the
missing archive step all move across unchanged. Do this first, so the
[migration](postgres-migration/README.md) imports 8 GB instead of 40 GB and its
record-for-record audit means something. The invalidation rule itself is
now the migration's [stage 0](postgres-migration/5-stage-0.md).
