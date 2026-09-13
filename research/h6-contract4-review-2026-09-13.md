# H6 contract 4 engineering review

Reviewer: Codex coordinator. Date: 2026-09-13 UTC. This is an engineering
review of retained source accounting, not the owner's event or identity review.

Reviewed corpus: `7c944ac81361b0a343e34a29ead875b3fe6363f7750d41fda47ddec5bb400c71`.
The frozen cutoff is `2026-09-13T06:56:36.583158+00:00`. The packet contains
31,823 distinct inputs; 31,289 pass. Every example body and extract hash and
the complete report digest were independently checked. No source requests ran.

The reviewer inspected every example's guard and accounting summary and the
retained structures behind the blocking cases. The activation sample covers
registry identities, file and event indexes, ordinal finals, callback rounds,
numeric averages, empty placeholders, missing dates, malformed tables and
unknown cells. Passing counts do not authorize individual identity joins.

| Finding                                                                      | Review disposition                                                                                                                                                                     |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Two registry profiles have empty first and last names in both role envelopes | Keep `registry_identity_missing` blocking. Numeric IDs alone do not provide complete source identity.                                                                                  |
| Seven EEPro documents contain no terminal result table                       | Keep `coverage_no_terminal` blocking. The inspected auditions body says “Coming soon”; it does not prove an empty competition.                                                         |
| Two EEPro captures contain a table row shape mismatch                        | Keep `round_row_shape` blocking. Equal aggregate row counts cannot justify guessing missing cells.                                                                                     |
| Forty EEPro numeric preliminary pages declare `Avg`                          | Preserve literal scores and aggregates. The canonical contest remains unsupported; no callback conversion is authorized.                                                               |
| Seventy-two scoring.dance event pages lack dates; two fail extraction        | Keep these blocked. The inspected unpublished event has no date or round links; unrelated event dates cannot fill its fields.                                                          |
| 238 scoring.dance round pages have unnamed or undeclared columns             | Keep `critical_unknown` blocking. The inspected finals uses judge abbreviations without declared individual ownership; an unnamed nonempty column cannot be dismissed as presentation. |
| Twelve WDR documents now pass accounting                                     | Explicit `Average Raw Scores` and Solo categories remain unsupported canonically. Known ordinal, numeric and medal cells retain their literal evidence. WDR has no removal authority.  |
| One WDR document contains `4\|SVW`                                           | Keep `critical_unknown` blocking. Its meaning is unverified.                                                                                                                           |
| Calendar, council, newsletter and WDR award contracts are unassessed         | Do not activate these page kinds under this review. Their phase1 findings and legacy status remain visible.                                                                            |
| Three private registry-crosscheck artifacts have no source adapter           | Exclude them from source-contract activation; they remain verification artifacts, not failed network lookups.                                                                          |

The reviewed contract may be exercised on an isolated clone for
`wsdc_registry.dancer`, `eepro.index`, `eepro.autoindex`, `eepro.round`,
`scoringdance.sitemap`, `scoringdance.recent`, `scoringdance.event`,
`scoringdance.round` and `wdr.rounds`. The immutable review receipt must name
this exact digest. Unknown failures remain blocking. Existing unassessed facts
gain no removal authority from migration or this review.

The isolated [replay](verification/h7-contract4-replay-20260913.json) completed
in 328.39 seconds with 31,276 accepted units and no foreign-key violations.
No selected observation or source scope was added or removed. The
[payload comparison](verification/h7-contract4-payload-comparison-20260913.json)
found only the new explicit scoring-method annotation; source rows are unchanged.
Guard failures retain their prior selected legacy facts as unassessed. Eleven
older registry captures and one older event capture were superseded, preserving
the newer selected inputs.

The [canonical impact review](verification/h7-unsupported-scoring-impact-20260913.json)
evaluated 85 mapped EEPro and WDR events. Projector18 makes 67 previously parsed
contests unsupported: 57 WDR numeric or Solo contests and 10 EEPro numeric finals.
Their old descendants total 68 rounds, 1,358 entries, 930 placements and 683 final
marks; no callbacks or callback marks are affected. All 683 final marks belong
to the numeric EEPro finals and cannot be justified as ordinal placements.
The retained Tulsa2026 fixture explicitly prints numeric judge scores and an
`Avg` column. The finals exemption was removed and ordinal-finals positive
controls still pass. This is an explained withdrawal of derived facts, not
permission to delete source rows.

The coordinator approves deployment of the reviewed contracts with
projector18, retaining every listed guard failure and unsupported-contest finding.
The activation must follow the verified V3 publication and its checkpoint.
Production replay and its final public candidate still require integrity and
scope checks. This approval changes no owner year acceptance, alias decision,
identity adjudication or archive request budget.

Follow-up: `eepro.autoindex` is excluded from that deployment approval pending
its contract5 review. The retained FreedomSwing2019 index contains seven file
links, but two visible labels are truncated before the extension. The old
extractor and its accounting witness both missed those links. Contract5 must
count complete href targets independently from raw HTML. The other eight
contract4 reviews remain applicable. No autoindex policy was enforced in
production under the defective contract.
