# danceconvention.net results for 2026 WSDC events

2026-10-15. Follows [the missing 2026 results search](missing-2026-results-2026-10-15.md),
which pointed at danceconvention.net (DCN) for about eight WSDC events without
checking. This page records what DCN actually serves and how the new `dcn`
source reads it ([D-0255](../../decisions/0255-collect-danceconvention-net-finals-from-the-results-payload.md)).

## How it was read

18 requests from the Mac, one at a time, at least 5 s apart, with the project
User-Agent and no cookies: `robots.txt` (unchanged since 2026-09-08; the
archive and results tabs are allowed), the events archive, 14 results tabs and
two score PDFs. 25 MB on the wire. The archive and two results tabs are kept as
reduced fixtures (`tests/fixtures/sources/dcn/live-*.nuxt.html`): the exact
payload `<script>` of each capture in a minimal page, under the 1 MiB file
limit. The provenance beside them records each full body's SHA-256, size and
extract hash; a test checks that each excerpt extracts identically.

## What the archive lists

The archive (`/eventdirector/en/eventsarchive`) lists 44 events of 2026, now
with a `hideAffiliationOnPublicPages` field. 14 are published, `WSDC`
affiliated and flagged with results. All 14 results tabs are public
(`publishCompResults = true`):

| DCN id    | Event                         | Contests | Finals in one table | Registry occurrence           |
| --------- | ----------------------------- | -------: | ------------------: | ----------------------------- |
| 301368170 | Go West Swingfest 2026        |        5 |                   5 | wsdc-367, 2026 not yet held   |
| 301189300 | Best Of The Best 2026         |        9 |                   9 | 2026-09-best-of-the-best-wcs  |
| 301367300 | Austin Rocks / ATX ROX 2026   |       16 |                  16 | 2026-09-austin-rocks (listed) |
| 301439540 | Sea Dance Fest 2026           |       12 |                  10 | 2026-09-sea-dance-fest        |
| 301273270 | Korea Westival 2026           |       10 |                  10 | 2026-09-korea-westival        |
| 301107622 | Shakedown Swing 2026          |        5 |                   5 | 2026-08-shakedown-swing       |
| 301273271 | St.Petersburg WCS Nights 2026 |       13 |                  13 | 2026-07-st-petersburg-wcs-... |
| 300818670 | Revitalise WCS                |        9 |                   9 | 2026-07-revitalise-wcs        |
| 301108262 | Americano Dance Camp          |        8 |                   6 | 2026-07-americano-dance-camp  |
| 300760260 | Swingsation 2026              |        7 |                   7 | 2026-05-swingsation           |
| 301107621 | HONEY FEST 2026               |       11 |                  11 | 2026-04-honey-fest            |
| 300797311 | Swingvasion IV (2026)         |        3 |                   3 | 2026-03-swingvasion           |
| 300766630 | Southern Lights Swing         |        7 |                   7 | 2026-03-southern-lights-swing |
| 300804490 | Swing & Snow 2026             |       13 |                  13 | 2026-02-swing-snow            |

Totals: 128 contests, 253 rounds, 124 single-table finals with 1,156 placed
couples, 220 masked names in those finals. Of the search's list, St.
Petersburg's id is 301273271 and HONEY FEST's 301107621, not the ids it gave.
The search had Best of the Best as "not posted yet" and Korea Westival,
Shakedown Swing, Sea Dance Fest and Swingvasion as "not found"; all five have
results here. Go West Swingfest was not in the search: its 2026 registry
occurrence does not exist yet.

## What a results tab holds

One Nuxt payload per event with every contest, round and ranking. A ranking
has role, name, city, country, partner (finals and couple rounds), rank and,
for a tie, `interval` (the number of tied places). A preliminary or semifinal
lists only the dancers that round eliminated, with their overall rank; those
called back appear in the next round. Finals rank 1..N except four Pro-Am J&J
finals (Americano, Sea Dance), which rank each role separately. Division types
seen: `RANDOM_PARTNER`, `RANDOM_COUPLE`, `OPEN_COUPLE`, `PERM_COUPLE`,
`PROAM_MIXED`, `PROAM_JNJ`, `SWITCH_JNJ`. `PROAM_JNJ` rows add `proName` in
Cyrillic.

Every round links `roundscores/<roundId>.pdf`. The two read (Austin Novice
J&J finals and prelims) print bibs, judge names and codes, marks
(10/4.5/4.3/4.2/0 in prelims, ranks in finals), sums and results
(`Callback`, `Alt 1`, `-`). They print names the payload masks: the finals PDF
names Braxton White's partner, whom the tab shows only as
`******* *******`.

## How events map (D-0253)

Production state, read-only, 2026-10-15. Eleven listing names reduce to a
registry series slug with a held occurrence in the end month: they map
without help. Go West Swingfest maps once the registry adds its 2026
occurrence. Two need a reviewed `series_aliases.csv` row (not added; the
owner writes overrides):

```
printed_name,series_id,source,note,from_month,to_month
Austin Rocks | ATX ROX 2026 - West Coast Swing Magic,wsdc-300,dcn,"Austin Rocks on DCN; registry 2026-09-austin-rocks [high, D-0255]",,
KOREA WESTIVAL 2026_10th Anniversary,wsdc-268,dcn,"Korea Westival 10th edition on DCN; registry 2026-09-korea-westival [high, D-0255]",,
```

Austin's registry occurrence is still `listed`, not `held`, so even with the
row its contests go to a placeholder until the registry holds it.

## Gaps left

- Contest names with `J&J` or `JnJ` classify as `other`, not
  `jack_and_jill`, in seven events (Austin, Southern Lights, Revitalise,
  Korea, Americano, Go West, one St. Petersburg contest):
  `normalize/divisions` matches only "jack" and "jill".
- No prelims, bibs or marks until the score PDFs are read with a rule for
  masked names.
