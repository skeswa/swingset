# D-0255: Collect danceconvention.net finals from the results payload

Recorded: 2026-10-15  
Decided by: agent, 2026-10-15; source: the session's release work on missing 2026 results (D-0172)  
Topic: Sources  
Supersedes: the "adapter deferred to v1.1" status of the [DCN guide](../../docs/reference/sources/danceconvention.md)  
Superseded by: —

## Decision

- Register `dcn` with two page kinds. `dcn.archive` reads
  `/eventdirector/en/eventsarchive` and follows each published event with a
  `WSDC` affiliation and a `results` flag. `dcn.event` reads that event's
  `/results` tab. Both decode the `__NUXT__` payload without running scripts
  (D-0068) and detect change by extract.
- Publish final rounds only: places, leader and follower names. Preliminary
  and semifinal rankings list only the dancers each round eliminated, with no
  marks; they are withheld until the score PDFs are read.
- Do not fetch score PDFs yet. A PDF prints names that the results tab masks
  (`******* *******`, seen in Austin Rocks 2026 Novice finals). A masked name
  stays an empty cell. City and country are never kept.
- Host settings stay as the guide's §6: 2 s gap, 200 requests and 300 MB a
  day. The source ships `enabled = false`; enabling it is a production
  switch.

## Why

The 2026-10-15 search for missing 2026 results pointed at DCN for about
eight WSDC events. The DCN archive lists 14 published WSDC events of 2026
with results, and all 14 tabs were read that day: 128 contests, of which 124
finals (1,156 placed couples) fit one table. Eleven map to their registry
occurrence by name; two need a series alias row. One page per event holds
every contest.

## Alternatives and consequences

Reading the PDFs first would add bibs, marks and full prelims, but needs a
rule for masked dancers and a parser for the current layout. Emitting
prelim rankings would publish a promoted count of 0. Registering the source
edits `sources/registry.py`, `admission/contracts.py` and
`state/recipe_scope.py`, which are in every recipe scope: the deploy
re-runs every parse, project and link unit, as contract 5 did.

## Links

[danceconvention.net guide](../../docs/reference/sources/danceconvention.md)
