# D-0261: Scopes hold only the dispatch tables' code, and each kind's parse scope names its own contract

Recorded: 2026-10-15  
Decided by: agent, 2026-10-15, under D-0172; source: maintenance item 20 and the session instruction of 2026-10-15 to narrow contract scopes, including the registry  
Topic: Derivation identity  
Supersedes: in part, D-0207 (the registry entry in every extract and parse scope)  
Superseded by: —

## Decision

- `sources/registry.py` and the new `admission/kinds.py` are dispatch
  tables (`recipe_scope.DISPATCH`): literal tables plus a lookup that
  imports one module by name. The walk stops at them. A scope that reaches
  one holds `<path>#code`, the digest of its code with the table values
  blanked, not its bytes. Each extract and parse scope names what the tables
  resolve for its kind: the owning adapter and, for a parse scope, the kind's
  contract module.
- Each assessed kind's rules and contract version live in the module
  `kinds.CONTRACTS` names for it. `admission/contracts.py` keeps the shared
  core (pagination, warning fields, the declaration check, `evaluate`).
- Adapters no longer import admission. `@declared` only marks a parse;
  `contracts.declare` attaches the declaration where admission parses a page
  (`schedule.parse`, the corpus export).
- A lookup edit re-runs every scope that reaches the module (the registry:
  all 51; the kind table: 31). A test pins each module's imports, lookups and
  code digest, and fails on any top-level statement but a docstring, an
  import, a literal table or a lookup.

## Why

A contract edit changed 41 of 45 scopes and re-ran every parse, project and
link unit: contract 5 queued 37,669 parse, 33,394 project and 2,934 link units
for two page kinds, and held fetching for an hour (D-0243). Now a comment in
`wsdc_calendar_contract.py` changes one parse scope (144 snapshots today); a
core edit changes the 20 parse scopes and no project, link or build scope; a
new source registration changes only its own kinds' scopes. On 1,459 sampled
production snapshots and 31 fixtures, the old and new code give identical
extracts, results, declarations and reports.

## Alternatives and consequences

Keeping the registry's bytes in every scope is sound but makes every new
source a full re-run. The cost: readers outside a kind's scopes see a table
edit without re-running. The build writes one public coverage row per
registered source, so a new registration reaches the next release build,
not earlier builds. The phase-two gate reads a child kind's contract version
from a module outside its scope (in link, project and build, and shallowly in
every parse scope); no adapter sets `archive_url` on a child watch today.
`admission/report.py` is in all 51 scopes and `sources/interpretation.py` in 47.

## Links

[State reference, code scopes](../../docs/reference/state.md#derivation-generations-h15)
