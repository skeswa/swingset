# Why four events dropped out of the first daily build after contract 5

Date: 2026-10-11 (UTC)  
Type: Investigation  
Topic: Release support  
Related plan or decision: D-0240, D-0241; [reclaim receipt](../../evidence/runtime/reclaim-2026-10-11/receipt.json)

## Summary

The first daily build after contract 5 (`cand_aa37094ea6974c95`, 2026-10-11
03:29 UTC) had 1,802 events against 1,805 on 2026-10-05, so the gate refused
it (`decrease:events 2105->1802`). Four events dropped out (SwingCouver 2025,
Calgary Town Open 2026, Mooseland Swing 2026, WCSParty 2026) and one registry
event was added (River Swing Nights, correct). The four kept their source
support; the builder checked it against stale read sets. This is a defect we
introduced the conditions for, not a real loss, so the approval floor stays.

## Findings

`_Provenance.of` in `build/partitions.py` takes a unit's read set from the
generation's creation (`derivation_generations.dependency_set_id`). When derive
re-runs a unit with unchanged output it keeps the generation and records the
new read set in `derivation_equivalences`; `_link_event_reads` and
`recorded_reads` read that, this function does not. Contract 5 relabelled round
observations from extract 4 to 5 (D-0241), so stale read sets named extract-4
pairs that no contract-5 accepted generation holds, and the four events'
rows counted as unassessed legacy rows (733 to 3,490 each). A read-only
re-run of the extraction with the newest-equivalence lookup builds exactly
these four, keeps the other 1,129 withheld partitions withheld, and changes
no built partition. None of the four is among the 26 blocked rounds.

## Conclusion and follow-up

Fix `_Provenance.of` to prefer the newest equivalence's read set, with a test,
and deploy with receipts; the next build should have 1,806 events and pass the
existing floor. Whether the partition content key should include the read set
is a follow-up.

## Evidence and reproduction

Read-only scripts and simulation output stayed in the session scratch
directory (not retained); counts are from production on 2026-10-11 around
04:00 UTC.
