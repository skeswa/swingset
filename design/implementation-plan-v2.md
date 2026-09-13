# Implementation plan for v2: history and self-healing

Status: accepted, 2026-09-12. Owner: Sandile Keswa. This is the plan we
follow after v1.

v2 is two streams of work that were designed separately: the backfill to
2010-01-01 ([backfill](backfill.md), packages WP11 to WP16) and the
self-healing revisions ([rollout](self-healing-rollout.md), H1 to H18).
This plan sequences them into one order. It owns only that order, the
gates between the streams, and what done means for each stage. Scope
stays where it is: the rollout owns each revision's change and
scenarios, backfill owns each package's scope and done criteria, and the
contracts in the [design index](README.md) own behavior. When this plan
and a scope document disagree about scope, the scope document wins.
When they disagree about order, this plan wins.

Facts about the running system were checked on 2026-09-11 and are in
[implementation status](../docs/implementation-status.md). Facts we
could not check are marked **unverified**.

Local implementation and the G1 fallback applied on 2026-09-13 UTC are
tracked in [v2 progress](../docs/v2-progress.md). V1 closed with the reviewed correction release on 2026-09-13 UTC. V3 closed with the correction-only publication on 2026-09-13 UTC. V2
and V4 closed together at public commit
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`. V5 infrastructure and V6 now proceed;
year acceptance and later activation retain their original gates.

## 1. Starting point

v1 is complete and live: WP0 to WP10 are implemented, the OrbStack
writer publishes on a 15-minute cycle, and the public dataset holds 573
events, 96 of them with results. Three facts about that state decide the
order below.

| Fact                                                                                                                                     | Consequence                                                                                            |
| ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| The registry sweep is partial, at cursor 3,297 of 27,039, and the post-sweep probe is buggy                                              | H1 and H2 land before the sweep completes so the first post-sweep probe runs on the fixed path         |
| Three identity defects are confirmed by the audit: judge ceiling, unrestricted divisions, paired names; published judge IDs are all null | H3 to H5 land before any historical event is linked, or the errors are copied across sixteen years     |
| A published release cannot be corrected in place; each correction is a new release                                                       | No historical year publishes before the identity schema (H10) and enforced admission (H7) are in force |

## 2. The ordering rule

**History enters through the new contracts, once.** Historical score
sheets are not fetched until admission is enforced for their page kinds
and the safe identity schema is published. This costs about three
weekends of delay before the archive fetch starts. The alternative, a
mechanism to fetch now and hold parsing or publication until later, is
new machinery that exists only to save those weekends, and every held
year would still need a review pass before release. We take the delay.

Fetching phase 1, the event list, is not held. It is about 130 archive
reads and 28 PDFs, it has no identity joins, and everything downstream
needs it.

## 3. Stages

Sizes use the implementation plan's scale: S is a session, M is two or
three, L is a weekend or more. Sizes for the backfill packages are
estimates made here; the rollout's sizes are the rollout's. Update the
Status column in the same change that finishes a stage.

| Stage | Work                                   | Depends on                             | Done when                                                                                                                                                                                      | Size                                                  | Status      |
| ----- | -------------------------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ----------- |
| V1    | H1, H2, H3, H4, H5                     | none                                   | Each revision's scenarios pass; G1 passes, including its fallback if needed; a correction release removes the audit's known wrong dancer joins and preserves withheld judge IDs                | 5 S                                                   | complete    |
| V2    | WP11, then WP12 and WP13 together      | none                                   | Every registry occurrence since 2010 has one `events` row; the `coverage` table is published; each year 2010 to 2026 is `events_accepted` or has open findings naming why not                  | 3 M                                                   | complete    |
| V3    | H8, H9, H10                            | V1                                     | The override file is the journal; `probable` links no longer populate default `wsdc_id`; a correction-only release exists and has been exercised once against the live dataset                 | 3 M                                                   | complete    |
| V4    | H6, then H7                            | V1                                     | Shadow reports over the v1 archive and the phase 1 captures are reviewed; admission is enforced for registry lookups, scoring indexes, and round sheets; v1 evidence is bootstrapped as legacy | 2 M                                                   | complete    |
| V5    | WP15, then WP14 and WP16, year by year | V2, V3, V4                             | Each year's sheets are fetched newest first, admitted under H7, linked under H10, and published with archive versus origin counts in `coverage`; gaps are findings, not silent absences        | 2 M, 1 S, plus three to six weeks of archive fetching | in progress |
| V6    | H11, H12, H13, H14, H15, H16, H17      | V3, V4; H11 and H17 may start any time | Each revision's scenarios pass; doctor shows the requirement inventory; the backfill backlog and live weekends share budget under H14 without either starving                                  | 6 M, 1 S                                              | in progress |
| V7    | H18                                    | V5, V6                                 | Each requirement kind is enabled one at a time and passes its scenarios under retries, crashes, and reordered work; the restore drill passes with outstanding repairs                          | L                                                     | not started |

Every stage ends with a published dataset that is strictly better than
the last, as [milestones](milestones.md) requires. V1 publishes
the reviewed identity corrections. V2 publishes the event list for every year. V3
publishes the safe identity schema; the schema is still pre-1.0 under
[publishing](publishing.md#commit-strategy), so this needs a card note
and no tag. V5 publishes one year at a time. M6 is done when V5 is done;
the schema is declared 1.0 then.

## 4. What runs in parallel

- The five V1 revisions are independent of each other and of V2. Start
  V2 as soon as one session is free.
- V3 and V4 are independent of each other and both follow V1. Run them
  side by side.
- Within V5, fetching runs in the background at backfill priority while
  V6 proceeds. Parsers for WP14 to WP16 may be written before V5 opens;
  their watches may not be created.
- H11 and H17 have no dependency inside V6 and may be started whenever
  a session is free, including during V2.

Nothing else overlaps. In particular V5 does not start for any year
before V3 and V4 are both done, and V7 does not start until V5 has
published every year.

## 5. Gates

| Gate | Condition                                                                                                               | Checked by                                                     |
| ---- | ----------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| G1   | H1 and H2 are deployed to the writer before the registry sweep completes                                                | doctor's sweep cursor against the 27,039-ID dump               |
| G2   | A year's phase 2 watches are created only when that year is `events_accepted`                                           | the `coverage` table, per [backfill](backfill.md#events-first) |
| G3   | No phase 2 watch is created for any year until H7 and H10 are deployed                                                  | this plan's Status column                                      |
| G4   | A year publishes only after its admission failures are reviewed and every guard failure is a finding or a fixed parser  | the year's findings are empty or explained in the card         |
| G5   | A requirement kind is activated under H18 only after its pause, resume, and doctor output are demonstrated and recorded | the rollout's migration steps 6 and 7                          |

If the sweep completes before G1 is met, the post-sweep probe must not
be trusted: pause the registry source, land H1 and H2, then run one
probe cycle and confirm through doctor that the cursor advanced on an
identical not-found response before resuming.

## 6. Decisions this plan makes

On 2026-09-13 UTC the owner approved the
[H3 retained-evidence packet](../research/judge-acceptance-2026-09-13.md).
The original V1 criterion named false published judge IDs, but both the audit
and the checked public baseline contain none. The corrected criterion uses
the audit's two wrong paired-name dancer joins and the approved judge
candidate comparisons. It requires no new judge IDs: some judges have no
WSDC number. This corrects the factual premise without removing the review
or correction-publication gate.

These are choices between the two streams that neither scope document
could make alone. Each names the contract that records it.

| Decision                                                                                                                             | Recorded in                                                           |
| ------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| The `coverage` table is defined once in WP12 with the columns H16 needs, so H16 extends it and never replaces it                     | [data model](data-model.md), [build](build.md)                        |
| Unresolved series names, unmapped sheets, and admission failures are findings, which H11 turns into requirements without migration   | [architecture](architecture.md#findings-and-review)                   |
| H6's shadow corpus is the v1 archive plus the phase 1 captures; history is not fetched to serve as fixtures                          | [rollout](self-healing-rollout.md)                                    |
| History is admitted under H7 from its first fetch; the rollout's legacy bootstrap (migration step 3) applies to v1 evidence only     | [rollout](self-healing-rollout.md#migration-enforcement-and-recovery) |
| The override file converts to the journal format in V3, while it is small; no historical override rows are written in the old format | [identity linking](identity-linking.md)                               |

## 7. How each change lands

- One jj change per revision or package, described with its H or WP
  number in the summary line, its contracts updated in the same change,
  and its scenarios as tests. Commit and push when asked.
- A stage is finished by one further change that updates this plan's
  Status column and [implementation status](../docs/implementation-status.md)
  with the evidence: scenarios run, deployment time, and the public
  commit of the dataset it published.
- The formatter runs before every commit: `mise run fmt`.
- Nothing in this plan authorizes a live request outside the politeness
  rules in [fetching](fetching.md#politeness-rules) or the archive
  etiquette in [backfill](backfill.md#host-settings-and-etiquette).

## 8. Calendar

Weekend counts are estimates from the sizes above at the project's
weekend cadence. They are for planning, not commitments.

| Weekends | Stage                                 | Result                                                                  |
| -------- | ------------------------------------- | ----------------------------------------------------------------------- |
| 1 to 2   | V1                                    | Registry probe fixed; judge, division, and paired-name errors corrected |
| 3 to 5   | V2, with V3 and V4 starting           | Event list for 2010 to 2026 published with coverage                     |
| 5 to 8   | V3 and V4 side by side                | Safe identity schema published; admission enforced                      |
| 8 to 14  | V5 fetching in background; V6 landing | History published year by year; requirement inventory and controls live |
| 14 to 16 | V7                                    | Repair kinds active; M6 done; schema 1.0                                |

## 9. Risks and what changes the order

| Risk                                                                                                            | Response                                                                                                   |
| --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| The sweep finishes before V1                                                                                    | Section 5, G1 fallback                                                                                     |
| Archive rate limits are tighter than the 200 to 400 requests a day assumed; the `X-RL` header is **unverified** | V5 stretches; nothing else moves, because V6 does not wait for V5                                          |
| The EEPro operator conversation (issue #21) has no answer when V5 reaches WP16                                  | Skip WP16 for EEPro; the year publishes with its EEPro gaps listed in the card, as backfill already allows |
| H6 shadow reports show a v1 page kind fails its contract broadly                                                | Fix the parser under V4 before H7; V5 waits, because that kind would fail on history too                   |
| H10 reduces linked coverage more than expected                                                                  | Expected and accepted; the reduction is the point. Do not tune thresholds to restore it; H17 measures it   |
| A V6 revision is needed sooner, such as H14 because live weekends starve the backfill                           | Pull that revision forward; its dependencies in the rollout table still hold                               |
