# D-0264: Declared transform versions and differential proofs decide re-derivation, not code digests

Recorded: 2026-10-19  
Decided by: owner, 2026-10-19; source: "how can we adjust our architecture to greatly reduce rederivation which is very costly", then "write this as a plan step and make it stage 0"  
Topic: Derivation identity  
Supersedes: in part, [D-0207](0207-scope-code-recipes-by-import-closure.md) (a scope's closure digest as the recipe value) and D-0263's environment fingerprint as a trigger  
Superseded by: —

## Decision

- A unit's recipe names each code scope by its transform's **declared
  version** (`EXTRACT_VERSION`, `PARSER_VERSION`, a contract's
  `VERSIONS[kind]`, `PROJECTOR_VERSION`, `LINKER_VERSION`, and new `VERSION`
  constants on the remaining entry modules), not by the digest of the files
  in its import closure. `recipe/env` leaves the recipe. Policy and override
  inputs stay as content digests. Build keeps its closure digest.
- The closure digests and the exact runtime identity (the worker's
  interpreter and wheels, the server's dependency image) are still recorded
  at every acceptance, as an `audit` consumer, and enter no recipe.
- After a change to an audit digest, a **verify run** compares the new
  code's output and dependency set with what is stored, for every current
  unit of the affected transforms, on a copy. Releases wait until it is
  clean or every differing transform is bumped. Deploys are not gated and
  production is not held. Side effects are not compared, as D-0205 already
  does not replay them; an effects-only change needs the author's bump.
- The switch to the new values happens on the server at the switch, never
  on production SQLite: after a clean verify of the server's code over the
  imported checkpoint, a **re-key** records equivalence rows, so it re-runs
  nothing. The migration's own proof is one planned full re-derivation in
  the dry run, on the server.

## Why

With cutoff (D-0205) and scoped recipes (D-0207, D-0261) a code change
still re-runs every unit whose scope it touches; the project and link
scopes hold 178 files each because the package is one import cycle. In the
three weeks to 2026-10-19 those scopes changed in 9 of 27 accepted bundle
transitions, so about 36,500 units re-ran nine times, and 99.98% of such
re-runs are byte-identical. Without this, the switch alone would cost a
10 to 14 hour re-derivation.

## Alternatives and consequences

Narrower scopes alone cannot fix it: the import cycle is the scope.

A fuller proof system was designed first and reviewed four times: old code
against new code in separate runtimes with a versioned records protocol, a
bootstrap adapter, comparison of side effects, deploys gated by a proof
under a production hold, per-unit repair rows and an activation check in
every loop. Each piece closed a real gap. On 2026-10-19 the owner chose
the lighter shape instead, because the full system was larger than the
port itself, needed three production deploys before the switch, and held
production for up to about 70 minutes per deploy. The gaps accepted: an
effects-only change without a bump deploys unnoticed, as today; a
forgotten output bump runs until the verify run finds it, but no release
uses it; read sets on Postgres are checked by the test suite, not at run
time. The deferred pieces and their triggers are listed on the plan page.

Cost: a whole-graph verify run is about 35 minutes at two processes on the
worker (estimate from measured times, 2026-10-19), against about 7.5 hours
of re-derivation for the same units.

## Links

- [Stage 0 of the Postgres migration](../../docs/plans/postgres-migration/5-stage-0.md)
