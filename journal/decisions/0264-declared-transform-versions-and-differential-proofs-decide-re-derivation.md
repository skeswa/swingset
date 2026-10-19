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
- The closure digests and the environment identity are still recorded at
  every acceptance, as an `audit` consumer, and enter no recipe.
- A bundle whose audit digest changed for a scope is accepted only with a
  bump of that scope's version or a **differential proof** receipt for it:
  new code run over every stored input of that transform on a clone, in
  the deploy's environment, with output identical to what is stored under
  the cutoff's own equality. Otherwise the worker refuses the bundle and
  alerts.
- The switch to the new values **re-keys** every current unit with
  equivalence rows after a clean proof of the deploying tree, so it re-runs
  nothing.

## Why

With cutoff (D-0205) and scoped recipes (D-0207, D-0261) a code change
still re-runs every unit whose scope it touches; the project and link
scopes hold 178 files each because the package is one import cycle. In the
three weeks to 2026-10-19 those scopes changed in 9 of 27 accepted bundle
transitions, so about 36,500 units re-ran nine times, and 99.98% of such
re-runs are byte-identical. The migration as planned paid for three full
re-derivations, 10 to 14 hours each, one of them a one-to-three-day release
gap. Hand-written versions already exist and already queue work; what was
missing was a check that makes forgetting one impossible to deploy.

## Alternatives and consequences

Narrower scopes alone cannot fix it: the import cycle is the scope. Running
old code beside new for every unit was considered and rejected: production's
stored output is the old code's output, so the proof compares with it and
runs old code only for differing units. The cost: a whole-graph proof is
still a pass over every unit, without writes: about 35 minutes at two
processes on the worker, measured on 2026-10-19 against 7.5 hours of
re-derivation for the same units, and a change to output
for inputs no stored snapshot has passes unbumped, which is correct but
leaves the version silent about it.

## Links

- [Stage 0 of the Postgres migration](../../docs/plans/postgres-migration/stage-0.md)
