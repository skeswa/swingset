# JesAnn Nail history inputs

These generated extracts support the local history page for WSDC #7849.
They come from acknowledged dataset commit
`2a6c7dc744fb36eabb5163c0a527d787d3721f4f`, published September 16, 2026.
The registry profile was fetched September 11.

`exports/` contains 85 registry results, 34 name-matched score-sheet entries,
six name-matched judging appearances, and their scoring details. Sheet and
judge identity links remain unconfirmed. `exports/dataset/PUBLISHED` pins the
release. `release-manifest.json.gz` and `input-verification.json` record hash
verification of all 33 Parquet files in the local baseline copy, plus export
hashes. The large Parquet copy remains under the local `tmp/` report directory.

`extract.py` is the frozen extractor from the preceding history report. It
expects the complete release under a sibling `dataset/` directory; it is
retained as evidence, not as the page’s build command. Do not hand-edit these
exports or frozen files.

The [page builder](../../../tools/quality/person-history/README.md) uses these
exports without network access. Browser check results describe the generated
page and its source percentages; they do not confirm source identity matches.

The [`jes-test/initial-baseline/`](jes-test/initial-baseline/report.md) folder
retains the first Jes Test report. It measured individual-result evidence for
19 of 85 registry entries (22.4%) in the pinned release; all 19 covered entries
had final placements, with 19/19 assessable placements agreeing. Agreement is
secondary and its denominator is limited to those 19 entries.
