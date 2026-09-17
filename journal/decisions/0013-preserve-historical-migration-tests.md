# D-0013: Keep historical migration tests pinned to their reviewed schema

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: The owner authorized continued implementation. These integration choices have not received separate acceptance.  
Topic: Migration validation  
Supersedes: —  
Superseded by: —

## Decision and reason

Keep the WP16 schema-14-to-15 migration tests on a copied schema-15 source and
an explicitly pinned test runtime, matching the earlier H14/H15 test pattern.
The production preparation helper continues rejecting every other source and
schema. Advancing the application's schema must not silently expand that
historical deployment authority or disable its regression tests.

Index every new foreign-key child lookup in migrations 16 and 17. The existing
performance regression exposed seven missing indexes; parent checks must not
scan these growing evidence tables.

The first combined run exposed these issues. Its original log is retained in
[event-completion evidence](../evidence/runtime/event-completion-2026-09-16/combined-tests.log).
