# D-0241: Observations are rewritten when their snapshot or version labels change

Recorded: 2026-10-07  
Decided by: owner, 2026-10-07; source: choice (b) in the contract 5 runbook, ship the label fix with contract 5  
Topic: Local state  
Supersedes: —  
Superseded by: —

## Decision

`store_parse_result` keeps a watch's stored observations only when every row
is equal: kind, scope, sequence and payload, and also snapshot id, extract
version and parser version. An admitted parse whose payload is unchanged but
whose labels differ now rewrites the rows and queues their project scopes.
Parse cutoff (D-0205) is unchanged: a cut-off parse writes nothing.

## Why

Release support matches each row's observation, labels included, against an
accepted generation under the current contract. Before this, an admitted
re-parse with an unchanged payload left the old labels in place, so a unit
stayed supported only through an older accepted generation. Contract 5
(D-0240) makes every contract-4 generation stop counting, so 5,237 round units
and 12 WDR units would have left release support for good, 1,395 of them with
labels that had already drifted.

## Alternatives and consequences

Re-admitting drifted units by hand after each contract bump was the
alternative. The cost of this choice is more re-projection: a new snapshot or
version label with an equal payload now re-projects its scopes. A parse that
is cut off writes nothing, so units whose kind keeps its contract keep their
old labels: 309 `scoringdance.event` units are outside release support today
and stay so until they are re-admitted.

## Links

- [Parsing reference](../../docs/reference/parsing.md#contract)
