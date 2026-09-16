# Replaying source admission on isolated state (H7)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Source admission selects a checked interpretation as current. This record describes a replay on isolated state. The original work ID is H7.

The isolated replay completed in 328.39 seconds against a fresh SQLite backup
of `v2-v1-published-20260913T0543Z`. It used the coordinator’s
[recorded engineering review](h6-contract4-review-2026-09-13.md) of corpus
`7c944ac81361b0a343e34a29ead875b3fe6363f7750d41fda47ddec5bb400c71`.
All database writes and new extracts stayed under
`/var/tmp/swingset-h7-contract4-rehearsal`; retained bodies and extracts were
read through a separate immutable archive. There were no source requests,
production writes, projection writes, or publications.

The [replay receipt](../../evidence/admission/source-checks/h7-contract4-replay-20260913.json) records
31,276 accepted units, 324 guarded generations, 12 obsolete interpretations,
336 units still labeled legacy-unassessed, and zero foreign-key violations.
The guards preserved the prior selected evidence; they did not silently
assess or revoke legacy facts. The pure corpus included both the published
checkpoint and phase1 evidence. This stateful replay used the published
checkpoint only; the additional phase1 EEPro autoindex was not present.

| Page kind            | Attempts | Accepted | Guarded | Superseded | Payload annotations |
| -------------------- | -------: | -------: | ------: | ---------: | ------------------: |
| eepro.index          |        1 |        1 |       0 |          0 |                   0 |
| eepro.autoindex      |       72 |       72 |       0 |          0 |                   0 |
| eepro.round          |      447 |      438 |       9 |          0 |                3112 |
| scoringdance.sitemap |        1 |        1 |       0 |          0 |                   0 |
| scoringdance.recent  |        1 |        1 |       0 |          0 |                   0 |
| scoringdance.event   |      385 |      310 |      74 |          1 |                   0 |
| scoringdance.round   |     1633 |     1395 |     238 |          0 |                2018 |
| wdr.rounds           |       13 |       12 |       1 |          0 |                 413 |
| wsdc_registry.dancer |    29059 |    29046 |       2 |         11 |                   0 |

Every kind had zero added or removed selected observations and zero added or
removed scopes. The independent
[payload comparison](../../evidence/admission/source-checks/h7-contract4-payload-comparison-20260913.json)
found only the appended `scoring_method_raw` field: EEPro gained 178 literal
`Avg` annotations and 2,934 null defaults; scoring.dance gained 2,018 null
defaults; WDR gained 213 `Placement Order`, 152 callback-formula, and 48
`Average Raw Scores` annotations. All previously retained cells were unchanged.

The replay exposed ten EEPro numeric score sheets explicitly titled Finals.
The prior numeric guard exempted finals, which allowed integer scores to
be interpreted as ordinal final marks. Projector 18 removes that exemption.
The exact Tulsa 2026 body and an offline regression retain the decimal
scores and prove that no canonical marks or placements are emitted. The
ordinary ordinal-finals positive controls still pass. This projector repair
does not change admission reports or require reinterpreting the source rows.

The [read-only projected impact audit](../../evidence/admission/source-checks/h7-unsupported-scoring-impact-20260913.json)
assessed 85 mapped EEPro/WDR events. Compared with the baseline canonical
contests, 67 formerly parsed contests become explicitly unsupported: 57 WDR
numeric or Solo contests and ten EEPro numeric finals. Their baseline rows
are shown below. These are proposed canonical withdrawals requiring
engineering review; the audit did not write them to the database. Raw source
evidence remains available.

| Source | Contests | Rounds | Entries | Placements | Final marks |
| ------ | -------: | -----: | ------: | ---------: | ----------: |
| WDR    |       57 |     58 |   1,139 |        748 |           0 |
| EEPro  |       10 |     10 |     219 |        182 |         683 |

No callbacks or callback marks belong to these newly unsupported contests.
All 683 withheld final marks are from the ten EEPro numeric finals. The
impact artifact names every contest and its exact baseline row counts.
It does not claim a full publication diff or reviewed identity precision.

Reproduce with `journal/tools/admission/rehearse_source_admission.py`, supplying a new
output directory, read-only source checkpoint, exact corpus digest, explicit
reviewer/evidence, and the nine approved page kinds. Then run
`journal/tools/admission/compare_admission_replay.py` and
`journal/tools/admission/assess_unsupported_scoring.py` against the completed clone. Both
comparison tools open their inputs read-only. The replay tool requires
external review metadata and never creates an approval from passing tests.

The focused source/admission/project suite passed 59 tests after projector
18, with lint and type checks passing. Root owns the full-suite check, V4
deployment, and the final activation decision. H17 human adjudication and its
held-out evaluation limitation remain pending and separate from this review.
