# First-pass quality corrections

Repair reviewed 2026-09-10; backup verified and timers resumed 2026-09-11 UTC. The confirmed implementation defects from the
[first-pass audit](data-quality-2026-09-10.md) are corrected. Coverage and
source uncertainties remain explicit; this is not a numerical accuracy claim.

Baseline: public commit `b4dc135e07d7dbe8d62ef4eb7182e0223f5ec403`.
Corrected release: `ec6b7bbb84be9e5252d55c6bb1eae3feef05dd5b`. Candidate: `cand_d06d6d9e48fd41a2`.
Deployed code: `2089803d379ef4233ee45a49193ce97befcf0911`; projector 14, linker 5, EEPro round parser 6.
Corrections replayed archived evidence without fetching new source pages.

## Measured changes

| Measure                         | Pinned baseline | Corrected release |
| ------------------------------- | --------------: | ----------------: |
| Events                          |             573 |               573 |
| Events with results             |              96 |                96 |
| Contests                        |           1,792 |             1,759 |
| Rounds                          |           2,733 |             2,729 |
| Entries                         |          50,120 |            57,505 |
| Placements                      |          13,904 |            13,922 |
| Callbacks                       |          37,133 |            57,292 |
| Callback marks                  |         187,958 |           283,343 |
| Final marks                     |          78,410 |            78,612 |
| Dancers                         |           2,228 |             3,229 |
| Registry placements             |          21,230 |            33,427 |
| Registry rows mapped to events  |               0 |             1,269 |
| Placements with registry points |               0 |               153 |
| Entries with a WSDC ID          |   6,596 (13.2%) |     9,471 (16.5%) |
| Review queue                    |           2,922 |             2,427 |

Of the 153 placements with points, 141 have comparable expected points and
all 141 agree. The other 12 lack an established field size. Invalid dates,
callback arithmetic mismatches, registry event orphans, and attached-point
evidence orphans are all zero.

Results cover 61 EEPro events (8,803 placements), 22 scoring.dance events
(1,975), and 13 WDR events (3,144). The most recent results event is dated
2026-08-31; calendar metadata extends to 2030-03-10. All 96 results events
still lack city and country. WDR retains 1,149 anonymous entries.

The repaired state contains registry evidence collected after the pinned
baseline but before collection was paused. Increased registry coverage must
not be attributed entirely to code repairs.

For the original 2,228-dancer cohort, the correction recovers 1,826 `PRO`
and 90 `TCH` records. Another 132 raw `PRO` rows belong to 63 conflicting
keys and remain withheld. Together with 124 formerly hidden supported-division
conflicts, this yields 187 conflicting keys and 23,022 retained registry rows,
up from 21,230. There are now 1,191 event mappings in that same cohort.

## What changed

- Callback states retain source promotion and alternate evidence. Repeated
  partner rows cannot overwrite promotion with an elimination. Generic `Alt`
  stays unranked. Unknown outcomes, contradictory progression, and conflicting
  partner-specific marks are withheld with findings. Published summaries agree
  with retained marks. Round counts use canonical entrants.
- Complementary leader and follower panels are both projected. Shared final
  bibs are not assigned to both dancers. Explicit role and slash-pair bibs
  survive; uniquely supported final/preliminary identities reconcile. Ambiguous
  names and multiple observed bibs remain separate.
- Registry rows match unique event names and months despite differing series-ID
  schemes. Points require exact event, dancer, role, division, style, and place
  evidence. Field sizes use recorded round types. Unknown levels and raw
  `PRO`/`TCH` divisions no longer disappear or imply no points.
- Cross-year dates are valid. City of Angels' 177 placements moved intact from
  the erroneous 2027 edition to the reviewed 2026 override. Its dates remain a
  month-wide placeholder, not falsely precise source evidence.
- EEPro headings preserve WSDC/CSDC and format qualifiers while removing count
  and judging instructions. Capital Swing retains both WCS and country finals;
  distinct Swingover finals survive. Duplicate Northeast finals consolidate.
- Verified unpublished pages no longer count as parser failures. Empty detail
  pages retain dated index evidence; nine historical metadata events survive.
- The card separates calendar coverage from results, reports identity coverage
  and findings, and explains unsupported formats and withheld data.

The placement change is fully accounted for:

| Event                   | Placement delta | Archived evidence                                                                                |
| ----------------------- | --------------: | ------------------------------------------------------------------------------------------------ |
| Northeast Swing Classic |             −23 | Exact duplicate finals consolidated: 7 advanced, 8 intermediate, 8 newcomer/novice Am Follower.  |
| Capital Swing 2026      |             +38 | Recovered 12 advanced, 12 intermediate, and 14 novice WSDC finals; all 17 country finals remain. |
| Swingover 2026          |              +3 | Distinct “First Alt-American Final x 2” retained alongside the 12-row Large Final.               |
| City of Angels          |               0 | All 177 placements rekeyed from 2027 to 2026.                                                    |
| Total                   |             +18 | No unexplained event-level loss.                                                                 |

## Verification and operation

209 tests, Ruff, and strict mypy pass. Two independent builds pass manifest
hashes, all 17 schemas, row counts, primary keys, foreign keys, provenance,
dates, role/progression relationships, callback arithmetic, and exact registry
point evidence. The non-history tables agree after excluding execution
timestamps and run-derived review IDs. Historical changelog rows are retained.

The deployed full build completed in 34.694 seconds with 3 GB peak memory
and no swap. It retains 3,263,305 changelog rows. Streaming historical rows
removes the full-history allocation that caused the earlier 8 GB OOM;
caching immutable normalized names also reduced a representative linking
benchmark from 1.157 seconds to 0.061 seconds with identical output.

The [public release](https://huggingface.co/datasets/skeswa/swingset/tree/ec6b7bbb84be9e5252d55c6bb1eae3feef05dd5b)
contains the reviewed manifest and all 25 expected data files; obsolete 2027
partitions are absent. The remote consumer query returned all 13,922
placements, including 2,277 rows with couple names and the reviewed target
events.

Private archive commit `556e04b59b8404a3e5a12f91c4804eecbf8fee2f`
contains the matching baseline candidate and no pending candidate. Its 9,240
checkpoint files include matching public manifest and publication-receipt
hashes. The remote 691,200,000-byte archive transport hash and size match its
manifest. This verifies the checkpoint receipt and transport; the earlier
fresh-VM restore drills remain separate evidence.

All temporary service guards were removed and the normal cycle, backup, and
summary timers resumed at 2026-09-11 03:29:39 UTC. Before resuming, doctor
reported no pending work, candidates, host pauses, or operator pauses. The
first resumed cycle was running when checked; this does not establish a
completed healthy observation window.

## Remaining limits

- Registry bootstrap and identity coverage remain incomplete. Neither source
  IDs nor inferred links establish independently measured identity precision.
- WDR `S<n>` meanings, unlabeled Am/Pro roles, some bib ownership, and Jax
  Westie Fest 2026's exact results URL remain unresolved.
- Unsupported numeric scoring and partner-specific histories remain in raw
  evidence and the review queue. Callback rows are not a complete denominator
  for promotion rates.
- Scoring.dance event 179 is the remaining parser failure: the archived page
  advertises competitions but contains no round links or embedded round data.
  Recovery requires stronger source evidence, not guessed round IDs.
- Result-event locations and exact override dates remain incomplete. Live
  weekend/month acceptance windows and the full registry cross-check still
  need their operating evidence. The earlier OOM incident remains recorded
  and does not count toward a healthy unattended observation period.

Track continuing acceptance in issues #10, #12, #13, and #14. Issue #15 records
the correction release. Reproduce candidate checks with:

```sh
nix develop --command uv run python research/audit_candidate.py CANDIDATE_DIRECTORY audit.json
```
