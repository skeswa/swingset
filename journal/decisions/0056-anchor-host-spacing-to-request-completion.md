# D-0056: Anchor host spacing to request completion

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Fetch politeness and recovery  
Supersedes: —  
Superseded by: —

## Decision

Retain each host's effective request gap with its durable request reservation.
After the request returns or fails, require that original gap after the observed
completion boundary before admitting another request. Preserve any later existing
host deadline. Share live reservation ownership among clients in one writer
process. A process that lacks monotonic waiting evidence for a retained
reservation waits its full retained gap before another debit or request, even
when a completion timestamp was recorded. A further crash restarts that wait.
Separate system clocks in the same process share the monotonic clock domain.

Schema 28 marks previously paid hosts with unknown legacy spacing. Their original
robots delay and completion cannot be reconstructed from grant receipts. They
remain blocked until an operator records a reviewed stopped-worker baseline with
a conservative effective gap and evidence reference. That baseline still requires
a fresh full wait; it refunds no budget and clears no host or operator hold.
This operational reconciliation is already authorized and does not require a new
owner approval. The detailed implementation choice remains proposed here; no
separate owner acceptance is invented.

## Why

The fixture acquisition audit found two recorded intervals 9 and 3 milliseconds
below the required ten-second gap. The old gate spaced grants, then variable
reservation and receipt work delayed dispatch. A later grant could therefore
precede the previous dispatch plus the required gap. The fix uses an observed
completion boundary, which follows dispatch and body consumption on the ordinary
client path. It is deliberately more conservative than start-to-start spacing.

A new hook immediately before HTTP would still leave unbounded scheduling delay
between that hook and actual dispatch. Retaining a durable original gap also
avoids guessing after a crash or a robots/configuration change.

## Consequences

One grant timestamp owns its budget assessment, request debit, scheduler receipt
and response byte day. Dispatch after UTC midnight does not move those bytes to a
different day. No request or byte budget increases. Long responses reduce attainable throughput,
so earlier service estimates require fresh operating measurements. Read-only
assessment does not recover reservations or infer completion. In-process waits
use monotonic time; persisted host deadlines remain additional UTC boundaries. Recovery
depends on the existing exclusive database writer contract and proof that the
old worker has stopped. This is not a measurement of socket dispatch or remote
server completion. A fresh full wait avoids relying on an apparent UTC elapsed
interval after restart, but cannot prove remote processing has stopped.

The frozen earlier candidate and acquired fixture evidence remain unchanged.
Schema-28 deployment requires its own integrated validation, migration rehearsal
and legacy host reconciliation.

## Links

- [Investigation](../investigations/2026/request-spacing-2026-09-17.md)
- [Fetch contract](../../docs/reference/fetching.md)
