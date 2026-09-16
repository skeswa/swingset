# Retained warning review — 2026-09-13 UTC

This review used retained phase-1 bodies only. No new requests were made.
Adjacent `.json` files record each snapshot URL, body digest and fetch time;
`.extract.json` files retain the exact extraction inspected here.

| Evidence | Finding | Disposition |
| --- | --- | --- |
| 2017-01-24 WSDC calendar map | Nine explicit single dates, including `Jun 8, 2017` | Calendar parser 7 retains the printed date as both endpoints. The Chicago Classic HIATUS label remains intact. No multi-day duration is inferred. |
| Newsletter Volume 3 | Spaces before date commas; `(RM5)` shares a line with its date; internal spacing in ROCKY MOUNTAIN and BRIDGE TOWN | Newsletter parser 6 recovers nine additional rows, including two dated approval notices. A shifted next bullet cannot supply the prior listing's date. |
| Newsletter Volume 7 | New Orleans Dance Mardi Gras: `July 19 - 22, 2018 **` | The asterisk is a printed footnote, not part of the year. One dated row recovered; both hiatus notices remain findings. |
| Newsletter Volume 13 | Winter Coast Swing date appears next to article text | Recover the complete date in the right sidebar column without treating the adjacent article word `of` as part of the event name. One row recovered; Anchor Festival hiatus remains a finding. |
| Newsletter Volume 12 | `Dec 31, 2019, Jan 3-5, 2020` | Discontinuous printed dates do not establish one continuous interval. Keep the finding. |
| Newsletter Volume 23 | `Dec 29, 2022, Jan 2, 2023` | Comma-separated dates lack an explicit range marker. Keep the finding. Undated sibling listings remain findings. |
| Other retained newsletters | Years `20189` and `201717`, hiatus and cancellation text | No grammar fix can establish the intended dates. Keep findings. |

Across all 28 newsletter bodies, parser 6 adds eleven dated rows and removes
or changes no previously parsed rows. The exact body/text/page-count empty
review allowlist remains unchanged. Source tests check the actual recovered
names and dates and reject malformed or discontinuous alternatives. This is
parser review only; no series aliases or year acceptance were approved.
