# D-0027: Compare turn policies with all modeled pages ready

Recorded: 2026-09-16  
Decided by: agent  
Topic: Scheduling validation  
Supersedes: —  
Superseded by: —

## Decision

Compare one-, four-, and eight-request turns at 50- and 75-percent listed-page
shares over a finite 512-request prefix. Use the retained demand shape of 218
source references and 3,757 unattempted watch rows, with all modeled pages ready
from the start and continuous synthetic discovery. Exercise the real scheduler,
request gates, and accounting, with synthetic membership and successful outcomes.

## Why

The earlier model kept only one result watch per event outstanding. This
experiment also exercises selection over the larger ready queue and compares
first service without extrapolating unfinished observations into an ETA.

## Limits

These are watch-row sizes, not verified distinct page obligations. Synthetic
host and pressure limits deliberately leave those independent gates open; the
experiment does not calibrate expansion watermarks, retries, multiple classes,
artifact completion, or ordinary-host throughput. Record unserved events and
unknown first-service times. Keep operating defaults and production unchanged.
