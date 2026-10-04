# Why no identity link is published

Date: 2026-10-11 (UTC)  
Type: Investigation  
Topic: Identity  
Related plan or decision: [Data quality recovery](../../../docs/plans/data-quality.md) item 1; D-0002, D-0180; [09-21 regression](published-field-gaps-2026-09-21.md)

## Summary

The linker works: live state holds 105,043 confirmed links (97,902
`source_id` from WSDC numbers printed on scoring.dance sheets, 7,141
`registry_placement`), written as recently as 2026-10-11. The release's
identity policy removes all of them. `IdentityPolicy.apply`
(`build/identity_policy.py`) publishes a default ID only if it equals the
subject's ID in the published baseline; `138352e` has none, so every ID is an
unreviewed expansion (`accuracy_gate_pending`, `withheld_pending_H17`) and the
link row is rewritten as `none/unmatched`. Nothing in the code lifts the hold.

## Findings

| Candidate                              | Built (UTC) | Confirmed links | Gate outcome       |
| -------------------------------------- | ----------- | --------------- | ------------------ |
| `cand_a9a87deb` (`4653f3a`)            | 09-13       | 29,945 + 5,265  | accepted as legacy |
| `cand_b29fcd8b` (`0923f27`)            | 09-21 09:42 | 31,817          | 29,294 accepted    |
| `cand_80719e17` (`138352e`, published) | 09-21 10:21 | 0               | none               |
| `cand_abc27e8d`                        | 10-11       | 0 published     | 57,085 pending     |

In the 10-11 candidate, 52,557 printed-ID and 4,611 registry links were revoked
by the gate, with their placement IDs and points; 83 more lack a source
reference. Inputs and code paths for both confirmed routes fire at volume
(7,333 of 7,770 scoring.dance round sheets print `data-wsdc`; 19,410 registry
rows match an eligible placement). Couple entries and judges cannot be
confirmed by these routes by design.

## Conclusion and follow-up

Restoring identity output needs a policy decision and a small code change:
either complete the H17 accuracy review (a review agent adjudicates a sample,
D-0180) and accept supported `source_id` and `registry_placement` joins, or let
still-supported IDs from the last populated release (`0923f27`) pass. About
57,000 entry IDs would return in the current candidate.

## Evidence and reproduction

Read-only queries on production and the candidate parquet, 2026-10-11; scripts
stayed in the session scratch directory.
