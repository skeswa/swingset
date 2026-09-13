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
