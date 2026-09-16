# WSDC newsletters

Archived WSDC newsletters can list events missing from later calendars. An empty event list needs source-specific evidence, not just an empty parse.

[All sources](README.md) · [Shared fetching rules](../fetching.md)

The council newsletter index is `https://www.worldsdc.com/newsletter/`.
`wsdc_newsletter.index` discovers same-host PDF issue links; each PDF is
an index watch parsed by `wsdc_newsletter.events`. Nothing here creates
score-sheet watches.

The event-list research recorded 28 issues, Volumes 1 through 30 with
Volumes 24 and 28 absent, covering December 2016 through May 2024. The
intake records the actual discovered issue list; missing or changed
issues are findings rather than invented URLs.

Use one request in flight per host, at least five seconds between
requests, the project User-Agent, conditional validators, and the
configured host budget. The finite phase 1 intake uses the same gate as
live collection and stores PDF bytes unchanged. Newsletter requests are
origin reads, not Wayback captures.

Dates and names come from the sidebar and dated approval notices. The
Volume 6 fixture exercises two-page, multiple-column layouts and 41
dated rows. Quarter-only approvals, malformed dates, unrecognized
sidebars, and purple trial/member-activity colour remain review findings.
Colours are not guessed from extracted text. A recognized issue without
safe dated rows is recorded with its warning, never silently discarded.

`journal/tools/collection/intake_phase1.py` builds the target catalog, resumes the intake,
and writes the owner review pack. The catalog, ledger, snapshots, and
review CSVs together explain each issue's outcome. `events_accepted`
remains an explicit owner decision after the findings are resolved.
