# Historical year review and fixture readiness, 2026-09-17

The current production export still has 17 pending phase-one captures. Every
year from 2010 through 2026 remains unaccepted. The new
[year packet](../../evidence/collection/phase1-review-2026-09-17/current-review/README.md)
provides the exact events, findings, alias groups and capture references for
each year. It is preparation for resolving findings, not a request to accept
unfinished years.

## Evidence and reconciliation

The coordinator exported production state through the deployed schema-14
runtime with `review_pack(..., reconcile=False)` and a read-only writer lock.
The [export](../../evidence/collection/phase1-review-2026-09-17/year-review/review.json)
is distinct from the older 2026-09-13 export. Both currently record 213 targets:
155 parsed, 33 empty, 17 pending, four findings and four duplicates. All pending
targets are exact retained 2025–2026 calendar captures. They are not score-sheet
requests and do not require the new-source fixture exception.

The [report](../../evidence/collection/phase1-review-2026-09-17/current-review/report.json)
hashes all five input CSVs. Every year's event and finding totals reconcile to
its detailed rows. There are 658 printed-name groups in `series-review.csv`.
The older resume note's 569-group alias proposal refers to another artifact;
it must not substitute for this export or count as accepted aliases.

A capture gap is not necessarily an unfetched body. For 2026 the 56 referenced
gaps are 17 pending captures, four acquired 2016 map bodies with parse failures,
and 35 parsed captures with open warnings. All 35 parsed captures have an
explicit source finding in this export. For 2010 the same breakdown is
17 + 4 + 16. Calendar gaps conservatively apply to every year because retained
inline calendars include older editions; capture year cannot safely narrow the
listing horizon. The current closure contract deliberately preserves this.

The source findings comprise 62 parse warnings and four map failures. Among
them are 18 undated/hiatus notices, eight prose approval notices, seven colour
fallback warnings, malformed or reversed dates, and one unparsed sidebar.
The [earlier warning review](../../evidence/collection/phase1-2026-09-13/warning-triage/review.md)
already resolved spacing, asterisk footnotes and column-boundary problems;
those fixes must not be counted as new work. Discontinuous dates and malformed
years remain unresolved. The old assumption that every newsletter has a dated
sidebar was disproved by the eight independently reviewed policy-only issues.
No fresh conclusion establishes the old-site asterisk's meaning, the earlier
month-rule distribution, absent newsletter volumes, or individual trial colours.

## Executable preparation

[reconcile_retained_phase1.py](../../tools/collection/reconcile_retained_phase1.py)
generates a new packet offline and refuses an existing output directory. Two
focused tests pass, checking detailed counts, retained warnings, pending work
and input preservation. Ruff passes for this helper, its tests and the gate
constructor. These checks do not validate an acquisition or publication.

```sh
.venv/bin/python journal/tools/collection/reconcile_retained_phase1.py \
  --input journal/evidence/collection/phase1-review-2026-09-17/year-review \
  --output NEW_REVIEW_DIRECTORY
```

[prepare_phase1_resume_gate.py](../../tools/collection/prepare_phase1_resume_gate.py)
prepares a gate from actual locked, read-only production state. It verifies the
schema-14 source inventory, acknowledged baseline, hold, restore marker,
accepted inputs and exact remaining subset of the original 17 targets. It
rechecks the hold and restore marker after acquiring the lock. The retained
resume driver checks the gate again before execution. Offline assembly checked
partial completion, preservation of input authority and rejection of a changed
baseline. Production gate preparation and intake belong to the coordinator.

## Fixture and human decisions

The owner has now approved the [fixture proposal](v2-new-source-fixture-proposal-2026-09-13.md)
in [D-0053](../../decisions/0053-approve-exact-new-source-fixture-exception.md): five exact archived bodies plus at most a CDX
probe and page zero, 12 requests total, 32 MiB and 15 minutes. It authorizes zero
DCN PDFs, additional index bodies, origin requests or production acquisition.
The [ordering rule](../../../docs/plans/history-and-recovery.md#2-the-ordering-rule)
requires source-kind admission before historical fetching; the
[fixture contract](../../../docs/reference/parsing.md#fixtures-and-tests)
requires complete real controls. General continuation authority does not supply
this exception.

The current packaged runner is
[fixture-exception-h13-002.py](../../evidence/admission/fixture-exception-2026-09-16/fixture-exception-h13-002.py),
not the historical `python -m research.fixture_exception` invocation.
[Fresh offline verification](../../evidence/collection/phase1-review-2026-09-17/offline-verification.json)
checked all 638 frozen runtime files and nine helper-closure files, reran all
63 packaged tests in 2.78 seconds, and exercised the dry run with zero requests.
The dry run uses the helper closure as `--repo` to resolve retained evidence.
Execution must use the exact `/nix/store/z689qy41inndill3d92ym8im852x3649-source`
as `--repo` and add `--authorization`, `--execution-gate`,
`--execution-gate-sha256` and `--execute`, under the frozen runtime's `PYTHONPATH`.
The wrapper verifies its sibling closure separately. A gate and authorization
must contain actual current paths, publication receipt and execution window;
none was manufactured here.

The owner resolved WP14 to 2010–2016 in [D-0054](../../decisions/0054-keep-step-right-history-at-the-2010-floor.md);
the criterion is corrected and the history floor is unchanged.
The [H17 packet](h17-review-packet-2026-09-13.md) is available at
`/tmp/swingset-v2-phase1-check/h17-review/index.html`. Its 275-subject sample
and all 848 evidence artifacts verify again. Extract artifacts are hashed after
canonical JSON reconstruction because the review packet pretty-prints them.
There are still no supplied human adjudications; the owner deferred their
collection to a future review website in [D-0055](../../decisions/0055-defer-human-adjudication-to-a-review-website.md). The single tuning component
cannot support held-out, public or judge precision. An independent human must
review the sample, including investigation beyond unresolved candidate lists.

No year acceptance, source acquisition, production mutation, policy activation,
deployment or publication was performed by this offline work.

## Subsequent map-body inspection

The four acquired map failures are now explained by complete retained controls:
all popups contain names and websites without printed dates. The
[2026-09-17 inspection](calendar-map-gaps-2026-09-17.md) accounts for this source
limitation without inventing event dates or clearing production findings. These
remain year-review gaps; no extra acquisition or parser change occurred.
