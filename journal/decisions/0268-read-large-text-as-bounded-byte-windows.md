# D-0268: Read large text as bounded byte windows

Recorded: 2026-11-04  
Decided by: agent under D-0172, 2026-11-04; source: the owner's instruction to implement stage 1  
Topic: Postgres large values  
Supersedes: —  
Superseded by: —

## Decision

Read retained UTF-8 text through `ops.text_bytes(text, offset, length)`, a
strict immutable alias of Postgres 17's internal `bytea_substr`. The offset
and length count bytes. Store large recipe and dependency manifests with
`STORAGE EXTERNAL` so each small read fetches only its TOAST slice. Initial
schema installation requires the separate administrative migration job;
the worker cannot define internal functions or change storage rules.

## Why

Converting the whole text to `bytea` before every slice read only 26 MiB of
a 256 MiB field in 15 seconds. The native slice read all 256 MiB in 1.49
seconds with a 43 MiB client, preserving the exact bytes. Postgres text and
bytea share the varlena representation; the pinned implementation slices
that representation directly. A future major upgrade must recheck this
internal interface before accepting its schema checksum.

## Links

[Measured trigger and byte checks](../evidence/runtime/postgres-stage1-2026-11-04/large-value-reader.json).
