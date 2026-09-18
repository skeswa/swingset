# D-0090: Bind an explicit quarantine source policy

Recorded: 2026-09-17  
Decided by: agent  
Topic: Bounded fixture acquisition  
Supersedes: —  
Superseded by: —

## Decision

For the exact two-URL DCN origin fixture operation under D-0087 and D-0088,
prepare a sealed acquisition configuration that explicitly enables `dcn` only
inside that quarantine runner. Bind the complete configuration and exact URL
allowlist in its reviewed packet. Evaluate the ordinary source-enabled check
against that explicit operation configuration; do not silently treat a missing
source entry as enabled.

An explicit disabled DCN entry in the actual ordinary source configuration is
an interlock: stop rather than override it. Also preserve production H13 source,
kind, host and global pauses, robots, cooldowns, shared usage, known-spacing
requirements and stricter host limits at every request. Check the actual source
configuration and runtime binding again before each debit. An absent ordinary
DCN registration remains absent; it grants no ordinary acquisition.

## Why and limits

The owner supplied standing authority for necessary v2 fixture operations,
replacing per-fixture permission requests. The exact archived results body
prints these two URLs, and the bounded Archive lookup returned no captures.
The ordinary source is unregistered and defaults disabled. A documented,
reviewed quarantine configuration makes the limited exception explicit while
keeping the normal kill switch meaningful.

Do not edit or accept production configuration, register a source kind, create
watches or interpretations, accept a year, or dispatch the ordinary collector.
The separate runner creates only quarantine evidence and paid-request/control
receipts. Source-kind admission and historical acquisition keep their gates.
This is an implementation choice under standing authorization, not new owner
stage acceptance. Independent review must verify the exception is confined to
the exact fixture operation before any origin request.

- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
- [Exact origin scope](0088-scope-origin-score-pdf-controls-after-empty-archive-lookups.md)
