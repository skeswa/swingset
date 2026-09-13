# Accepted sample offsets 20–59: model-assisted review

Human review is required. This second sidecar contains 26 provisional candidate proposals and 14 abstentions for exactly the next 40 accepted sample IDs, sorted lexicographically. It creates no adjudications, gold labels, identity overrides, threshold changes or new public joins. The first `proposals.json` and `README.md` remain byte-for-byte unchanged; their hashes are recorded in the new JSON.

[proposals-accepted-020-059.json](proposals-accepted-020-059.json) records the original sample digest, seed, cohort, cutoff, subject fingerprints, actor/tool attribution, exact source references, raw scoring rows and independent registry comparisons. All 196 distinct referenced artifacts were verified: raw body SHA256 or canonical JSON archive SHA256, with portable extract file hashes recorded separately. The sample remains the same pre-release local tuning population. The inspected slice is not a new representative sample. Reviewed precision and held-out evaluation remain unavailable.

Two verified contradictions concern the frozen assertion's source provenance, not verified person identity:

- Peter Jonsson, sample `1c227d3e55df1c8a1345b519`, subject `2025-08-uptown-swing/intermediate-jj/L-134`: frozen method `source_id` chooses 23980. The cited raw Intermediate leader bib 134 cell links to registry 19920 and prints `data-wsdc="19920"`. Registry 23980 has a Novice result at that event, which cannot verify the Intermediate source assertion.
- Peter Jonsson, sample `43d3d4bd37e525b3cf2f6447`, subject `2026-04-nordic-wcs-championships/intermediate-jj/L-142`: the same frozen 23980 assertion conflicts with raw Intermediate leader bib 142 explicitly printing 19920.

The requested read-only current-public check found both joins already withheld in V4 commit `81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`: default IDs and public assertion IDs are null; status is unmatched; assertion method is none and acceptance is revoked. Their public source snapshots match the frozen raw bodies, and the retained H8 locators explicitly record source ID 19920. [peter-current-public-check.json](peter-current-public-check.json) preserves the exact public fields, source references and bindings. No immediate current-public correction was demonstrated. These findings do not authorize assigning 19920 or merging the two accounts.

Other priority human checks:

- Michele Hatfield has two same-name registry records containing the same event, year, role, division and finalist result. Placement agreement cannot disambiguate them.
- Josh Kneeland's printed ID has no retained points, while another same-name record has unrelated leader points. Neither absence nor printed ID alone settles identity.
- Johannes Forsmark's Nordic sample is April while registry evidence is May; Priscilla Bellet's Westie Gala sample is December while the corresponding registry entry is January. Resolve occurrence boundaries before using exact placement support.
- Jone Bacinskaite and Katie Beechler have same-name registry records separated by role, while the source depicts both roles under one bib. Role-specific result agreement favors the proposed record for that subject but cannot establish distinct people or authorize a merge.
- Nadia Noel's matching Masters registry event is the 2023 edition, not the sampled 2025 edition. Older-edition evidence must not be treated as current-edition support.
- Paired finals bibs, multiple roles, Strictly versus Jack&Jill contests, and unpointed prelims are kept distinct. Missing points are not automatically contradictory evidence.

| Offset | Sample ID | Subject | Model output |
| --- | --- | --- | --- |
| 20 | 1ba284b9107345b731028040 | Emilia Zalewska | Propose 20168 |
| 21 | 1c227d3e55df1c8a1345b519 | Peter Jonsson | Abstain |
| 22 | 1f253294c798f300757383d0 | Willow Vander Kooi | Propose 25755 |
| 23 | 1f40e140d1322ba94a7b869d | Christine Kearney | Propose 20219 |
| 24 | 1fbfca74b7f7221bc0a47c3e | Allison Brown | Propose 17970 |
| 25 | 224fdc10361cff8b85a9d386 | Josh Kneeland | Abstain |
| 26 | 22aee343cb5012c4a42580ce | Igor Pitangui | Propose 11629 |
| 27 | 304b1e5e607b2af8342d380d | DAVID COLLINS | Propose 14545 |
| 28 | 30d17d132ad7988210b717eb | Dario Haxhia | Propose 20974 |
| 29 | 3127032c55d2310801c8e665 | Johannes Forsmark | Abstain |
| 30 | 317b3dcf226cf97deda2c871 | Alison Gibson | Propose 19448 |
| 31 | 33f517eb2d81ee5b80a26fa8 | Ali Muller | Propose 18201 |
| 32 | 34bdedf16d6bf106c5561fe7 | Brittany Richardson | Abstain |
| 33 | 34cc7cd662890bf7029f03b9 | Priscilla Bellet | Abstain |
| 34 | 39bc6b8c05a74d6fe860db5c | John Carrez | Propose 13957 |
| 35 | 3aeec49becd7c89be2ebc244 | Josh Kneeland | Abstain |
| 36 | 3ebe6ce471f18f9b2a42f6e0 | Brock Daniels | Propose 16099 |
| 37 | 40f906fba6909b5d87c40f65 | Sergey Sirotkin | Abstain |
| 38 | 43d3d4bd37e525b3cf2f6447 | Peter Jonsson | Abstain |
| 39 | 45f228a53feb91b53ec5f0a2 | Michele Hatfield | Abstain |
| 40 | 461a87ad59d1c6f514a29d7c | Eliott Basse Green | Propose 23494 |
| 41 | 47f9d9f892a548587ffaca1c | Ed Preble | Propose 13338 |
| 42 | 48f11e9edc132570114a4fb7 | Jelena Breuer | Abstain |
| 43 | 49b0802836124e7a8163f4a6 | Jeffrey Wang | Abstain |
| 44 | 4d6aad80cc3ae60d9762180c | Kyler Pleasant | Propose 28326 |
| 45 | 4db59a6cfebd54200b6a50c4 | Sebastian Quinones | Propose 11759 |
| 46 | 4ea3db10e4c3a76d02edf049 | Maria Bileychik | Propose 12183 |
| 47 | 542b6204b4ebdd2bd7a0fd49 | Claudia Beyer | Abstain |
| 48 | 56c0a68d4869bcabf700f853 | Anthony Riojas | Propose 17190 |
| 49 | 59013426dff34993429af824 | Jone Bacinskaite | Propose 27825 |
| 50 | 5b147078fe5a937fd4a9437f | Miguel Abdala | Abstain |
| 51 | 5ddafa458b6c81282492e5d0 | ANGELA Boroughs | Propose 27826 |
| 52 | 5e35a0ae0e899f6cdb0aa724 | Brittany Richardson | Propose 20920 |
| 53 | 5fb9d263c236bbcf0f37e0e5 | Noelle Hoeppner | Propose 17182 |
| 54 | 628ae27cd41ef6989ff908c3 | Diana Fu | Propose 26882 |
| 55 | 63d6685d4bd3f08e69596075 | Elise Holland | Propose 22525 |
| 56 | 6793931e82049e3ec7905426 | Katie Beechler | Propose 27830 |
| 57 | 67eda58c1f824d839eaa34f8 | Henny Marie Smelhus Sveen | Propose 24728 |
| 58 | 69c7bf757be9422e305a5035 | Benjamin Smith | Propose 18208 |
| 59 | 69dd4dc6ecfe71fd8a2cf063 | Nadia Noel | Abstain |

Use the unchanged packet's `index.html` for human review. Inspect the exact raw source and registry locators, verify the person independently, check missing candidates, and record human attribution and evidence through the original review workflow. Do not feed either model sidecar to the evaluation command as adjudications. A reviewer who consulted these proposals should preserve that assistance disclosure; it is not a blinded review.

No third-party requests were issued. The only additional state access was a bounded read-only local VM check of the two published Peter subjects and their retained source references. No production, runtime, original packet, identity journal or override writes occurred.
