# D-0078: Bind phase-one resume to frozen extension inputs

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Historical acquisition operations  
Supersedes: —  
Superseded by: —

## Decision

Package the unchanged retained 17-target intake driver with a new reviewed
wrapper. Bind the packet to an exact frozen source inventory and schema literal
of at least 28. Require that runtime, environment, configuration and overrides
to have already been accepted through ordinary input acceptance. Verify the
captured bundle and semantic input rows; neither preparation nor execution
accepts inputs or migrates the database.

Preserve the original target identities, remaining-subset review, parser-version
gate, ordinary FetchClient and shared request policy. Keep the hold, require
inactive ordinary units, and check actual schema, inputs, hold and baseline
inside the writer lock and before each ordinary request debit. Authority lost
during acquisition produces a resumable gate stop. No phase-two year gate,
source-kind policy or repair activation changes.

## Why

The old gate-preparation helper is pinned to schema 14. Merely changing that
number could permit requests under old accepted inputs. Reusing its sealed
finite driver preserves the reviewed retry, robots, cooldown and backpressure
behavior while the new wrapper proves that the extension runtime and actual
inputs agree. An exact caller-supplied frozen schema supports the separately
reviewed schema 29 integration without pretending source 003 validated it.

This record describes local implementation and tests, not operational or owner
acceptance. The coordinator selects the reviewed runtime and fresh gate.

## Links

- [Helper, checks and invocation](../investigations/2026/extension-phase1-resume-2026-09-17.md).
- [Original finite-intake contract](../evidence/collection/phase1-2026-09-13/phase1-resume-operations-final-20260913.md).
