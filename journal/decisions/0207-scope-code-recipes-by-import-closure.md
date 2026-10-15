# D-0207: Scope code recipes by import closure

Recorded: 2026-09-25  
Decided by: agent, 2026-09-25, under D-0175; source: phase 1 of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Derivation identity  
Supersedes: —  
Superseded by: amended by D-0261 (dispatch tables in no scope; per-kind contracts)

## Decision

Each captured bundle adds `recipes/scopes.json`. It names, by digest, the
files in each code scope's static import closure over the captured bytes:
`recipe/code/extract/<kind>`, `recipe/code/parse/<kind>`,
`recipe/code/project/<unit_kind>`, `recipe/code/link` and
`recipe/code/build`. `recipe/env` covers the interpreter, installed
distributions and pyproject's `[project]` table (`runtime/project.json`); the
whole `pyproject.toml` and `uv.lock` stay in the bundle but out of it, so a
tooling edit or a dev-tool bump re-parses nothing. A unit's recipe holds only its
own scope, `recipe/env` and its policy inputs. This replaces H15's
package-wide `recipe/runtime`. `state/recipe_scope.py` owns the entry table
and an `EXCLUDED` list, with a reason for each path no scope names.

- An extract or parse scope invalidates like its kind's version label;
  `recipe/env` re-queues every parse, as `recipe/runtime` did.
- Extract reuse keys on the kind's extract scope.
- Source-generation recipes and admission currency hold only the kind's parse
  inputs, not every accepted input.
- `schedule/parse.py` and the parse writers (`recipe_scope.PARSE_WRITERS`:
  admission select and support, observations, verification, findings,
  `history/acquisition`, `intent`, `origin_dispatch`, `fetch/wayback`) are
  shallow entries in every parse scope: their own bytes and their packages'
  `__init__`, not their imports or data files. Tests pin what
  `schedule/parse.py` imports (the rest is `clock`, `state/db`, `state/work`)
  and each shallow module's imports outside its scope.
- A parse _retry_ still keys on `version/repository`: a deploy that fixes
  code a parse unit ran re-opens the attempts it blocked, without
  re-queueing parsed snapshots.
- A bundle without `scopes.json` still yields `recipe/runtime`, so retained
  bundles reproduce. The first scoped accept sets it to the absent digest and
  re-queues everything once.

## Why

Any code change re-parsed about 33,900 snapshots and re-ran every project and
link unit. After three import moves a parse closure is 39 to 47 files. A
non-parser change now re-queues no parse.

## Alternatives and consequences

The package is one import cycle (F4). Project, link and build closures are
still about 214 files, so an edit inside the cycle re-runs every project and
link unit; with cutoff, that costs CPU, not generations. Phase 2 splits the
cycle. The walk sees `importlib` only in `sources/registry.py`, whose entry
names the owning adapter. A shallow module's storage imports (`state/db`,
`state/work`, `schedule/watches` and a few more, pinned by test) are outside
the parse scopes; a change there that alters parse output needs a label bump.
A new module must join a scope or `EXCLUDED`, and a new import of a shallow
module must be reviewed, or a test fails.

## Links

- [Derivation invalidation plan, step 2](../../docs/plans/derivation-invalidation.md)
