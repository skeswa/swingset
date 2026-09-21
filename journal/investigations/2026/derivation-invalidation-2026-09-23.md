# Why every deploy re-derives the whole graph, and what it costs

Date: 2026-09-23, UTC  
Type: Investigation  
Topic: Derivations, retention  
Related plan or decision: [Derivation invalidation and retention](../../../docs/plans/derivation-invalidation.md),
[D-0022](../../decisions/0022-preserve-supported-event-bases.md),
[D-0189](../../decisions/0189-stop-publishing-until-identity-output-is-recovered.md),
[D-0196](../../decisions/0196-raise-the-database-size-cap-to-64-gb.md)

## Summary

Almost all of the work the pipeline redid this week produced the same bytes it
already had. Of the 328,233 derivation generations that replaced an earlier
generation of the same unit, 328,169 have the same output digest as the one
they replaced: **99.98%**. Of the 182,819 parse generations that replaced an
earlier one, 174,866 have a byte-identical parse result: **95.65%**.

One cause explains it. Every generation's identity is a fingerprint over its
recipe, and every stage's recipe contains one input, `recipe/runtime`, which is
a digest of every file in the `swingset` package. So one edited line changes
every unit's fingerprint. The database holds 364,132 generations and 364,132
distinct fingerprints: a fingerprint has never repeated, so the "already
derived" check can never succeed, while 310,540 dancer derivations produced
only 27,901 distinct outputs.

The same mechanism, not a build failure, emptied release `138352e`: new
identities for unchanged dancer output made every old identity link
incompatible ([published field gaps](published-field-gaps-2026-09-21.md)).

The storage picture is worse than the queue behaviour suggested, and in a
different place than expected. The 40.6 GB database is 47% dependency-set
manifests that no generation references — 17.68 GiB of release closures written
by build attempts and never collected, plus another 1.74 GB of the same
manifests stored a second time inside eight build generations' recipes. A
further 7.8 GB is row references belonging to superseded generations, and
4.1 GB is superseded parse evidence. Together that is 80% of the file, and
**none of it can be removed by any code in the tree**: `gc --apply` may delete
only `derivation_payloads` rows, and only once a generation is archived, which
needs a table that does not exist yet.

Recommendation: an early-cutoff rule at both stages — recompute, compare the
output bytes, and keep the existing generation when they match — plus recipes
scoped to the modules a stage actually imports. Cutoff first, because it stops
the published regression and the growth; scoping second, because it stops the
five hours of CPU. Neither weakens provenance: the rule recomputes before it
reuses, which is exactly what D-0022 asked for.

## Context

Three things happened between 2026-09-21 and 2026-09-23.

1. Every deploy re-queued all 32,522 parse units, about five hours of work,
   even when no parser could have been affected. The 05:45 UTC switch on
   2026-09-23 changed one file, `history/closure.py`, which no parser imports.
2. Re-derived units got new generation ids although their output was
   unchanged, and downstream units then read `mixed_dependency_generations`
   and were withheld. On 2026-09-21 that emptied every identity link out of a
   published release.
3. The database grew from 5 GB to 40 GB in a week. The retention planner that
   would collect superseded generations is killed by the worker's 8 GB memory
   limit, so the size cap was raised to 32, then 48, then 64 GB
   ([D-0186](../../decisions/0186-raise-the-database-size-cap-to-32-gb.md),
   [D-0194](../../decisions/0194-raise-the-database-size-cap-to-48-gb.md),
   [D-0196](../../decisions/0196-raise-the-database-size-cap-to-64-gb.md)).

This investigation measures all three on the live database, traces what an
early-cutoff rule would do to the release closure and its proofs, and proposes
an invalidation granularity. It changed nothing and deployed nothing.

## How the measurements were taken

Every query ran read-only against the live worker, with the cycle still
writing, so counts drift by tens of rows between queries:

```sh
orb -m swingset -u root sh -c 'cd /var/lib/swingset && sudo -u swingset \
  /var/lib/swingset/venv/bin/python - <<"PY"
import sqlite3
conn = sqlite3.connect("file:/var/lib/swingset/state.sqlite?mode=ro", uri=True)
conn.execute("PRAGMA query_only=1")
...
PY'
```

The file was 9,919,760 pages of 4,096 bytes, 40,631,373,824 bytes, with a
1.86 GiB write-ahead log, on SQLite 3.50.4.

Two shapes of query were infeasible and were replaced. A size histogram over
`derivation_dependency_sets.manifest_json` was killed by the memory limit twice
because SQLite materializes the 235 MB values; threshold counts replaced it.
`PRAGMA query_only=1` forbids temp tables, so the release-pin ranking was done
in Python from a streamed result.

## Findings

### How much of the re-derivation is wasted

Derivation stages, every generation that replaced an earlier one:

```sql
SELECT g.stage, g.unit_kind, count(*) AS n,
       sum(CASE WHEN p.output_digest = g.output_digest THEN 1 ELSE 0 END) AS same_output
FROM derivation_generations g
JOIN derivation_generations p ON p.generation_id = g.previous_generation_id
GROUP BY g.stage, g.unit_kind ORDER BY n DESC;
```

| Stage and kind            | Replacements | Same output | Share      |
| ------------------------- | ------------ | ----------- | ---------- |
| `project` / dancer        | 281,231      | 281,120     | 99.96%     |
| `project` / event         | 24,117       | 24,023      | 99.61%     |
| `project` / source\_index | 16,465       | 16,449      | 99.90%     |
| `project` / source\_event | 3,343        | 3,343       | 100%       |
| `link` / event            | 3,185        | 3,185       | 100%       |
| `project` / calendar      | 31           | 30          |            |
| `project` / history       | 25           | 2           |            |
| `project` / inventory     | 19           | 9           |            |
| `project` / map           | 16           | 8           |            |
| `build` / release         | 7            | 0           |            |
| `build` / correction      | 9            | 0           |            |
| **Total**                 | **328,233**  | **328,169** | **99.98%** |

The parse stage does not use these tables. It writes `source_generations`, one
immutable row per attempt carrying the full parse result, report, manifest and
recipe. The same question there:

```sql
SELECT g.page_kind, count(*) AS n,
       sum(CASE WHEN p.result_json = g.result_json THEN 1 ELSE 0 END) AS same_result
FROM source_generations g
JOIN source_generations p ON p.generation_id = g.previous_generation_id
GROUP BY 1 ORDER BY n DESC;
```

| Page kind              | Replacements | Same result | Share      |
| ---------------------- | ------------ | ----------- | ---------- |
| `wsdc_registry.dancer` | 166,854      | 158,935     | 95.25%     |
| `scoringdance.round`   | 8,489        | 8,489       | 100%       |
| `eepro.round`          | 3,857        | 3,857       | 100%       |
| `scoringdance.event`   | 2,804        | 2,789       | 99.47%     |
| `eepro.autoindex`      | 646          | 646         | 100%       |
| others                 | 169          | 150         |            |
| **Total**              | **182,819**  | **174,866** | **95.65%** |

Five page kinds never chain at all: every `wsdc_calendar.events`,
`swingdancecouncil.events`, `wsdc_newsletter.events`, `wdr.awards` and
`wsdc_newsletter.index` row has a null `previous_generation_id`, so they are
outside this measurement rather than at 0%.

By day, using `created_at`:

| Day        | Derivations | Same output     | Parse gens | Same result      |
| ---------- | ----------- | --------------- | ---------- | ---------------- |
| 2026-09-21 | 165,628     | 164,868 (99.5%) | 105,410    | 91.7% of chained |
| 2026-09-22 | 86,928      | 86,745 (99.8%)  | 26,692     | 99.8%            |
| 2026-09-23 | 76,652      | 76,620 (99.96%) | 53,671     | 99.8%            |

2026-09-16 wrote 34,987 generations, all first-generation: that was the cold
start and is the only day whose work was not mostly repetition.

### Why the check never fires

```sql
SELECT stage, unit_kind, count(*), count(DISTINCT input_fingerprint),
       count(DISTINCT output_digest), count(DISTINCT unit_id)
FROM derivation_generations GROUP BY 1,2;
```

| Stage and kind          | Generations | Fingerprints | Outputs | Units  |
| ----------------------- | ----------- | ------------ | ------- | ------ |
| project / dancer        | 310,540     | 310,540      | 27,901  | 29,053 |
| project / event         | 27,049      | 27,049       | 273     | 2,932  |
| project / source\_index | 16,849      | 16,849       | 400     | 384    |
| link / event            | 6,024       | 6,024        | 179     | 2,839  |
| project / source\_event | 3,812       | 3,812        | 1       | 469    |

Across all 364,132 generations there are 364,132 distinct input fingerprints.
`derivations.desired()` reuses an existing generation only when the pointed
generation's stored `input_fingerprint` equals the new one, so the reuse path
has never once been taken. Outputs, meanwhile, repeat heavily: 310,540 dancer
derivations produced 27,901 distinct outputs, and every `project/source_event`
generation ever written has the same output digest.

Generations per unit: `project/source_index` averages 43.9 and peaks at 121;
`project/dancer` averages 10.7 and peaks at 21; `project/history` and
`project/inventory` have one unit each with 26 and 20 generations.

### Where the 40 GB actually is

From `SELECT name, sum(pgsize) FROM dbstat("main",1) GROUP BY name ORDER BY 2 DESC`
(300 seconds; `WHERE name=?` is near-instant):

| Object                                                               | Bytes          | Share |
| -------------------------------------------------------------------- | -------------- | ----- |
| `derivation_dependency_sets` (+ index)                               | 19,228,868,608 | 47.3% |
| `derivation_row_refs` family (table + 2 autoindexes + payload index) | 8,971,878,400  | 22.1% |
| `source_generations`                                                 | 5,039,730,688  | 12.4% |
| `derivation_generations` (+ indexes)                                 | 2,338,777,664  | 5.8%  |
| `derivation_payloads` (+ index)                                      | 914,112,512    | 2.2%  |
| everything else                                                      | ~4.1 GB        | ~10%  |

Three numbers inside that table were not expected.

**17.68 GiB of dependency-set manifests are reachable from nothing.** There are
727,581 sets holding 19,062,326,908 bytes. Only 180,460 of them, 77,755,209
bytes, are named by any generation's `dependency_set_id`. The other 547,121
sets hold 18,984,563,859 bytes — 46.7% of the whole database. The bulk sits in
89 manifests of 16 MiB or more, totalling 18,597,154,031 bytes, of which zero
are referenced. The ten largest are about 235 MB each and begin
`{"baseline":{"commit":...},"cutoff":...,"dependency_sets":[...]}`: they are
release closures.

`build/service.py` commits `closure.retain()` before the file phase, so a
build attempt stores its whole private closure manifest permanently whether or
not the build finishes. Only eight `build/release` generations exist, so most
of those 89 manifests belong to attempts that never completed — the seventeen
failed cycles of 2026-09-21 among them. Because every re-derivation changes the
closure, each attempt writes a _different_ manifest and the content-addressed
`INSERT OR IGNORE` never deduplicates.

**The same manifests are stored a second time.** The eight `build/release`
generations hold 1,740,446,395 bytes of `recipe_json`, an average of 217 MB
each, because a pinned build's context carries the full closure manifest and
`complete()` writes the context into `recipe_json`. That is 84% of the
`derivation_generations` table. Only 247,244,168 bytes of all `recipe_json` in
the database belongs to the newest generation of its unit.

**87.2% of row references belong to superseded generations.** 12,375,694 of
14,187,880 row references, against 35,684 live generations out of 364,196.
At the family's average of about 634 bytes a reference, that is roughly 7.8 GB.

For parse, joining `source_units` on `accepted_generation_id`: 186,064
superseded generations hold 4,144,573,234 bytes of result, report, manifest and
recipe against 691,536,633 bytes in the 31,898 current ones — 85.7% superseded.
3,833,303,537 bytes of that is in rows whose parse result is byte-identical to
their predecessor's. `report_json` for `wsdc_registry.dancer` alone is
2.48 GB, 12.7 KB per row.

So about 20.7 GB (51%) is release-closure manifests stored twice, 7.8 GB (19%)
is superseded derivation row references, and 4.1 GB (10%) is superseded parse
evidence: 80% of the file.

### Nothing in the tree can remove any of it

`retention_apply.apply_plan` is the only thing that removes anything, and the
only database rows it may delete are `derivation_payloads`. `eligible_payloads`
returns nothing until the `archived_generations` table exists, and that table
is plan step 4 of the [bounded state plan](../../../docs/plans/bounded-state-and-archive.md),
which has not been built. `derivation_generations`, `derivation_row_refs` and
`derivation_dependency_sets` carry immutability triggers and have no delete
path anywhere in the tree. `source_generations` is not in the retention plan at
all.

So even a retention planner that fitted in memory would free zero database
bytes today. It would remove disposable candidate directories and undeclared
files under `blobs/`, `extracts/` and `inputs/`, and nothing else. Queue item 8
assumed otherwise.

`derivation_dependency_sets` is worse than uncollectable: the retention plan
does not mention it. Its rows are neither local, nor archivable, nor unknown,
so the 17.68 GiB does not appear in any doctor report or plan total.

### What an early-cutoff rule would do

The rule: after a unit re-runs, fingerprint its output rows. If the digest and
row count equal those of the generation the scope currently points at, keep
that generation, record that the new fingerprint also produces it, and move no
pointer. Otherwise write a new generation exactly as today.

The decision can only be taken inside `complete()`, because
`desired()` mints the id before any row exists. `Selection.generation_id`
therefore becomes provisional, which is the source of most of the work below.

**The closure and the release manifest need no change, and that is the point.**
`build/closure.py` never compares a recipe to a generation. `_Selection.add`
enforces one generation per scope and raises `mixed_dependency_generations`
when it reaches two; `validate()` re-walks the pinned ids; `public_manifest`
publishes `selected_generations`, a list of ids. All of that keeps working, and
the fences fire less often because upstream ids stop moving. On 2026-09-21 all
29,048 dancer generations and 2,490 of 2,541 event generations were replaced by
identical output; under the rule none of the dancer ids and only 51 event ids
would have changed, so at most 51 events would have lost their links instead of
all 178 populated ones.

That is also the honest risk: those fences are what caught the regression. They
would still fire for a real input change; they would stop firing for a code
change that alters no output, which is the case they should never have fired
for.

`build/identity_policy.py` and `build/identity_restriction.py` read published
parquet, decisions and journal digests, and name no generation id. Both are
unaffected. `closure_support.support_token` deliberately excludes selection
pointers. `build/generations.completed()` keys on candidate id and manifest
hash.

The link compatibility check is `_Selection.add` itself: a link generation's
dependency set names the dancer cohort, its event and the history generation by
id, and the history anchor names the same dancers. Stable ids make them agree.

Three build-side behaviours do change and should be stated:

- `build/reuse.trigger()` digests the selected generation ids, so a code change
  that alters no project output no longer forces a rebuild. That is correct,
  and would have avoided several of this week's builds.
- `ReleaseClosure.semantic_fingerprint` would treat two releases with different
  upstream recipes but identical outputs as the same. Also correct, also worth
  writing down.
- `closure_rows.py` takes published `run_id`, `first_seen_at`, `last_seen_at`
  and `asserted_at` from the selected generation's row. Under the rule those
  freeze at the first run that produced the bytes. This is a real change to
  published provenance: the dataset would say when a value was first derived,
  not when it was last confirmed.

**Everywhere that assumes a generation id changes when the recipe changes**,
all of it in `state/` rather than `build/`:

| Place                                                  | What it assumes                                                                                                | Effect                                                                                                                                    |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `state/derivations.py` `desired()`                     | the pointed generation's stored `input_fingerprint` equals the desired one                                     | the rule does nothing unless this consults the new mapping                                                                                |
| `state/derivations.py` `_current()`                    | same comparison decides pendingness                                                                            | a kept generation reads as pending forever and re-runs every cycle                                                                        |
| `state/derivations.py` `complete()`                    | a repeat of an existing generation needs no dependency-set write                                               | the re-run's dependency set names different upstream ids and would never be stored                                                        |
| `state/derivations.py` pointer update                  | writes the new `desired_fingerprint` beside the old generation id                                              | every reader that cross-checks the two sees a real mismatch                                                                               |
| `state/derivations.py` `group()`                       | inline children capture the parent's reserved id before it commits                                             | a child holds an id the parent abandoned; `_validate_dependencies` raises                                                                 |
| `state/derivations.py` continuity                      | reuse reads continuity out of the pointed generation's `recipe_json`, and non-reuse keys support on `previous` | under the rule `previous` and current become the same generation                                                                          |
| `state/retention.py` `reachable()`                     | `derivation_generations.dependency_set_id` names everything a generation consumed                              | the generations an aliased re-run actually consumed become unreachable, so archivable. **This is a correctness hazard, not bookkeeping.** |
| `state/requirement_report.py` `build_materializations` | counts unfinished work by raw fingerprint comparison                                                           | every kept scope reads as permanently pending                                                                                             |
| `project/process.py` calendar guard                    | compares a pre-output provisional id to the pointer                                                            | the guard that stops a replay overwriting inventory-enriched rows silently stops working                                                  |
| `admission/select.py`                                  | `source_units.desired_fingerprint == source_generations.input_fingerprint` decides `current`                   | the parse-side equivalent of `_current()`; same fix needed                                                                                |
| `migrations/0029` history fence                        | bumps a revision on every `derivation_generations` insert                                                      | fewer inserts, fewer bumps; the new mapping needs the same trigger                                                                        |

Schema-wise the room exists. `generation_id` is the only unique key on
`derivation_generations`; there is no unique index on
`(stage, unit_kind, unit_id, input_fingerprint)`, so nothing in SQL enforces
that a fingerprint maps to one generation. A separate mapping table is purely
additive. But the row cannot be amended — migration 14 installs
`derivation_generation_no_update` — and migration 32 was declared the last one
on purpose ([D-0167](../../decisions/0167-intern-derivation-payloads-in-the-last-migration.md)),
so this needs migration 33 and a record that says why.

Tests that freeze the current behaviour and must invert:
`tests/test_h15_acceptance.py` has a case that changes one runtime file, keeps
the output identical, and asserts `newer.generation_id != first.generation_id`
and that two generations exist. `tests/build/test_closure_validation_scope.py`
asserts unconditionally that the provisional id was written.
`tests/test_retention_plan.py` fixtures hard-code one fingerprint per
generation.

**Provenance.** The rule does not weaken it. A published row still names one
generation; that generation still names its exact inputs and its exact recipe;
its output bytes are still the ones published. What the rule adds is a second,
checked witness: recipe R2 was run and produced the same bytes. It is not a
relabelling. [D-0022](../../decisions/0022-preserve-supported-event-bases.md)
says "Do not transplant or relabel old generations to avoid that replay" — the
rule performs the replay in full and only declines to mint a new label
afterwards, so it needs an explicit carve-out rather than a reversal.

The one thing the manifest stops telling a reader is which code version most
recently produced the bytes. The mapping row keeps that, with its own recipe
and `run_id`; it is private state, not published. The related loss is real: the
published `last_seen_at` becomes "when these bytes were first derived".

### What invalidation granularity is right

Today `recipes.capture_runtime` hashes every file under `src/swingset` plus
`pyproject.toml` and `uv.lock`, and `recipe_inputs` hands the single digest to
all four stages. That directory holds 232 Python files, 32 SQL migrations,
`link/weights.toml`, `history/alias_review.html` — and **130 fixture files**
under `*/fixtures/`, which no module in `src/swingset` opens. Only tests read
them, by path. Editing a parser fixture today re-parses 32,522 snapshots.

The proposal is one recipe input per stage and page kind, built from bytes:

- `recipe/code/extract/<page_kind>` and `recipe/code/parse/<page_kind>`: the
  transitive import closure, inside the `swingset` package, of the module that
  defines that page kind's extractor and parser, plus every non-Python file in
  any package directory that closure touches, minus `**/fixtures/**`.
- `recipe/code/project/<unit_kind>`, `recipe/code/link`, `recipe/code/build`:
  the same, from a declared entry module per unit kind.
- `recipe/env`: interpreter identity, installed distribution RECORD digests,
  `pyproject.toml` and `uv.lock`. It joins every stage's recipe, so a
  dependency upgrade still invalidates everything.

The closure is computed statically over the AST, counting every `import` and
`from` statement wherever it appears, including inside functions. This codebase
uses function-local imports heavily, and a static walk catches them; it
over-approximates, which is the safe direction. There is exactly one dynamic
import in the package, `__import__("datetime")` in `schedule/cycle.py`, and it
names a standard-library module.

The entry table — which module belongs to which stage and kind — is written by
hand, but the bytes still decide, so a forgotten version bump cannot hide a
change. A test keeps the table honest: the union of every declared closure,
plus an explicitly listed excluded set (fixtures, the CLI, the scheduler, the
backup and publish packages), must equal the whole package. Nothing may be
silently unattributed.

The captured bundle still holds every file for exact replay. Only the
_selected_ inputs change, and that is already how `recipes.captured_recipe_inputs`
separates the two.

**Extractor changes versus parser changes.** `state/work.py` already has the
right branches: `version/extract/<kind>` nulls the watch's cached extract
version and enqueues that kind's snapshots, while `version/parser/<kind>`
enqueues them without touching the extract. Scoping means the code digests take
over from the hand-written version labels and route through those same
branches. An extractor change re-extracts and re-parses that page kind's
snapshots. A parser change re-parses from the stored extract and does not
re-extract. Today neither is true: `schedule/parse.py` re-extracts whenever
`recipe/runtime` changed, so one Python edit re-extracts and re-parses every
retained snapshot.

Concretely: 29,059 of the 31,821 retained snapshots are registry dancer pages,
so a change to `sources/wsdc_registry` would still cost most of a full pass,
while a change to `history/closure.py`, the 2026-09-23 05:45 UTC case, would
cost nothing.

`state/migrations/*.sql` is reachable from `state/db.py`, which every stage
imports, so a new migration would still invalidate everything. That is the
conservative answer and it is tolerable once cutoff is in place: the cost
becomes CPU, not new rows and not a withheld release.

### What a superseded generation must leave behind

Retention never deletes a label, a dependency set or a row reference. It
decides only where the output _bytes_ live, and `recent_window` (default 3) is
one of eight root kinds, not the thing that keeps a release alive.

The simulation, ignoring roots:

```sql
WITH r AS (SELECT row_count, row_number() OVER (
             PARTITION BY stage,unit_kind,unit_id
             ORDER BY created_at DESC, generation_id DESC) rn
           FROM derivation_generations)
SELECT count(*), sum(row_count),
       sum(CASE WHEN rn<=1 THEN 1 ELSE 0 END), sum(CASE WHEN rn<=1 THEN row_count ELSE 0 END),
       sum(CASE WHEN rn<=2 THEN 1 ELSE 0 END), sum(CASE WHEN rn<=2 THEN row_count ELSE 0 END)
FROM r;
```

Keeping the newest one leaves 35,684 generations (9.80%) and 1,812,186 row
references (12.77%). Keeping the newest two leaves 70,145 (19.26%) and
3,217,573 (22.68%).

The live release pins 33,505 derivation generations through the `selected`
list in the manifest of `build/release` generation `dg_3ef8f7fd…`, written
2026-09-21T10:24:52Z. Of those, **none** would survive a window of one and 336
a window of two, and **none is still the materialized generation of its scope**.
That is not an argument against a small window: `retention._candidate_selection`
reads `selected_generations` out of the baseline candidate's own
`_meta/manifest.json`, so those 33,505 are roots by the `baseline` root kind
and the walk follows their dependency sets. It is an argument that the window
protects nothing a release needs, and it is a stark restatement of D-0189: the
published release is built entirely on generations the pipeline has since
replaced.

So a window of one is safe, and a superseded generation that no root reaches
needs to leave behind only its label, its dependency set and its row
references, which are permanent anyway.

A restore is a checkpoint or archive restore; nothing in the tree reproduces an
old release from the database. Reproducing one needs the published dataset
(immutable on Hugging Face), the private closure manifest — which is why
`closure.hydrate` looks it up in `derivation_dependency_sets` by the
`private_digest` the public manifest carries — and the bytes of the pinned
generations. The first is outside this database, the second must be exempted
from any collection of dependency-set manifests, and the third is already held
by the `baseline` and `pending_candidate` roots. Reproduction stays possible.

### Is the retention planner fixable by streaming

It is fixable and it is worth fixing, but on its own it changes nothing,
because nothing it plans can be removed.

The planner's memory goes mostly to one cache. `retention.reachable()` stores
`edges[generation_id]`, the full tuple of child generation ids, for every
generation it walks. A link generation names the whole 29,053-strong dancer
cohort, so 6,024 link generations alone hold about 175 million string
references. `_children` also calls `leaves()` without a shared `visited` set, so
the same cohort manifest is decoded once per generation rather than once. On top
of that, `_dependency_sets()` loads a row per generation, `_generation_rows()`
builds a dict per generation plus two full-table dictionaries, and the finished
plan is serialized to JSON twice, once for its bytes and once for its digest.

Two changes fix it without redesigning anything: key the child cache by
dependency-set id instead of generation id — the ids are content addresses, so
one shared cohort set is decoded once — and stream `_generation_rows` and the
plan bytes to the file instead of building them whole. `_payload_bytes` already
streams and is the model.

Sizes after each option, from today's 40.6 GB:

| Option                                                                              | Estimated file                                                                                                                                  |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Streaming planner only                                                              | 40.6 GB. Frees nothing.                                                                                                                         |
| Early cutoff at both stages, nothing collected                                      | ~40.6 GB, but it stops growing: per deploy, from ~165,000 generations, ~105,000 parse generations and one 200 MB closure manifest to near zero. |
| Cutoff, plus collecting unreferenced closure manifests and storing the closure once | ~20 GB                                                                                                                                          |
| Also removing superseded row references and superseded parse evidence               | ~8 GB, after `gc --reclaim`                                                                                                                     |

The last row needs either step 4 of the bounded state plan, so the bytes are
somewhere else first, or a decision that a superseded generation no root
reaches is not durable. Both are out of scope here and belong in the plan.
The 8 GB figure is about 1.6 times the 5.0 GB the bounded state plan measured
before any recomputation history existed, which is the sanity check on it.

### Is this a reason to move to PostgreSQL earlier

No. It is orthogonal, and moving first would make the migration harder.

The planner dies of Python heap, not of SQLite. The invalidation rule, the
permanent immutable tables and the missing archive step move to PostgreSQL
unchanged. What PostgreSQL adds here is a server-side `DELETE` and easier
online reclaim — useful once there is something the design permits deleting,
which is not the case today. Meanwhile step M3 of the
[migration plan](../../../docs/plans/postgres-migration/README.md) has to
account for every record it imports, and 80% of those records are redundant.
Cutting the database from 40 GB to about 8 GB first makes that step cheaper and
its audit meaningful.

## Conclusion and follow-up

Three changes, in this order, in
[the plan](../../../docs/plans/derivation-invalidation.md):

1. **Early cutoff**, at the derivation stages and at parse. It stops the
   published regression, stops the generation and parse-evidence growth, and
   makes the closure stable so build attempts stop writing a new 200 MB
   manifest each. It needs migration 33, a decision record carving it out of
   D-0022, and the publish tier's independent check.
2. **Collect what is already there**: the 17.68 GiB of unreferenced closure
   manifests, and the duplicate copy inside build recipes. This is the largest
   single number in the database and the fastest to recover.
3. **Scoped recipes** from the import graph, which turn a five-hour re-parse
   into nothing for a change no parser imports.

The streaming planner is worth doing with step 2, since step 2 needs the
planner to name what it collects, but on its own it frees nothing and should
not be represented as a fix for the size cap.

Queue items 4 and 8 are folded into that plan. This investigation recommends;
the decisions it implies are not yet recorded, and nothing here has been
deployed or scheduled for deployment.

## Evidence and reproduction

Every number above comes from the read-only queries quoted in place, run
against `/var/lib/swingset/state.sqlite` on the OrbStack worker `swingset` on
2026-09-23, with the cycle still writing. No receipt directory was created:
nothing was changed, and a read-only measurement of a moving database is
reproducible only to within the drift noted above.

Prior measurements this builds on:
[published field gaps](published-field-gaps-2026-09-21.md) for the 29,048
identical dancer digests and the 178 incompatible links, and
[state storage measurement](state-storage-measurement-2026-09-18.md) for the
5.0 GB baseline.
