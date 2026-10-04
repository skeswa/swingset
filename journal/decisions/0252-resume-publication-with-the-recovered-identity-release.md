# D-0252: Resume publication with the recovered identity release

Recorded: 2026-10-13  
Decided by: owner, 2026-10-12 and 2026-10-13; source: "turn publishing back on", then "Fix blockers, then publish" and "Accept and publish now" after the independent check  
Topic: Publication  
Supersedes: [D-0189](0189-stop-publishing-until-identity-output-is-recovered.md)  
Superseded by: —

## Decision

The release loop publishes again (`services.swingset.dryRun = false`). The
first release, `cand_d7747db3e2a94e78`, was built by hand under an operator
hold, checked by an independent agent, and published as Hugging Face commit
`3fe7bae` on 2026-10-13 18:52 UTC. Later builds publish when they pass the
gate, once a day (D-0245).

## Why

D-0189 stopped publication until a release restored what `138352e` dropped.
This release does: events 2,105 to 2,114, events with results 107 to 276,
entries 58,558 to 146,772, and 67,012 confirmed identity links from 0. No
event loses results. A first check blocked the switch on 2026-10-12; its
blockers were fixed by D-0250 (corrections) and D-0251 (baseline headers).

The owner accepted three small regressions the gate does not measure: four
September 2026 events lose city, country and region; nine events show a
results URL as their website; four new result events duplicate WSDC events.
They are queued in the maintenance list. The gate still has no per-event loss
check (maintenance item 28), so the first automatic build is compared per
event by hand.

## Links

- [Receipt](../evidence/runtime/publication-resume-2026-10-13/receipt.json)
