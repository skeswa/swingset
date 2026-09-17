# D-0016: Stream body verification in event diagnostics

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: The owner authorized continued implementation of the history and recovery plan. This implementation choice has not been separately accepted.  
Topic: Event evidence verification  
Supersedes: —  
Superseded by: —

## Decision and reason

Add a streaming body-verification operation to the archive and use it for event
inventory checks that need only validity. Hash decompressed chunks without
retaining the whole HTML body. Preserve gzip integrity, digest checks, and the
archive's recovery contract; event diagnostics still use an archive without
recovery. Extract JSON validation remains unchanged.

The current body reader must return bytes to parsers, but inventory only needs
to know whether the referenced bytes exist and match. This separate operation
avoids allocating an entire body for that yes-or-no check. It bounds the read
buffer, not total bytes, elapsed time, or a whole event's history. The bounded
watermark verifier in [D-0015](0015-bound-event-expansion-observations.md) remains
separate work. The frozen original H16 source is unchanged.
