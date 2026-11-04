# D-0271: Keep irreproducible source witnesses in history

Recorded: 2026-11-05  
Decided by: agent under D-0172, 2026-11-05; source: the owner's instruction to implement stage 1  
Topic: History backup and source identity  
Supersedes: —  
Superseded by: —

## Decision

History includes observation contexts with no approved immutable counterpart,
pending parse tokens for snapshots with retained bodies, and source equivalences
whose generations remain in history. Other caches, hints and leases follow
their reset rules. Retained legacy recipe code scopes resolve to fully verified
captured bundles; unresolved references refuse a backup or re-key.

Restore preserves these rows and references exactly before rebuilding. It does
not approve legacy evidence, replace a pending token with the restore time, or
forget another fingerprint of a revoked generation.
Ordinary rebuilding may append an exact-source proof alias for a retained
generation; every original alias still passes its key, field and reference audit.

## Why

The real sample contains authoritative observations from before admission
records. Dropping them loses facts. Pending tokens enter source-generation ids;
resetting them changed an interrupted paid turn's provenance. Source equivalences
also carry revocation associations. A clean differential proof cannot recover
missing historical version declarations from an absent code archive.

These are history dependencies, even when neighboring rows are disposable.
The backup can grow; reducing its size does not justify discarding them.

## Links

[History classes and audits](../../docs/plans/postgres-migration/4-hosting.md#backups-and-recovery).
