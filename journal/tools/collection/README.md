# Collection and historical sources tools

Build event inventories, examine archived pages, and prepare bounded source collection.

[All tools](../README.md) · [Investigations](../../investigations/collection.md) · [Evidence](../../evidence/collection/README.md)

| Script                                                         | Purpose                                                                                                                     |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| [accept_wayback_event.py](accept_wayback_event.py)             | WP11's one bounded EEPro2019 event-index read; never insert child watches.                                                  |
| [build_events.py](build_events.py)                             | build events                                                                                                                |
| [build_results_sources.py](build_results_sources.py)           | Turn the wcs-results-sources workflow output into journal/evidence/collection/source-survey-2026-09-04/results-sources.csv. |
| [intake_phase1.py](intake_phase1.py)                           | Build, execute, and review the finite v2 phase1 catalog. Never fetch score sheets.                                          |
| [review_eepro_archive.py](review_eepro_archive.py)             | Reinterpret the retained WP11 index offline without changing its fetch receipt.                                             |
| [seed_source_urls.py](seed_source_urls.py)                     | Print reviewed WDR source URL override candidates from research CSV.                                                        |
| [wayback_coverage.py](wayback_coverage.py)                     | Summarize the 2026-09-11 Wayback CDX coverage checks. Offline; stdlib only.                                                 |
| [reconcile_retained_phase1.py](reconcile_retained_phase1.py)   | Generate per-year review material from a hashed retained export, offline.                                                   |
| [prepare_phase1_resume_gate.py](prepare_phase1_resume_gate.py) | Prepare the exact remaining phase-one intake gate from locked read-only production state.                                   |

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
