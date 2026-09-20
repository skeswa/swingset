# D-0177: Keep only maintained tools in the journal

Recorded: 2026-09-20  
Decided by: owner, 2026-09-20; source: "Clean up the journal (tools is a mess)"  
Topic: Research tools  
Supersedes: —  
Superseded by: —

## Decision

`journal/tools/` keeps only tools that answer a recurring question and have a
README entry: the collection and intake scripts, the dataset quality and Jes
Test tools, the offline admission contract check with its fixture library, the
candidate audit, the state storage measurement with its two benchmarks and JSON
profile, the scratch replay driver, and the two repository checks. The 63
one-off operation runners and their 33 test files were removed on 2026-09-20.
Their receipts stay under `journal/evidence/`, and their sources remain in
history at commit `e136d95`. The identity tools directory was removed with them;
H17 review is deferred to a website (D-0055).

## Why

The directory held about 35,000 lines of Python, more than half the size of the
pipeline itself, and most of it was exact runners bound to one packet, one
schema or one date that had already run. Each dragged a test file into the
core suite. Under D-0175 a script that runs once lives in scratch and a script
that runs twice becomes a `swingset` subcommand, so the directory now holds
what that rule keeps.

## Links

- [Tools index](../tools/README.md)
- [D-0175](0175-fewer-records-receipts-not-bundles-and-tiered-review.md)
