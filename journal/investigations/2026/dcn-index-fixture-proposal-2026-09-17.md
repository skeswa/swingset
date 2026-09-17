# Exact DCN index fixture, 2026-09-17

Status: Approved and executed. The owner accepted this exact exception in
[D-0060](../../decisions/0060-approve-exact-dcn-index-fixture.md). The corrected
runner captured it at 16:16:12 UTC using two requests and 2,804,642 bytes;
[independent acquisition audit](../../evidence/admission/dcn-index-review-2026-09-17/audit.json)
passed. The original proposal below remains the scope of that decision.
Parser review and source-kind activation are separate.

Request one additional archived DCN index body to establish its actual format,
event locators and empty/changed-input controls. The
[machine proposal](../../evidence/admission/fixture-review-2026-09-17/dcn-index-proposal.json)
pins its retained CDX evidence and limits. [D-0053](../../decisions/0053-approve-exact-new-source-fixture-exception.md)
approved metadata discovery but expressly excluded additional index bodies;
this request requires a separate owner decision.

| Field               | Exact target                                                                                                         |
| ------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Original            | `https://danceconvention.net/eventdirector/en/eventsarchive`                                                         |
| Capture             | `20251112105828`                                                                                                     |
| Replay              | `https://web.archive.org/web/20251112105828id_/https://danceconvention.net/eventdirector/en/eventsarchive`           |
| Retained CDX digest | `74UF7246MXYEOC53OCCDTDBA2UUKMAXE`                                                                                   |
| CDX MIME / length   | `text/html` / `1594993` (capture metadata, not a promised response length)                                           |
| Evidence            | Retained CDX page SHA-256 `d1e3f7d8031e7a0f9bb113273250abce9fdf48df13756c199b8c609b9a2387fb`, row 7 including header |

This is the newest of six retained 2025 distinct-digest captures. The other
timestamps are January 18, April 25, June 14, September 14 and October 7.
They are unrequested alternatives; failure of the selected capture does not
authorize trying another.

The proposed ceiling is five HTTP requests including any robots request and
at most three Archive redirects for the one body, 8 MiB per response, 16 MiB
total, and 15 minutes. There are zero CDX, origin, child, PDF, retry or alternate
requests. Use one request in flight and at least ten seconds between actual
dispatches, with any stricter active host or robots rule preserved. The shared
Archive budget remains 200 requests per UTC day; recheck actual remaining usage
at execution and never reset it to make this proposal fit.

The [independent audit](fixture-controls-review-2026-09-17.md) found two issue-time
gaps below ten seconds in the completed five-body run. A corrected maintained
transport with a reviewed dispatch boundary and advancing-clock regression is
a prerequisite to this new acquisition. The old fixed-manifest wrapper cannot
be repurposed. A new reviewed wrapper must pin this exact manifest, the actual
runtime/schema and acknowledged baseline, preserve H13 and host gates, and run
only through the coordinator with other worker operations serialized.

The body remains in fresh, separate, single-use quarantine. This proposal
creates no production watch, generation, observation, year acceptance, page-kind
activation or publication. A locator found in the body is review material only.
No score-PDF proposal is attached: the captured 2018 metadata page supplied no
PDF locator or capture evidence.
