# Jes Test

Jes Test is an offline benchmark for how much of JesAnn Nail’s WSDC registry
history has retained individual result evidence. It reads a local published
dataset, verifies each consumed Parquet file against the release manifest, and
writes deterministic JSON, CSV, and Markdown reports.

```sh
.venv/bin/python -m journal.tools.quality.jes_test \
  --dataset tmp/jesann-nail-history-2026-09-17/dataset \
  --output tmp/jesann-nail-history-2026-09-17/jes-test
```

The main measure is the share of registry entries with one corresponding
individual Jack & Jill entry and verified participation evidence. Final-result
and individual judge-mark coverage are shown separately. Correspondence uses
the WSDC ID or a small explicit name-alias list, edition, month, role, division,
and style. It never depends on placement or points. Ambiguous candidates do not
count as covered. These aliases apply only to this benchmark and do not change
production identity links.

The secondary measure compares numeric registry placements and finalist claims
with retained final placements. Preliminary participation can establish
coverage but cannot establish final-placement agreement. Registry points are
context, not independent evidence.

Use Jes Test as a canary when a new dataset release is prepared: compare its
report with the previous release report using `--baseline`. Shared registry
keys show coverage gains and losses, new disagreements, changed registry
claims, and evidence changes. Review those changes before accepting a release;
the benchmark is a diagnostic, not an automatic publication gate.

```sh
.venv/bin/python -m journal.tools.quality.jes_test \
  --dataset tmp/new-release \
  --baseline journal/evidence/quality/jesann-nail-2026-09-17/jes-test/initial-baseline \
  --output tmp/new-release-jes-test
```

The required `--jes-test` flag on the [history page builder](../person-history/README.md)
embeds the report for an integrated panel and uses its placement-independent
matches to group the all-participation view. The report must refer to the same
published commit and candidate as the page exports.

[Accepted decision](../../../decisions/0083-benchmark-jesann-registry-coverage.md) ·
[Baseline evidence](../../../evidence/quality/jesann-nail-2026-09-17/jes-test/initial-baseline/report.md)
