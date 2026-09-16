# Finding pages and saving evidence

Swingset learns about events from calendars, the registry, and result-site
indexes. A source adapter knows how to find pages on one kind of site.

## From a link to a saved page

A _watch_ describes a request to check again, including its URL, request data,
and polling rules. The scheduler chooses a due watch. The fetch layer checks
robots.txt, request limits, and pauses before contacting the host.

When content arrives, Swingset archives it. This saved response is a
_snapshot_. Later stages work from the saved bytes, which means a parser fix
usually needs local work rather than another request to the source.

## Changed content and old results

Some sites can tell us a page has not changed. A successful check can renew
freshness without creating new facts. A failed request cannot establish that
a dancer or result is missing.

Historical collection also uses the Wayback Machine. The time an archived
page described an event can differ from the time Swingset downloaded it.
Keeping those times separate helps prevent old evidence from replacing newer facts.

## Go deeper

- [Fetching rules](../reference/fetching.md): request limits, responses, and archive storage.
- [Scheduling](../reference/scheduling.md): which work runs next.
- [Historical collection](../reference/backfill.md): dates, archive selection, and coverage.
- [Source guides](../reference/sources/README.md): details for each site.
- Code: [fetch](../../src/swingset/fetch/client.py) and [source adapters](../../src/swingset/sources/__init__.py).
