# D-0248: Publish new identity links that pass the H17 review rules

Recorded: 2026-10-11  
Decided by: owner, 2026-10-11; source: "Agent accuracy review, then accept", then, on the review's rules, "Adopt them (Recommended)"  
Topic: Identity  
Supersedes: the baseline-only rule for new default joins ("withheld until H17 permits expansion", H10)  
Superseded by: —

## Decision

A release publishes a default entry WSDC id that the published baseline lacks
when its confirmed link is:

- `registry_placement`, as today checked against this release's registry rows; or
- `source_id`, and (a) the printed id is in the release's `dancers` table,
  (b) the sheet name equals the registry name after `normalize_name`, or has
  the same words reordered, and (c) no role contradiction: the registry does
  not credit the id in the other role for this result, and the entry is not
  final-only in a non-switch contest where the registry or one id in both
  roles shows a role flip.

The source interpretation must be usable: legacy support keeps a baseline
id but never authorizes a new join, so such rows are withheld as
`source_interpretation_unavailable` (992 of 54,382 in the 2026-10-12
simulation; independent check condition C1).

Failures are withheld as `printed_identity_not_in_registry`,
`printed_name_disagrees` or `role_contradicted`; passes count as
`accepted_h17`. Manual links and judges keep the baseline-only rule, and
baseline IDs keep their path. The manifest's `accuracy_expansions` is `D-0248`.

## Why

A review agent (D-0180) adjudicated 1,375 of the 105,043 live confirmed links
of 2026-10-11 in 16 strata. All 640 sampled in the accepted strata were
correct (stratified one-sided 95% upper error bound 2.2%). Printed IDs whose
name disagrees were often wrong (158 of 377 with unrelated names), and 107 of
125 IDs missing from the registry were wrong. The rules accept 100,118 links
(7,141 registry, 92,977 printed) and withhold 4,925. The family-ID guard (one
id, two names at an event) adds nothing: rule (b) already withholds one side.

## Alternatives and consequences

Restoring IDs from the last populated release (`0923f27`) was the other
option; it would have restored fewer IDs and none from newer pages. The review
found 34 contests in 27 events where the scoring.dance parser reads a
follower-first final as leaders; rule (c) withholds them until that is fixed.

## Links

- [Investigation](../investigations/2026/why-no-identity-link-is-published-2026-10-11.md)
