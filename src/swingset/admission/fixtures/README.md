# Admission contract fixtures

Exact retained source bodies copied from the published V1 checkpoint on
2026-09-13. Adjacent JSON records retain the source URL, snapshot identifier,
body digest, observation time, and recipe used in the first shadow corpus.
No source requests were made to collect these fixtures.

Contract 1 correctly withheld undeclared headers. Contract 2 explicitly
accounts for empty presentation columns, displayed callback aggregates,
promote/alternate columns, and WDR type-12 tally columns. The individual mark
and outcome interpretation remains the existing projector's responsibility.
No source cells were removed or padded.

Contract 4 retains the exact EEPro `Avg` header and WDR `roundSubHeader`
as `RoundSheet.scoring_method_raw`. Numeric EEPro contests, including finals,
WDR `Average Raw Scores`, and WDR's explicit `Solo` category remain
unsupported in the canonical scoring model. Raw judge scores, averages,
medals, and participants remain available; no numeric score is converted
into a callback mark or relative placement. WDR `#` is an excluded display
ordinal. Unknown methods, typed cells, or callback/mark codes still block.

`wdr-numeric.body` is the exact retained document from snapshot
`snap_20260909T150519Z_3e342cde93d4`; its adjacent metadata pins the actual
source URL and body digest. It contains six numeric divisions and a Solo
contest. WDR `S<n>` and paired-finals bib ownership remain unverified
findings and explicitly excluded interpretations. WDR receives no removal
authority, including when its accounting report passes.

`eepro-numeric-finals.body` retains the Tulsa 2026 numeric finals document
from snapshot `snap_20260910T032433Z_164af9cc267f`. Its `Avg` layout has
literal judge scores such as 94, 97.2 and 98.9. A printed Finals label does
not make these relative-placement ranks. The projector marks these contests
unsupported, and a separate ordinary ordinal-finals fixture remains a
passing canonical control.

The first contract's failed reports remain in the retained shadow corpus.
These fixtures document a code-reviewed contract revision; they do not record
human corpus approval or activate any policy.

Contract 5 fixtures were copied on 2026-10-06 from production's stored bodies
(read-only, digest-checked) for the admission adjudication (D-0240). The
adjacent JSON records the snapshot, URL, watch, source reference and body
digest. No source requests were made.

| Fixture                            | Snapshot                                          | Shows                                                       |
| ---------------------------------- | ------------------------------------------------- | ----------------------------------------------------------- |
| `scoringdance-partner-quarter`     | `snap_20261006T022723Z_b25242e14f18_713e05129836` | one-table SILC quarter; partner in blank column 2           |
| `scoringdance-partner-allamerican` | `snap_20260912T152433Z_a50ef25b12d8`              | All-American prelim; partners partly with `data-wsdc`       |
| `scoringdance-two-table-partner`   | `snap_20261006T010819Z_bf58ae47ef2f_984351dd97d7` | two tables, followers first; stays blocked                  |
| `scoringdance-cja-semi`            | `snap_20261006T021714Z_30ffaed5d016_55362ddcada3` | chief-judge adjustment after Σ and its `CJA` remark         |
| `scoringdance-v1-final`            | `snap_20260923T195712Z_da88fda65040_ba63c1b920e4` | `V1` violation badge on a placed final row                  |
| `scoringdance-dq-final`            | `snap_20261005T180141Z_b547ea4079ff_12e681a839f3` | `DQ Lead` on 1st and 3rd; fails the outcome-conflict guard  |
| `scoringdance-diss-prelim`         | `snap_20261006T002309Z_8c7fa6204738_7280b6eaa0f7` | `DISS` on rows the page shows as eliminated                 |
| `scoringdance-anon-judges`         | `snap_20260912T152448Z_7ee2873231eb`              | Champions final with random lowercase judge codes           |
| `wdr-svw`                          | `snap_20260909T150549Z_abf51d8711ef`              | NASDE attribute group and a `4\|SVW` Placement Order mark   |
