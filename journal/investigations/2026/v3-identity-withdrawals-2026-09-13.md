# Reviewing identity matches to withdraw (V3)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Candidate `cand_a8580553523d4b02` withdraws 1,756 of the 36,715 default entry IDs in published baseline `7cfcf4ec5dbc994d91f3e4d816f43b3abe16637b`. It retains 34,959. Independent classification reproduces every withdrawal. This is evidence validation, not human adjudication that every withdrawn identity was wrong.

The exact candidate manifest SHA-256 is `b5cf073615019ce3f5e64f1405071551d6c2c3e799fb6f17517dbdfd64bc15e4`. The [receipt](../../evidence/releases/v3/v3-identity-classification-20260913.json) pins the policy token, artifact hashes, counts, and inspection limits. No network requests, state changes, builds, or publication were performed for this audit.

## Classification

| Candidate reason                    | Independently observed cause                                                                    |   Entries |
| ----------------------------------- | ----------------------------------------------------------------------------------------------- | --------: |
| Printed source identity unavailable | Owned cell has no ID; old ID occurs under the same name in another contest or role              |     1,494 |
| Printed source identity unavailable | Owned cell prints a different ID; old ID occurs under the same name elsewhere                   |         9 |
| Printed source identity unavailable | Owned cell has no ID; same contest and role has another, bibless source row printing the old ID |         1 |
| Current decision withholds          | Duplicate participant locators; every affected subject still prints its old ID                  |       166 |
| Current decision withholds          | Printed ID conflicts with the registry support set                                              |         4 |
| Source reference unavailable        | Retained EEPro cells name paired competitors without independently owned individual cells       |        82 |
| **Total**                           |                                                                                                 | **1,756** |

The 1,504 printed-ID withdrawals all have baseline method `source_id`. None has the old ID in another retained source row with the same normalized name, contest, role, and exact bib. Borrowing an ID from an event-wide name dictionary had concealed that ownership distinction. This correction does not replace the old assertion with a different confirmation method.

The 170 current-decision holds are not 170 registry contradictions. The accepted decision journal contains zero rows. Of these holds, 166 reflect ambiguous locators and four reflect conflicting evidence. The baseline contains zero manual assertions, so the subsequent fix requiring an active positive decision for manual confirmation cannot affect this baseline.

## Raw source checks

Sixteen representative scoring.dance bodies and five registry profile bodies were read from the retained archive and verified against their SHA-256 digests. The [source proof](../../evidence/releases/v3/v3-identity-source-proof-20260913.json) preserves exact cell attributes, source URLs, raw row text, registry identity fields, and registry competition records.

- **Angelique Pernotte, Budafest 2020 open strictly:** the follower cell beside Joao Parada has no `data-wsdc` attribute. The old ID 9005 occurs elsewhere in the event. The row still preserves both competitors and their marks.
- **Maxime Zzaoui, SwingTime Denver 2024 champion Jack & Jill, leader bib 428:** the owned anchor prints 7541, while the baseline default was 1507. Registry support also identifies 7541. This release withholds the old ID without adding the other one.
- **Peter Jonsson:** seven entries print an ID different from their baseline default: two have old 19920 versus printed 23980; five have old 23980 versus printed 19920. **Antoine Duvocelle:** one entry has old 13357 versus printed 12037. These eight rows and Maxime's row account for all nine differing printed IDs.
- **Tobias Ziegler, All Star SwingJam 2026 advanced Jack & Jill, leader bib 183:** the owned cell has no ID. A different, bibless row in the same contest and role prints 23025; registry evidence also supports 23025. The existing `source_id` assertion is withheld because that support does not belong to the exact old locator. This is not a finding of a wrong person.
- **Aaron Nuno, SwingTime Denver 2024 country all-American, follower bib 227:** source rows pair him with leader bibs 377 and 107. Both follower cells print 22485, but both map to the same scoped participant locator. The conservative uniqueness check withholds the ID. All 166 ambiguous-locator subjects retain their old ID in their owned source cells; resolving this locator limitation remains separate from identity adjudication.

The four registry conflicts have matching names, role, division, and mapped event support:

| Participant           | Printed ID | Registry support IDs | Raw registry result                                              |
| --------------------- | ---------: | -------------------- | ---------------------------------------------------------------- |
| Michele Hatfield      |      20666 | 20666 and 24054      | Both profiles: SwingTime Denver, August 2024, newcomer leader, F |
| María Aguirre Roquero |      22943 | 26441                | Paris Swing Classic, February 2025, newcomer leader, 4           |
| Arnold Cheong         |      27026 | 26581                | Asia WCS Open XIII, April 2026, novice leader, 5                 |
| Brandie Charity       |      27749 | 27751                | City of Angels WCS, April 2026, novice follower, F               |

These retained registry profiles have no projected merge target. María's raw registry month is February 2025 while the canonical event slug is January 2025; the audit preserves that discrepancy. The correction withholds conflicting defaults without deciding which number is current or whether the profiles represent duplicates.

## Evidence scope

The [complete subject classification](../../evidence/releases/v3/v3-identity-withdrawals-20260913.json.gz) records all 1,756 old IDs, methods, exact references, printed and registry ID sets, and alternative source locations. Counts use baseline release tables and retained parsed source cells; representative raw body inspection independently checks those cells and registry records. This report does not claim raw inspection of every affected row or authorize new default joins.

The complete classification is stored as gzip to keep the repository small.
Decompression reproduces the original JSON byte for byte; the original JSON
SHA-256 in the classification receipt still applies. The uncompressed local
copy is ignored by Git and Jujutsu.
