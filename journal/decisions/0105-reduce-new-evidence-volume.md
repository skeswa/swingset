# D-0105: Reduce new evidence volume

Recorded: 2026-09-17  
Decided by: owner, 2026-09-17, Sandile Keswa (reduce file volume); source: Owner request to reduce low-signal files and large repository files during v2 implementation  
Topic: Evidence retention  
Supersedes: —  
Superseded by: D-0106 for tracking files over 1 MiB

## Decision

Use compact run receipts, reference existing evidence, keep disposable output
in scratch storage, and compress new large text before sealing it. The
[evidence guide](../evidence/README.md#keep-new-evidence-small) owns the exact
workflow. The coordinator chose 256 KiB as the compression threshold and 1 MiB
as the threshold for explaining a retention exception; these are implementation
choices, not separately owner-approved limits.

## Why

The tracked inventory at this change contained 1,865 evidence files totaling
84,644,884 bytes. Twelve exceeded 1 MiB, totaling 33,070,212 bytes. Repeated
checkpoint receipts, comparison packets and per-attempt output make review
harder. `mise run evidence-size` reproduces the size inspection without adding
another retained report.

Existing sealed bundles stay intact: gates depend on their bytes and paths.
This changes new-output practice; it does not shrink existing version-control
history or implement an external archive. D-0005's journal organization and
D-0006's decision recording remain in force. Tests and operational acceptance
requirements are unchanged.
