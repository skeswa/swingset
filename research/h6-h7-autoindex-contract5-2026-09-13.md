# EEPro autoindex contract 5 follow-up

Autoindex extractor 3 fixes an observed omission: Apache truncates visible
anchor labels, including their file extension, while the href retains the full
filename. Contract 5 independently enumerates raw DOM hrefs and verifies every
HTML/PDF FileRow plus the ordered HTML child watches. The retained Freedom
Swing 2019 document has seven files; the former extractor retained five.

The new [corpus receipt](verification/h6-autoindex-contract5-20260913.json)
assesses all 73 retained autoindexes from the published V1 checkpoint and
phase1 intake. Every document passes. The corpus digest is
`6750eccd884a572c00a2f525d719a8c434b1dfdea73367558c0cb490e63c7755`, with
cutoff `2026-09-13T07:24:05.788133+00:00`. All 73 bodies, extracts, and reports
are linked from
`/tmp/swingset-v2-phase1-check/h6-autoindex-contract5-final/index.html`.
The coordinator independently reviewed this changed contract in
[h6-autoindex-contract5-review-2026-09-13.md](h6-autoindex-contract5-review-2026-09-13.md).
The previous autoindex contract-4 review was not reused. Other kinds remain
at contract 4.

The clean [replay receipt](verification/h7-autoindex-contract5-replay-20260913.json)
uses a fresh disposable copy of the published V1 checkpoint and only its 72
existing autoindexes. All 72 were accepted in 18.27 seconds. Foreign-key
checks were clean. FileRows increased from 449 to 457, with no removed scopes,
added scopes, or removed files. Eleven positional payload changes are sequence
shifts; the independent [native-file comparison](verification/h7-autoindex-contract5-native-comparison-20260913.json)
confirms that no previously retained file changed.

The eight recovered files are:

| Existing source index | File                                                    | Effect of parsing the admitted index |
| --------------------- | ------------------------------------------------------- | ------------------------------------ |
| adc2025               | ctstcountryswingfinals.html                             | New round watch                      |
| adc2026               | ctstcountryswingfinals.html                             | New round watch                      |
| adc2026               | ctstcountryswingprelims.html                            | New round watch                      |
| adc2026               | divisional_scoring_analysis_dancehall-championships.pdf | FileRow only                         |
| njhustle2025          | proamsalsawcsbachata.html                               | New round watch                      |
| njhustle2026          | proamsalsawcsbachata.html                               | New round watch                      |
| worlds2025            | dancehallshowdownfinals.html                            | New round watch                      |
| worlds2025            | dancehallshowdownprelims.html                           | New round watch                      |

All five parent indexes are existing V1 watches with `via=origin` and
`kind=autoindex`. These discoveries do not claim G2 year acceptance or
historical phase2 authorization. Production activation and parsing would create
seven new round watches: four for 2025 and three for 2026. Workers remain held;
this rehearsal made no source requests. The coordinator's G3 decision preserves
all eight FileRows and declared child intents, while production bootstrap does
not commit new round acquisition watches. Each parse runs inside an outer
transaction; only newly created, unfetched round watches are removed before
commit, and an explicit acquisition-gate finding retains the URL and source
event. Existing V1 watch controls remain unchanged. Future gated historical
work can materialize those intents after year acceptance. This is an operational
bootstrap gate, not a changed admission contract or inferred year acceptance.
The isolated replay above records the parser's proposals; the production gate
is applied by the coordinator's separate bootstrap operation.

Freedom Swing 2019 remains a read-only corpus/fixture control. The final replay
contains zero watches for `eepro:freedomswing2019`, and the phase1 transfer
continues to exclude its transport-only snapshot. A preliminary disposable
clone that included it was discarded after the G3 clarification; no controls
from that clone remain.

Blocked-admission public summaries now use actual stored generation guard
codes, including the staging-only `non_authoritative_row_loss` reason. Dynamic
exception text is stripped before public summaries. The regression proves that
a source document whose pure report passes can still be blocked by historical
row witnesses and disclose the correct reason. Focused admission, parse-recovery,
and EEPro tests passed 38 tests; scoped lint and type checks passed. Root owns
final formatting, the full suite, and deployment/activation.
