# D-0009: Retain source event page obligations separately from completion

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: —  
Topic: Event completion  
Supersedes: —  
Superseded by: —

## Decision

Implement the accepted event-completion design through three small interfaces:
bounded admission bootstrap, indexed watch membership, and read-only verification
of one source event. Identify an event by source and source reference. Keep its
canonical alias as optional reporting metadata.

Identify a page by source, method, normalized HTTPX URL without its fragment,
and the form dictionary passed to FetchClient. Watch kind and archive capture
are supporting evidence, not extra logical pages. Preserve every parent claim;
only a reviewed watch-removal contract may retire that parent's omitted claims.
Another parent's claim survives. Retain predecessor, membership digest,
additions, removals, parent snapshots, and admitted generations.

Keep pagination unknown: the existing `captured_document_end` witness proves
only a captured document. Do not infer an entire event's completeness from it.
Legacy watches establish uncertainty, not interpreted pages or historical turns.
Registry dancers and calendar indexes do not create source event groups.
Classify event requests by their actual parser purpose through one shared map;
transport-shaped watch kinds such as EEPro `autoindex` and WDR `json` do not
exclude them. Unknown parsers on legacy `event`/`round` watches retain an
unassessed hint, without granting interpretation authority.

Recompute acquired and interpreted stages from verified local artifacts and
admission evidence. Related revocation follows both unit/fingerprint and
snapshot/parser identity. Validate the source generation's content address
before using its declarations. Missing or corrupt evidence reopens the stage;
it does not erase the obligation. Verification neither fetches nor restores.

## Why

Aliases and mutable work queues cannot preserve a stable page denominator.
A smaller parent response can be incomplete; treating every omission as removal
would make a regression look like progress. A persisted complete flag can
outlive its artifacts. Separate immutable obligations from current evidence so
one interface owns these checks and scheduler/report callers need not repeat them.

## Alternatives

- Infer pages from canonical contests: loses unmapped source events and confuses
  pages with results.
- Replace membership on every refresh: discards unsupported omissions and age.
- Scan every event's artifacts on each scheduler selection: unbounded repeated
  work. Membership queries are indexed; detailed verification is explicitly scoped.

## Consequences

The first increment can account for known pages while whole-event pagination
remains unknown. It does not claim publication, eligible-service age, historical
successful progress, or full extension acceptance. Bootstrap records at most 100
accepted decisions and 100 legacy watches per call, atomically with its cursors.
No successful stage flag becomes durable authority. Retained generations increase
state size; one-event verification reads artifacts sequentially and caches only
small evidence statuses for that call.

## Links

- [Persistence contract](../../docs/reference/state.md#event-completion-persistence-h14-extension)
- [Scheduling contract](../../docs/reference/scheduling.md#event-completion)
- [Recovery plan](../../docs/plans/recovery/README.md)
- [Offline scenarios](../../tests/test_event_enumerations.py)
