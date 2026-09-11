# Source playbooks

One file per site we read. A playbook is the operational truth for that
site: URL patterns, what to fetch, how often, how we know something
changed, how we parse it, and what to do when the operator asks us to
stop. Where a playbook disagrees with `design/`, the playbook wins and
the design document gets fixed.

Every playbook has the same sections in the same order, so the fetch
layer can be checked against them line by line:

1. Status: when facts were last verified, the operator relationship,
   terms of use read and when.
2. What it gives.
3. URL patterns.
4. Discovery: how new events and pages are found.
5. Change detection: what we verified about validators and caching.
6. Politeness settings: only the values that differ from the defaults
   in `design/fetching.md`. This section is the single place a host's
   overrides are written; `config/hosts.toml` is built from it.
7. Fetch procedure: the exact sequence per watch state.
8. Parsing: page shapes, selectors, payload shapes.
9. Quirks.
10. Backfill: how history is read without loading the origin.
11. Load estimate.
12. Operator switch: how the operator stops us without asking.
13. Open items.

| Playbook | Host | Covers (events in the last year) |
|---|---|---|
| [eepro](eepro.md) | `eepro.com` | 38 |
| [scoring-dance](scoring-dance.md) | `scoring.dance` | 87 |
| [danceconvention](danceconvention.md) | `danceconvention.net` | 19 |
| [world-dance-registry](world-dance-registry.md) | `scores.worlddanceregistry.com` | 14 |
| [wsdc-calendar](wsdc-calendar.md) | `worldsdc.com` | all |
| [wsdc-registry](wsdc-registry.md) | `points.worldsdc.com` | all |
| [long-tail](long-tail.md) | many | 15 |
| [wayback-machine](wayback-machine.md) | `web.archive.org` | history for every source |
| [step-right-solutions](step-right-solutions.md) | `steprightsolutions.com` (archive only) | 108 events, 2009 to 2019 |

Counts are from `research/results-sources.csv` (181 event editions
ending between 2025-09-04 and 2026-09-04). The techniques behind these
playbooks are surveyed in `research/scraping-techniques.md`.

Rules that apply to every playbook and are not repeated:

- User-Agent `swingset/<version> (+https://github.com/skeswa/swingset)`.
- One request in flight per host, 5 s floor between requests to a host,
  `Accept-Encoding: gzip` always and only, no cookies, no assets, never
  solve a challenge.
- Anything older than 30 days is read from the Wayback Machine first.
  History from 2010-01-01 is read as `design/backfill.md` describes.
- `robots.txt` is refetched every 24 h. A `User-agent: swingset` group
  is honored above everything else.
