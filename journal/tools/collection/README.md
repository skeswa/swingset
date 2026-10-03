# Collection and historical sources tools

Build event inventories, examine archived pages, and run the finite phase-one
intake.

[All tools](../README.md) · [Investigations](../../investigations/collection.md) · [Evidence](../../evidence/collection/README.md)

| Script                                               | Purpose                                                                                                                     |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| [accept_wayback_event.py](accept_wayback_event.py)   | WP11's one bounded EEPro2019 event-index read; never insert child watches.                                                  |
| [build_events.py](build_events.py)                   | Build the event CSV from saved calendar snapshots.                                                                          |
| [build_results_sources.py](build_results_sources.py) | Turn the wcs-results-sources workflow output into journal/evidence/collection/source-survey-2026-09-04/results-sources.csv. |
| [event_site_review.py](event_site_review.py)         | Print draft `source_urls.csv` proposals from a state copy's retained event-site CDX hits. Read-only; no request.            |
| [intake_phase1.py](intake_phase1.py)                 | Build, execute, and review the finite v2 phase1 catalog. Never fetch score sheets.                                          |
| [origin_candidates.py](origin_candidates.py)         | Report origin-fallback candidates and existing origin intents from a state copy. Read-only; no request.                     |
| [seed_source_urls.py](seed_source_urls.py)           | Print reviewed WDR source URL override candidates from research CSV.                                                        |
| [wayback_coverage.py](wayback_coverage.py)           | Summarize the 2026-09-11 Wayback CDX coverage checks. Offline; stdlib only.                                                 |

## Original source survey

[Survey inputs and outputs](../../evidence/collection/source-survey-2026-09-04)
include the event CSV, platform indexes, workflow returns, and reviewed overrides.
`build_results_sources.py` reads that bundle and replaces its result CSV. Copy
the bundle to a separate checkout before reproducing it; preserve the retained
original. It requires one or more workflow JSON arguments.

`build_events.py` is the original working-directory script: it reads
`snaps/*.html` and writes `events.csv` in the current directory. Those original
snapshot inputs are not all retained here. Treat the saved CSV as evidence;
do not assume the script can recreate it from this checkout alone.

[Artifact formats](../../evidence/formats.md) describe the columns and limitations.

## Event-site review

`python -m journal.tools.collection.event_site_review --state <copy>
[--events 10] [--year Y] [--after <event_id>] [--overrides overrides/source_urls.csv]
[--out file.jsonl]` reads a state copy read-only and prints one JSON line per
proposal or gap. Only a unique HTTPS binding in one format carries a
`source_urls_csv` draft. To adopt it, check the capture, the event and the
parser, then copy the line into `overrides/source_urls.csv`. Rules:
[historical sources](../../../docs/plans/historical-sources.md#event-site-candidates-from-retained-cdx-evidence).

## Origin candidates

`python -m journal.tools.collection.origin_candidates --state <copy>
[--source S] [--config config] [--out file.json]` asks the retired origin
fallback's question offline ([D-0236](../../decisions/0236-origin-candidates-and-event-site-review-are-offline-reports.md)).
It builds the platform plan as the offers pass does and, for each page whose
archive offer stops at `capture_gap_exhausted` or `operator_pause`, prints
`origin_dispatch.candidate`'s reason (`eligible_origin` or why not) and its
proposal: the CDX queries and tried captures that prove the gap. It also
lists every `history_origin_intents` row. It refuses `/var/lib/swingset` and
a state whose `state.lock` or `state.intent` another process holds, creates
no file in the state directory, reads inside one rolled-back transaction, and
has no transport. Per-cycle cadence is not judged. To adopt a row, check the proof and the host's rules in
[backfill](../../../docs/reference/backfill.md#origin-fallback-for-gaps), then
add a reviewed row to `overrides/source_urls.csv`.

Both tools import private `src` helpers (`history.backfill._options`,
`history.event_sites._matches`). A change to either runs
`tests/test_origin_candidates_tool.py` and `tests/test_event_site_review_tool.py`.
