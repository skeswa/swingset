# Evidence for the 2026-09-12 missing-data report

[Read the report](../../../missing-data-2026-09-12.md).

Public revision: `845d92a04e30b03d4a7081655e754ec2cea323aa`.
Candidate: `cand_9671fadae32d4be2`, built at 2026-09-12 16:33 UTC.
State captured at 16:37 UTC. These are dated inventories, not live status.

| Files | Contents |
|---|---|
| `capture.json`, `published-manifest.json` | Snapshot identities, input hashes, public file hashes and table counts |
| `integrity.json`, `archive-summary.json` | Published data integrity and referenced archive artifact verification |
| `events-*.csv`, `events-summary.json` | Event/year/source coverage, discovery gaps, parser failure, and research leads with exclusions |
| `registry-*.csv`, `registry-summary.json` | Missing reference IDs and claims, name differences, event mappings and withheld conflicts |
| `details-*.csv`, `details-summary.json` | Entry/judge completeness, per-round detail, point-assessment groups and identity risk inventories |
| `operations-unfetched-watches.csv` | All 4,482 never-fetched source URLs, watch IDs and parents |
| `operations-registry-refresh-due.csv` | 175 due registry confirmation/probe watches |
| `operations-summary.json`, `operations-failures.csv` | Queues, budgets, source health, last backup, and retained failure evidence |
| `field-completeness.csv` | Null and blank-string counts for all 254 columns across 17 public tables |
| `issues.json` | Open issue descriptions captured for this audit |

CSV files use standard CRLF record endings and preserve source names. Do not
edit generated inventories by hand. The report lists the capture and audit
commands. Capture inputs remain in `tmp/missing-data-2026-09-12/`; they are not
included in this evidence directory. Public metrics use pinned Parquet;
operational/lookup evidence uses the SQLite backup. Row-count agreement does
not imply those two snapshots are one database transaction.

Counts across files overlap and use different denominators. Missing IDs are
entry records, not unique people. Unmapped registry results still retain their
result and point values. Research URL leads and declared judge-roster matrix
ratios are not verified missing-result counts. See the report's qualifications
before using these inventories to prioritize work.
