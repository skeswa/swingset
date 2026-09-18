# D-0024: Compare restored event work with an uninterrupted run

Recorded: 2026-09-16  
Decided by: agent  
Topic: Restore validation  
Supersedes: —  
Superseded by: —

## Decision

Extend the event checkpoint test beyond read-only inspection. Restore and
activate a disposable local checkpoint, resume through ordinary gates, and
compare supported output with an uninterrupted run of the same evidence.
Include paid partial turns, a retained pause, and parent evidence whose
enumeration work remains unfinished at the checkpoint.

## Why

Byte-for-byte restoration proves preservation but does not establish convergence.
The H18 extension also requires unfinished event work to resume without
refunding requests, ignoring controls, or losing new obligations.

## Consequences

Keep the test offline and use real checkpoint and scheduling interfaces. Record
any limits of its final-output comparison. Passing a synthetic local scenario
does not enable repairs or satisfy the V5/V6 publication prerequisites for V7.
