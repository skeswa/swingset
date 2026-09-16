# Newsletter event-list empty review, 2026-09-13

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

The coordinator independently read every extracted page of the eight retained
PDFs below and checked rendered layouts for Volume 14 page 1, Volume 20 page 2,
and Volume 21 page 1. The documents contain 11 pages in total. Their text is
complete enough to identify their purpose; the inspected graphics are council
logos, not event listings. Raw PDFs and capture receipts remain in the
`wsdc_newsletter/fixtures/` directory.

All eight bodies are **empty for registry-event listings**. They are not empty
documents. Policy dates, committee-member names, hypothetical examples, and
a health-discussion town hall do not establish competitive event editions.
The Volume 21 “Event Listings” heading introduces submission instructions,
not a list of events. This review makes no claim that no events occurred.

| Volume | Pages | Content                                                                                                               | Body SHA-256                                                       |
| ------ | ----: | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 14     |     1 | Pandemic support letter and home activities.                                                                          | `085674f10a5da1ab09fe968b42eee10a1301a39cc04a7aebce4c173a973d8fc7` |
| 15     |     1 | Event cancellation policy and future calendar submission reminder.                                                    | `aafbd00bb65715cc286d65033bb1f8fbb52b1c1f6eac9fea01cc149683b31566` |
| 16     |     1 | Event health restrictions and cancellation policy.                                                                    | `b00e313eecfaf55361c87040d3e056d1902664f38e6dded7e37373a91dc9923d` |
| 17     |     1 | Points freeze, event policies, and future role-rule discussion.                                                       | `e10a45b1cbafd884f1d9be5a8fba406170f1cd6e57e0f908d9a2655396aa6f90` |
| 18     |     1 | Reopening policy, venues, and competitor threshold changes.                                                           | `59a6bf606025adc6cb8f809124cb3414ebb47f0b84c5f8e64fe66c59221ac759` |
| 19     |     1 | New website announcement; reminder that absent event forms leave the calendar incomplete.                             | `793ee2d9e0810bc4cc1983efaeadb5d3c7dd820f8eed08664519af08746c5936` |
| 20     |     3 | Three pages of venue, competition, judging, and points policies.                                                      | `12545410f858740ff9ac6a85852eb8b32bf64767bdc59aaf860c56a3e617dcfd` |
| 21     |     2 | Two pages of advisory committee names, listing-submission instructions, event policies, and a health town-hall recap. | `b7a224d7799f3eb1ca13ec9307ac1fc2149cccdabe680585d6aa1abf268fc82b` |

Reviewer: Codex coordinator, 2026-09-13 UTC. Method: independent reading of
the full retained text and representative rendered PDF pages, with body
digests verified against capture receipts. This is a parser-content review,
not the owner’s per-year G2 acceptance or an H17 identity adjudication.

`sources/wsdc_newsletter/empty_review.py` records both the raw body and
extracted-pages digests plus the page count. Only those exact combinations
return a legitimate empty event-list result. A changed body, changed text,
or unknown layout retains the ordinary review finding. Extractor 2 carries
the body digest; parser 5 applies this finite review. It grants no removal
authority by itself; H7 still owns admission policy.
