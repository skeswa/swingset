# D-0272: Retain selected derivation provenance

Recorded: 2026-11-05  
Decided by: agent under D-0172, 2026-11-05; source: the owner's instruction to implement stage 1  
Topic: History backup and release provenance  
Supersedes: —  
Superseded by: —

## Decision

A derivation scope with a selected generation is a history root, including a
unit that has never been published. Retain its generation and the ordinary
reference and output closure. Restore audits its original fields exactly.

Before activation, invalidate the active scope's cache certificate while
keeping its selected generation as a cutoff witness. Ordinary replay must
recompute the output before certifying it. An equal output keeps the original
generation, timestamps and run; a cold witness cannot satisfy a prerequisite.

## Why

The real sample rebuilt the same facts with new processing timestamps and run
ids. This changed every dancer and registry placement and added 32,539 false
changelog rows. Those provenance fields are immutable evidence of the original
work. Downloading a baseline alone cannot recover an unpublished unit's receipt.

## Alternatives and consequences

Keeping only published closures loses unpublished provenance. Copying old
timestamps into newly minted generations misstates their identity. Retaining
selected scopes can keep predecessor generations and their outputs, so history
may exceed its early size estimate. Measure the sample here and the full state
in stage 2; size does not override the reference rule.

## Links

[History classes and audits](../../docs/plans/postgres-migration/4-hosting.md#backups-and-recovery).
