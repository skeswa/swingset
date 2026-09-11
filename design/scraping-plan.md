# Scraping implementation plan

Written 2026-09-08 from the research in `research/scraping-techniques.md`
and the playbooks in `docs/sources/`. This is the order of work for the
fetch and parse side of the pipeline. It refines [milestones](milestones.md);
where they disagree, this document wins until the milestones are edited.

## Constraints that shape the plan

1. Some site operators are friends of the owner, and EEPro has an API
   coming. The scraper must be the guest they never notice, and each
   source adapter must be replaceable by an API client without touching
   parsers or the data model.
2. Everything runs on hardware we own. No scraping services, no
   proxies, no headless browsers in the pipeline.
3. The four platforms cover 89% of held events; the long tail is
   handled by overrides and generic adapters, never by per-event code.

## Principles, in priority order

1. **Do not fetch.** Anything older than 30 days comes from the Wayback
   Machine when it has it.
2. **Fetch the smallest thing that changes.** EEPro autoindex, WDR
   `routeInfo.json`, scoring.dance event page; refetch a child page on
   a clock only where the parent cannot reveal a correction inside it.
3. **Trust only honest validators.** Conditional GET where it works
   (EEPro, WDR, Wayback); normalized fingerprints elsewhere (DCN,
   calendar, scoring.dance); discard bodies that are unchanged by
   fingerprint.
4. **Fixed budgets per host,** in requests and, for DCN, bytes. A
   budget is a stop, not a goal.
5. **Operators hold the switch.** A `User-agent: swingset` robots group
   stops us within 24 h; a 403 or challenge pauses us for 24 h and
   alerts a person; `enabled = false` in config is ours.
6. **Talk first.** Before the first live poll of a friend's site, the
   owner sends them the playbook's numbers and asks what they prefer.

## Operator conversation (before phase 2)

For each friend-run site, one message containing:

- the User-Agent string and the GitHub README that explains the project;
- the poll intervals, request count, and bytes per event weekend from
  the playbook;
- the robots.txt switch, and that a `Crawl-delay` is honored;
- three optional favors, each of which removes load: a `Last-Modified`
  or `ETag` on dynamic index pages; a tiny JSON listing of events or
  files; an email when results are posted, so we do not poll at all;
- the question of whether they would rather we not poll during the
  event and only read afterwards. Their answer becomes the host's
  config, committed with the date.

Record the date and outcome in the playbook's section 1.

## Architecture changes to the design

These are small and are folded into the design documents in this
commit.

| Change | Where |
|---|---|
| Each page kind has one pure `extract(body) -> Extract`; its canonical hash is the fingerprint and its content is the input to record construction, so nothing is parsed twice. Unchanged fingerprint: body discarded, `checked_at` recorded | [fetching](fetching.md#change-detection), [parsing](parsing.md#contract) |
| `Accept-Encoding: gzip` only, never brotli, so Apache's `-gzip` ETag suffix is stable and every client we run decodes it | [fetching](fetching.md#politeness-rules) |
| Per-host byte budget in addition to the request budget; defaults in fetching.md, overrides only in playbooks | [fetching](fetching.md#politeness-rules) |
| The Wayback Machine is a host in `hosts.toml` and a transport in the fetch layer; backfill watches carry an archive URL | [fetching](fetching.md#archive), [scheduling](scheduling.md#watch-states-and-intervals) |
| Watch kind `autoindex` (EEPro) and `json` (WDR) | [scheduling](scheduling.md#watches) |
| World Dance Registry is a source with its own discovery (link scan plus overrides) | [sources](sources.md#world-dance-registry-pro-score-scoresworlddanceregistrycom) |
| `overrides/source_urls.csv` feeds long-tail and WDR watches | [repository layout](repository-layout.md) |
| Robots: a `User-agent: swingset` group is honored above the wildcard; 4xx including 403 means unrestricted | [fetching](fetching.md#politeness-rules) |

## Phases

Each phase ends with tests passing offline and a dataset publish that
is strictly better than the last. Request budgets are per host per day
unless stated.

### Phase 0: fetch core (milestone M0)

Deliverables:

- `fetch/client.py`: httpx client, HTTP/1.1 keep-alive, 30 s timeouts,
  fixed headers, no cookies.
- `fetch/politeness.py`: per-host gate (one in flight, `next_allowed_at`,
  `Crawl-delay`, `Retry-After`, RateLimit header parsing as a hint),
  request and byte budgets, pause states with the doubling rules.
  Redirects are not auto-followed: each hop is a new request through
  the gate of its own host, at most 3 hops.
- `fetch/robots.py`: Protego, 24 h cache, 4xx unrestricted, 5xx
  disallow-all, per-agent group precedence.
- `fetch/archive.py`: content-addressed blobs, `snapshots` rows, derived
  blobs (for evaluated payloads).
- `fetch/wayback.py`: CDX query with resume keys, `id_` body fetch, its
  own host settings (10 s gap to start).
- `schedule/`: watches table, state machine, priority order, run budget.
- `swingset doctor`, `swingset cycle`, run summaries.
- Tests: `respx` for HTTP, a fake clock for the gate, golden fixtures.

Done when a cycle runs every 15 min on the box against the WSDC
calendar only, robots and budgets are visible in `doctor`, and a restore
on a clean VM works.

Verification during this phase: WSDC calendar fingerprint stability
over a week; Wayback rate behavior at 10 s gaps.

### Phase 1: registry mirror (M1)

Unchanged from the design: bootstrap sweep at a 2 s gap, dump
cross-check, bounded daily new-id probes while recent eligible unlinked
Newcomer or Novice finalists exist, weekly probes year-round otherwise,
and trickle refresh. Runs inside cycles at lowest priority.

### Phase 2: EEPro (M2)

Preceded by the operator conversation.

- `eepro.index`, `eepro.autoindex`, `eepro.round` parsers with fixtures
  from the archive.
- Watches: index page on the index schedule; one autoindex watch per
  slug on the live schedule; round watches fetched only on listing
  change, with conditional GET.
- The adapter exposes `EeproSource` with `discover()`, `policy()`,
  `extract()`. The future API client implements the same interface.
- Canonical model through `placements`; name-only linking;
  `link_candidates` and `review_queue`.

Done when a full past event is queryable end to end and a live weekend
stayed within the playbook's load estimate.

### Phase 3: scoring.dance (M3)

- `scoringdance.sitemap`, `scoringdance.recent`, `scoringdance.event`,
  `scoringdance.round` parsers.
- Fingerprint on parsed links (event page) and parsed cells with
  attributes (round page); `If-Modified-Since` sent anyway
  for edge 304s; challenge detection pauses the host.
- WSDC ids feed the linker; weights fit on this data; registry
  confirmation loop.

Round refreshes are the first to wait when the host budget runs short.
Done when matching registry evidence automatically produces `confirmed`
links after publication and the nonce check (raw hash vs fingerprint) has
been answered from a month of snapshots. The owner's roughly one-week
posting estimate is an expectation, not an acceptance deadline.

### Phase 3b: World Dance Registry (new)

- `wdr.rounds` and `wdr.awards` parsers on `routeInfo.json`.
- Discovery: `overrides/source_urls.csv` seeded from
  `research/results-sources.csv`; the daily event-site link scan that
  runs from 14 days before the event through 30 days after it; a yearly
  CDX read.
- Conditional GET on JSON; no HTML fetched.

Done when the 14 known events parse and a live weekend shows almost all
polls as 304s. Open questions (`S<n>`,
finals bib) are answered from fixtures and recorded in the playbook.

### Phase 4: danceconvention.net (M4)

- First task: the one-time bundle read to find the tab-navigation JSON
  endpoint. If it exists, is unauthenticated, and is allowed by robots,
  it becomes the results watch and the rest of this phase gets cheaper
  by roughly 50×.
- `dcn.list`, `dcn.event_results`, `dcn.round_pdf` parsers; `node`
  evaluation with the derived-blob cache; fingerprint on the `results`
  subtree; PDFs fetched only when a round's rankings change.
- Host settings as in the playbook's section 6 (they are stricter than
  the defaults on every axis).

Done when DCN events reach parity with EEPro and a live weekend stayed
under the byte budget.

### Phase 5: long tail, heats, judges (M5)

- `generic.html_table`, `generic.pdf_table`, `google_drive.folder`,
  `swingfiction.api` adapters driven by `overrides/source_urls.csv`.
- The link scan writes suggested override rows to the review queue.
- Heats where public; judge linking; suppression path tested end to end.
- The LLM draft tool (`swingset draft <url>`) as a manual command that
  writes to the review queue, never to tables.

### Phase 6: backfill to 2010-01-01 (M6)

Owned by [backfill](backfill.md), work packages WP11 to WP16.

- Wayback first for every source, newest year first, at the Wayback
  host's own gate. A capture counts only if it parses into complete
  results; otherwise it is a gap. Origin only for gaps, one event per
  cycle per host, DCN one per day, EEPro only after the operator names
  the old slugs, Step Right Solutions never.
- Events are enumerated from archived calendar captures, archived
  platform indexes, the Step Right index, and registry occurrences.
- The `coverage` table and card list every year, source, and coverage
  tier, and how many events came from the archive versus the origin.

## Per-host settings

Not repeated here. Defaults are in [fetching](fetching.md#politeness-rules);
each host's overrides are in its playbook's section 6 and nowhere else;
`swingset doctor` prints the effective table from `config/hosts.toml`.
A review of load should read the playbook, not this plan.

## Things to verify, by phase

| Item | Phase |
|---|---|
| Calendar fingerprint stable across a week; Yoast sitemap `lastmod` for `/events/` | 0 |
| Wayback 429 behavior at a 10 s gap (none in 27 requests on 2026-09-11) | 0 |
| DCN PDFs in the archive; Step Right promotion marks; registry month versus end date | 6 |
| EEPro `Count` column meaning; `/results/<year>/` existence; operator's answer on autoindex | 2 |
| scoring.dance nonce check; Cloudflare stability at 15 min; paid API scope and price | 3 |
| WDR `S<n>`, finals bib, `attributeGroup`, registration euid mapping | 3b |
| DCN JSON endpoint; PDF bibs vs app bibs; hidden 7-digit ids | 4 |
| Drive embedded folder view; Swing Fiction operator consent | 5 |

## Risks

- **A friend asks us to stop mid-season.** Then that source is disabled,
  the card says so, and the events wait for the API. The data model
  does not change.
- **DCN stays at 1.67 MB per poll.** The byte budget caps the damage at
  300 MB a day on the busiest weekend, which is under 4 kB/s averaged.
  If the operator objects, DCN drops to cooling-only polling (results
  read after the event).
- **WDR discovery misses events.** Expected; the review queue and
  overrides are the fix, and the card lists gaps.
- **Cloudflare starts challenging scoring.dance results pages.** The
  host pauses, the owner is alerted, and the paid API question becomes
  urgent.
