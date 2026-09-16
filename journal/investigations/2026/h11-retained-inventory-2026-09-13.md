# Measuring the missing-work inventory (H11)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

The missing-work inventory records gaps and why they need attention. This record concerns its checks or deployment preparation. The original work ID is H11.

The schema-6 shadow inventory completed two full scans of the retained G1
checkpoint on a disposable VM copy. No third-party requests, live writes,
repairs, or publications occurred. This verifies bounded local inventory work;
it does not satisfy human identity adjudication gates.

Input: `v2-g1-verified-20260913T0428Z`, schema 5, 31,629 snapshots,
94,811 entries, 21,156 placements, and five pending derivations. Sampling
correctly refuses this unsettled identity population.

| Measurement                                 | First scan | Second scan |
| ------------------------------------------- | ---------: | ----------: |
| Checked scopes                              |    109,049 |     109,049 |
| Pages of at most 100                        |      1,091 |       1,091 |
| Full scan, seconds                          |       9.64 |        4.58 |
| Slowest page, seconds                       |      0.072 |       0.057 |
| Doctor inventory with fixed cohort, seconds |      0.740 |       0.775 |

Opening and migrating the copy took 1.59 seconds. Both passes reported the
same states: 7,622 needs review, 67,216 satisfied, 3,775 ready, 776 out of
scope, and 3,054 waiting for source. The fixed cohort retained all 14,451
members. Its completion percentage and ETA stayed unavailable because it
contains blocked requirements. Attempts and retirements were not counted as
successful repairs. These timings are observations on this VM, not universal
latency guarantees. Artifact caches were warm on the second pass.

The scanner now seeks each scope in its native index order. Snapshot digest,
registry occurrence, per-requirement transition/attempt history, source-ID,
and placement entry indexes replace repeated table scans. Cohort reports
build membership sets once. Identity sampling loads finalist IDs once.
Query-plan regression tests exercise the production SQL; the receipt records
artifact, history and finalist plans from the actual copy.

Machine receipt: [h11-retained-inventory-20260913.json](../../evidence/runtime/h11/h11-retained-inventory-20260913.json).
Reproduce with [benchmark_requirements.py](../../tools/runtime/benchmark_requirements.py), on a
fresh SQLite backup of the checkpoint named `swingset-h11-review`. Link its
`blobs` and `extracts` to the checkpoint read-only. Never use the live state as
the benchmark destination:

```sh
PYTHONPATH=src python -m journal.tools.runtime.benchmark_requirements \
  --state /var/tmp/swingset-h11-review \
  --checkpoint /var/lib/swingset/checkpoints/v2-g1-verified-20260913T0428Z \
  --output /var/tmp/swingset-h11-review/timing.json
```

The benchmark bounds each scan to 2,000 pages by default, records whether it
completed, and never treats reaching the bound as completion. Schema 6 resets
an old partial lexical cursor for the new structured scope order. Migration
tests verify findings, transitions, attempts and fixed membership survive.

## Foreign-key deletion follow-up

The V1 replay subsequently exposed whole-table foreign-key checks during
canonical row deletion. Schema 7 adds child lookup indexes for every remaining
foreign key whose columns were not the leading columns of an existing index.
An audit of every FK lookup in the actual retained copy then found only indexed
searches. The migration took 1.41 seconds and increased the database file from
1,403,138,048 to 1,493,680,128 bytes. Deleting the 20 entries with the most
callback marks took 0.0017 seconds, including cascades for 700 marks and 40
callbacks. That transaction was rolled back; all measured row counts returned
to their original values and `foreign_key_check` returned no errors.

Receipt: [h11-retained-fk-indexes-20260913.json](../../evidence/runtime/h11/h11-retained-fk-indexes-20260913.json).
Reproduce on the disposable copy with
[benchmark_fk_indexes.py](../../tools/runtime/benchmark_fk_indexes.py):

```sh
PYTHONPATH=src python -m journal.tools.runtime.benchmark_fk_indexes \
  --state /var/tmp/swingset-h11-review \
  --output /var/tmp/swingset-h11-review/fk-timing.json
```

Schema 6 and 7 upgrades run once through the migration runner. A repeat open
preserves the current partial inventory cursor and all existing indexes.
Neither support migration changes observation provenance, identity decisions,
foreign-key semantics, or review history.

## Schema 10 cohort discovery reporting

Migration and reporting were measured on an isolated SQLite backup of
`v2-v3-published-20260913T0715Z`, at
`/var/tmp/swingset-h11-schema10-review`. The source checkpoint stayed schema 9;
its 1,510,047,744-byte database SHA-256 was unchanged after the benchmark.
Only the disposable copy was opened by the working schema 10 runtime. The
source contained 31,629 snapshots, 95,178 entries, 21,166 placements, 54,144
findings, 54,609 transitions, and no captured cohorts.

| Measurement                                | First scan | Repeat scan |
| ------------------------------------------ | ---------: | ----------: |
| Checked scopes                             |    131,876 |     131,876 |
| Pages of at most 100                       |      1,319 |       1,319 |
| Complete scan, seconds                     |     27.710 |       7.994 |
| Slowest page, seconds                      |      0.371 |       0.049 |
| Inventory report, seconds                  |      1.509 |       1.357 |
| Newly discovered work outside fresh cohort |          0 |           0 |

Opening and migrating took 0.653 seconds. The first scan populated the inventory
to 105,735 requirements; the second retained identical state counts. The new
35,201-member cohort captured transition cutoff 106,200. Already-satisfied and
retired requirements did not become new discoveries. The first-open lookup uses
the existing covering requirement-transition index. Both scans completed their
full cursor cycle; neither reached the 2,000-page bound. Scratch integrity and
foreign-key checks passed.

There was no production legacy cohort to migrate. One **synthetic legacy
control** was added only to the disposable schema 9 copy before migration,
using an actual retained timestamp and requirement scope. Its NULL cutoff
survived. Both reports preserved 1,513 known later discoveries and 233 records
with unknown same-timestamp order; the exact outside-work count remained NULL.
This control tests ambiguity handling, not a historical production capture.

The report correction has measurable overhead. Three alternating calls to the
pinned V3 report and current report on the same scratch database gave medians
of 1.512 and 2.340 seconds. Current calls ranged from 1.618 to 2.979 seconds.
Those observations stayed below the default five-second watch interval; they
are not a universal bound or evidence of zero regression. The earlier G1 scan
used a smaller population and cannot isolate this change's cost. The new
first-open map runs during reporting, not during each scanner page.

Receipt: [h11-schema10-cohort-benchmark-20260913.json](../../evidence/runtime/h11/h11-schema10-cohort-benchmark-20260913.json).
After making a fresh disposable SQLite backup and read-only artifact links,
reproduce scan and report measurements with:

```sh
PYTHONPATH=src python -m journal.tools.runtime.benchmark_requirements \
  --state /var/tmp/swingset-h11-schema10-review \
  --checkpoint /var/lib/swingset/checkpoints/v2-v3-published-20260913T0715Z \
  --previous-report-module /var/tmp/swingset-v3-release-source/src/swingset/state/requirement_report.py \
  --output /var/tmp/swingset-h11-schema10-review/timing.json
```

For a fresh destination, add `--clone-checkpoint` to let the script create the
copy and artifact links. The checkpoint connection uses `mode=ro&immutable=1`
and closes explicitly; existing WAL sidecars prevent automatic cloning.
Live database readers must retain ordinary read-only mode so committed WAL
contents remain visible.

Follow-up: this measurement's original one-off clone and inspection commands
used ordinary `mode=ro`. Later V4 preflight found WAL/SHM sidecars in the source
checkpoint directory. The database hash remained unchanged, but an unchanged
database hash does not establish an unchanged checkpoint directory. All those
reader processes exited; the deployment owner handles verification and any
quarantine separately. Future benchmark checkpoint reads use immutable mode. The
receipt preserves this limitation rather than claiming no checkpoint
filesystem effects.

The receipt records the synthetic control's scope separately. No third-party
requests, production mutations, repair execution, or publications occurred.
