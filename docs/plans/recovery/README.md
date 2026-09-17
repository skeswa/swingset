# Self-healing rollout

Status: accepted, 2026-09-12. Work plan for [self-healing](../../reference/recovery.md).
The order in which these revisions interleave with the backfill is owned by
[implementation plan v2](../history-and-recovery.md). Acceptance is not
completed work or new authorization to publish. Each revision is done when it
has updated the contracts it names, its tests are green, and every scenario
listed for it in [acceptance scenarios](#acceptance-scenarios) passes. On
acceptance the revisions slot into the [milestones](../milestones.md) and the
[implementation plan](../../../journal/archive/v1-implementation-plan.md), whose sizes they use: S is a
session, M is two or three, L is a weekend or more.

Revisions are numbered H1 to H18 in the order they are expected to land. The H
prefix means healing and keeps them distinct from the M milestones and the
plan's work packages. A revision may start when every revision it depends on
is done; revisions with no shared dependency can proceed in parallel.

## First tranche: admission and correction

The first tranche establishes trustworthy inputs and lasting corrections
before increasing automatic repair.

| Revision                      | Bounded change                                                                                                        | Contracts changed                          | Depends on | Size |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ | ---------- | ---- |
| H1. Verification records      | Add usable registry verification state and migrate recoverable check history.                                         | state, fetching                            | none       | S    |
| H2. Registry consumers        | Switch probe completion and stale-profile selection to H1; keep delayed-ID reconciliation after the intensive window. | scheduling, sources                        | H1         | S    |
| H3. Judge score ceiling       | Correct the judge scoring ceiling with archived counterexamples.                                                      | identity linking                           | none       | S    |
| H4. Unrestricted divisions    | Stop treating an unrestricted division as a low skill level in division consistency.                                  | identity linking, parsing                  | none       | S    |
| H5. Paired names              | Split or abstain on paired-name entries instead of producing one mixed-person link.                                   | identity linking, parsing                  | none       | S    |
| H6. Contracts in shadow       | Add adapter accounting and staged guard reports for registry, scoring indexes, and round sheets; report only.         | parsing, architecture                      | none       | M    |
| H7. Enforced admission        | Select staged generations atomically; enforce critical guards and scoped removal authority.                           | state, parsing, architecture               | H6         | M    |
| H8. Decision journal          | Extend `identity_overrides.csv` to the journal format, make it append-only, and convert existing rows once.           | identity linking, state, repository layout | none       | M    |
| H9. Decision-aware resolution | Apply the journal to every identity path and invalidate affected historical links.                                    | identity linking, state                    | H8         | M    |
| H10. Early publication safety | Drop `probable` from default joins, pin correction inputs, and support correction-only releases.                      | build, publishing, data model              | H9         | M    |

H6 runs in shadow: it reports guard outcomes with stable reason codes and adds
no deletion authority or fetch load. H7 needs generation checks only for its
admission transaction; it does not wait for H15. H9 uses conservative full
relinking for invalidation. H10 uses the conservative publication barrier plus
the restricted correction-only build; do not wait for H11 to remove a confirmed
bad published join. H3 to H5 change scores only. Their changes authorize no new
default joins: expansions pass H9, H10, and H17.

## Second tranche: continuous repair and measured accuracy

| Revision                          | Bounded change                                                                                                                          | Contracts changed               | Depends on                                  | Size |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- | ------------------------------------------- | ---- |
| H11. Requirement inventory        | Turn `findings` into the inventory, add the periodic historical scan, cohorts, and the doctor and summary sections; execute no repairs. | state, architecture, operations | none                                        | M    |
| H12. Work isolation               | Add durable attempt outcomes, retry eligibility, and per-scope failure isolation.                                                       | state, operations               | H11                                         | S    |
| H13. Pause and status             | Add the `kind` pause scope, unit-level gating, bounded control servicing, checkpoint recovery, and effective control status.            | operations, state               | H10, H12                                    | M    |
| H14. Fair scheduling              | Allocate host and cycle budgets, bound backlog, and reserve repair capacity; event-completion follow-up below.                          | scheduling, fetching            | H13                                         | M    |
| H15. Derivation fingerprints      | Capture recipes and desired and materialized fingerprints across projection, linking, and build; derive their work from them.           | state, build                    | H11                                         | M    |
| H16. Release closure and coverage | Validate selected dependency generations and publish coverage and freshness; source-event completion follow-up below.                   | build, publishing, data model   | H10, H15                                    | M    |
| H17. Reviewed accuracy evaluation | Establish representative accepted-link and unresolved-entry samples beside the regression corpus.                                       | identity linking                | H8                                          | M    |
| H18. Kind-by-kind activation      | Enable repairs one requirement kind at a time with independent convergence checks and restart and restore drills.                       | operations, scheduling          | H7, H10, H11 to H16, H17 for identity kinds | L    |

H11 and H17 can begin during the first tranche. H11 runs in shadow: it proposes
work and executes none. H18 enables one kind at a time; each enabled kind must
pass its scenarios under retries, crashes, and reordered work. A new source
kind requires its own contract and fixtures even after other kinds are active.
Historical discovery extends through the separate [backfill plan](../../reference/backfill.md).

## Event completion extension

Accepted addition to H14 and H16, recorded 2026-09-14. Enumeration, event turns,
expansion controls, artifact diagnostics, blocker history, and bounded progress
observations are implemented and tested locally. Bounded fleet accounting,
immediate-edge retirement proofs, and verified unavailable-page accounting also
pass local tests. Bounded ordinary-acquisition timing, evidence-backed unsupported
pages, and recorded event retirement/history are included in the reviewed
schema-28 runtime deployed under hold on 2026-09-17. Calibration, operating
acceptance and subsequent coverage publication remain pending. The later
historical-timing schema-29 increment is separately tested locally. See [current status](../../status.md).
Existing completion receipts for the original
H14 scope remain valid; class fairness alone does not satisfy these additional
scenarios. This work stays in V6, with no renumbering of H1 to H18.

[Scheduling](../../reference/scheduling.md#event-completion) owns event selection and completion
semantics. [Local state](../../reference/state.md#event-completion-persistence-h14-extension)
owns durable enumerations and turns; [operations](../../reference/operations.md#event-completion-reporting-h14-extension)
owns diagnostics; [coverage](../../reference/data-model.md#event-completion-coverage) owns the
release representation. The scheduler retains its existing interface and fetch
gate. Add no second fetching loop, canonical matching map, or completion flag
that can override retained evidence.

Implement and accept this extension in order:

1. After the existing H14 scheduler and H11–H13 inventory, isolation, and controls,
   recover enumerations from admitted parent evidence. Add local stage counts
   and bounded event rotation through the existing next-watch interface. Prove
   source references work independently of canonical matching and that waiting
   events receive service under continuous discovery.
2. Protect listed-page acquisition within new work, bound discretionary index
   expansion, and add durable selection reasons, blocker transitions, and event
   service and no-progress reporting. Calibrate the captured policy values in
   shadow against retained demand before operating acceptance.
3. With H15 generation support and H16 release closure, add the source-event
   coverage rows to a subsequent release. Verify the difference between local
   completion and acknowledged publication, including unsupported pages and
   unresolved maps. A frozen earlier candidate is not retroactively extended.
4. Pass the extension scenarios below and measure service for a fixed admitted
   cohort under ordinary host limits before V6 closes. H18's V7 activation and
   restore checks then include unfinished event enumerations and turn state.

Additional effort is not included in the original H14/H16 size estimates.
Estimate implementation and operating observation separately after the first
retained-inventory rehearsal; request budgets and operator holds are not changed
by accepting this design. A bounded fake-clock result alone is not evidence of
production throughput or a backfill completion date.

## Migration, enforcement, and recovery

1. Capture a restorable SQLite backup, archive inventory, override inputs, and
   pinned release manifest. Add new records without deleting current evidence.
2. Recover historical verification only where a recorded successful check can
   be tied to intact content and an accepted interpretation. Unknown history
   stays unknown; migration time is not a successful check time.
3. Bootstrap source generations by replaying archived inputs under the new
   contracts. Label existing selected output as legacy and unassessed until
   checked; migration grants neither admission nor removal authority. Report
   every rejected legacy scope and whether retaining its old facts remains
   permissible.
4. Convert the override file to the journal format and validate source
   references. Keep unmapped rows visible and blocked; never discard
   inconvenient overrides to make the conversion pass.
5. Compare shadow and existing output per source kind: additions, removals,
   newly withheld claims, failed guards, and changes in dependency scope.
   Review every new blocking failure and unexplained difference in the
   activation sample. Fixtures include empty, malformed, changed, and
   historically unusual units.
6. Enable enforcement per source kind using a captured policy revision.
   Critical guard failures block promotion; they cannot be converted to
   warnings to drain the queue. Continue unrelated accepted scopes within
   existing budgets.
7. Publish the safe schema and correction behavior with an attributable
   release note. Enable repair kinds only after their fixtures, budget
   measurements, and recovery checks pass.

On a bad rollout, pause new admissions or automatic repairs and retain the last
admissible generations. Preserve the journal and revocations. Rollback must not
restore known wrong joins, disable suppression, lower guards, or label
unassessed legacy output as accepted. Restore the captured evidence and replay
with a compatible runtime; expose unsupported scopes if recovery remains
blocked. Capture operator controls and progress history in backups. Before
enabling a repair kind, demonstrate its pause, resume, and doctor output and
record control latency and drain-time limits. A restored operator pause is
visible before any worker can start. During a pause, safety and doctor checks
stay active and report publication held for a pending correction.

## Acceptance scenarios

Test module interfaces with archived fixtures, a fake clock, and fake source
adapters. These tests make no live requests. Early revisions exercise their
own module interfaces and do not require later consumers or the full repair
loop; where a scenario says a behavior is outside its gate, a later revision
owns it. H18 reruns every prior scenario applicable to the kind being enabled.

| Revision      | Scenario                                                                                                                          | Required result                                                                                                                                                                                                                                                                                                                                      |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H1            | Identical found and not-found responses, valid 304s, and failed content checks are recorded by the evidence module                | Usable freshness advances only for successful checks with an accepted interpretation, without changing claim provenance; errors do not renew it. Probe and refresh consumers are outside this gate.                                                                                                                                                  |
| H1            | Verification encounters a missing or corrupt cached artifact                                                                      | No false freshness renewal; a stable failure reason and the required digest for recovery are retained. Automatic recovery is outside this gate.                                                                                                                                                                                                      |
| H2            | Probe completion and stale-profile selection consume usable verification records                                                  | Identical successful responses finish finite probes and rotate refreshed profiles; failed attempts neither satisfy a probe nor strand it on the annual interval.                                                                                                                                                                                     |
| H2            | A first-point dancer receives an ID after day 30                                                                                  | The old unresolved result is reconsidered without relying on an existing candidate-index edge.                                                                                                                                                                                                                                                       |
| H3            | The audit's judge counterexamples are rescored                                                                                    | Known false judge links score below acceptance and known true ones keep their evidence; no default join is populated by the score change alone.                                                                                                                                                                                                      |
| H4            | An entry in an unrestricted division is scored against dancers of every level                                                     | Division consistency is unavailable, not a mismatch; the audit's misinterpreted entries no longer lose their correct candidate.                                                                                                                                                                                                                      |
| H5            | A paired-name entry from the audit is parsed and linked                                                                           | The entry yields two subjects or an explicit abstention; no single mixed-person link is produced and the counterexample fixture is retained.                                                                                                                                                                                                         |
| H6            | Shadow evaluation of pagination that is missing, repeated, or changed during assembly                                             | Incomplete coverage and a stable reason code are reported; the input manifest and proposed action are retained. Current observations, admission pointers, and source request counts are unchanged.                                                                                                                                                   |
| H6            | Shadow evaluation finds a critical unknown field or a changed manual-extraction sentinel                                          | A stable actionable review record appears; no automatic acknowledgment or threshold relaxation. Current admitted output is unchanged.                                                                                                                                                                                                                |
| H7            | Admission attempts the incomplete pagination units from the H6 scenario                                                           | Promotion and removal authority are refused; the accepted pointer and observations are preserved; the failed candidate and its action are retained without retiring prior rows.                                                                                                                                                                      |
| H7            | An accepted authoritative enumeration removes a source row                                                                        | Only the source-owned claims in scope are retired; history and independently supported facts remain.                                                                                                                                                                                                                                                 |
| H7            | Crash before or after admission                                                                                                   | Restart selects the complete old or new generation with its observation projection and downstream invalidations; half a generation is never exposed.                                                                                                                                                                                                 |
| H7            | Desired source inputs change while an admission attempt is running                                                                | The obsolete admission cannot replace the current pointer or observations, or clear work for newer inputs. Publication behavior is outside this gate.                                                                                                                                                                                                |
| H7            | Admission evaluates a complete-looking unit with a critical unknown, missing required date, or unacknowledged extraction sentinel | Each blocking guard failure prevents promotion: the accepted pointer and current observations stay unchanged, nothing is retired or derived from the failed candidate, and its manifest and failure remain available. A passing control unit is admitted; admitting the failed unit with a warning does not pass.                                    |
| H8            | The legacy override rows are converted, including unmapped and ambiguous rows                                                     | Every row becomes a decision or an `insufficient_evidence` decision naming its unmapped entry; a repeated conversion changes nothing; a bundle that removes or alters a `decision_id` is rejected at input acceptance.                                                                                                                               |
| H8            | Crash around journal acceptance                                                                                                   | An unaccepted journal digest remains unselected and retryable; acceptance commits the digest and downstream invalidations together, without losing or duplicating decisions.                                                                                                                                                                         |
| H8            | Restore the journal, accepted digest, and reference migrations                                                                    | Accepted decisions and their provenance survive exactly; unaccepted digests stay unaccepted and repeated recovery is idempotent. Full repair execution is outside this gate.                                                                                                                                                                         |
| H9            | A pair is rejected, then the source event is remapped and the linker replayed                                                     | The decision follows the source reference; ambiguous migrations withhold instead of resurrecting the pair. Changed scoring and direct-ID paths cannot bypass the rejection.                                                                                                                                                                          |
| H9            | New evidence conflicts with a positive or negative decision                                                                       | Review opens; supersession is explicit; conflicting decisions leave the ID null; old decision history remains available.                                                                                                                                                                                                                             |
| H9            | A decision revokes an identity while linking is running                                                                           | The obsolete linking cannot restore the revoked link or clear the required relink; recomputation invalidates affected historical joins.                                                                                                                                                                                                              |
| H10           | The journal, suppression inputs, or acceptance policy changes after a release candidate is built                                  | The old candidate cannot publish; rebuild and recheck at the publication boundary. No newly revoked or suppressed join is restored.                                                                                                                                                                                                                  |
| H10           | A wrong published identity plus an unrelated deterministic parse failure                                                          | A coherent correction-only release removes the identity and unsupported dependents. The parse failure remains visible; work isolation is tested under H12.                                                                                                                                                                                           |
| H10           | A `probable` link exists with no accepted decision                                                                                | Its default `wsdc_id` column is null in the built tables and the candidate remains in `link_candidates` with every signal.                                                                                                                                                                                                                           |
| H11           | A stored acquisition requirement row is deleted                                                                                   | The next scan recreates it from evidence and policy. Derivation work needs no such test: it is a fingerprint comparison, and deleting a materialized fingerprint makes the scope pending.                                                                                                                                                            |
| H11           | Doctor is queried while work is active, idle, blocked, or the worker is stopped                                                   | Human and JSON output agree with a consistent local inventory, identify stale scans, and remain available without network or the writer lock. Watch mode refreshes within its interval.                                                                                                                                                              |
| H11           | Snapshot a cohort, then retry, satisfy, reopen, retire, and discover requirements, and restart reporting                          | Counts balance from durable transitions; retries never count as repairs; cohort membership stays fixed; reopenings reduce completion; new work appears separately. Unknown universes and paused or blocked cohorts receive no percentage or ETA.                                                                                                     |
| H12           | Work encounters a missing or corrupt artifact, with and without a valid backup                                                    | Restore by verified digest when possible; otherwise retain an `unavailable` requirement and isolate the failed work. Current replacement bytes cannot impersonate historical evidence.                                                                                                                                                               |
| H12           | A permanently failing parse runs beside a healthy fetch and an independent healthy derivation                                     | Within a declared finite number of fake-clock cycles, the healthy fetch archives its response and the healthy derivation commits output. The failed parse retains its evidence, attempt outcome, and requirement; identical bytes and recipe do not trigger an immediate retry loop. Neither healthy path uses the correction-only publication path. |
| H13           | Request `pause --all` during an active bounded unit and a long backlog                                                            | The request is persisted within the configured control-service bound. No matching action starts after that boundary; the active unit drains atomically. Status moves from pausing to paused only after attempts settle; timeout reports persisted state and remaining attempts.                                                                      |
| H13           | Combine `--all`, `--source`, `--kind`, and an automatic host pause, then selectively resume or expire one                         | Only matching operator pauses clear. Other applicable pauses remain effective, unrelated eligible work progresses, shared unsplittable work reports its paused dependency, and every automatic entry point obeys the gate.                                                                                                                           |
| H13           | Pause with a pending correction or an in-flight publication                                                                       | No repair publication starts while paused; an unsafe ordinary publication is held with a visible reason. A started publication settles through receipt reconciliation before `paused`. Suppression and acceptance checks remain active.                                                                                                              |
| H13           | Restart or deploy while an indefinite pause and a partial unit checkpoint exist                                                   | The pause is effective before work admission; checkpoints, attempts, and retry deadlines survive. Resume is idempotent and retains completed work; the partial unit restarts from its committed boundary.                                                                                                                                            |
| H13           | Observe an intentionally paused scope, a stuck drain, and a stalled unpaused scope                                                | Doctor shows reasons, pause IDs, expiry, exact resume selectors, and next actions. Eligible-work alarms distinguish intentional pause from stalled work; evidence age, pending corrections, and stuck-drain warnings remain visible.                                                                                                                 |
| H14           | Sustained current-event demand                                                                                                    | Old eligible work still progresses; service gaps and backlog stay within configured objectives.                                                                                                                                                                                                                                                      |
| H14           | Resume after a long pause with depleted host budgets and a large overdue backlog                                                  | Existing usage, cooldowns, and retry eligibility are honored; work resumes with ordinary fairness and no catch-up burst or extra budget.                                                                                                                                                                                                             |
| H14 extension | Monterey's 33 pages compete with continuously arriving events and live refreshes                                                  | With finite fixture capacity, Monterey receives bounded turns and all retrievable pages finish within a declared finite bound. Current work retains its share; a large event and an older waiting event cannot starve.                                                                                                                               |
| H14 extension | A page fails permanently while other pages and events remain eligible                                                             | The page retains its outcome and next action; independent pages progress. Failed attempts consume service but never count as successful progress or suppress a no-progress warning.                                                                                                                                                                  |
| H14 extension | Restart mid-turn, reset the daily budget, and resume after an operator hold                                                       | Turn position, issued usage, pending membership, retry eligibility, and successful progress survive. Issued requests are not refunded. Holds and host limits are rechecked per request; no catch-up burst occurs.                                                                                                                                    |
| H14 extension | An alias changes, a parent is archived, and queue hints are deleted                                                               | Grouping and discovery age remain tied to the source reference. Admitted enumeration evidence recreates unfinished children without fetching the parent, resetting retries, or inventing completion.                                                                                                                                                 |
| H14 extension | An index adds pages, omits pages without removal authority, or has incomplete pagination                                          | The pinned prior denominator remains inspectable. Valid additions create a new enumeration and preserve existing wait age; unauthorized omissions do not erase work. Unknown total coverage stays unknown.                                                                                                                                           |
| H14 extension | Listed-page demand exceeds the expansion watermark; then demand drains                                                            | Listed pages retain their protected share, discretionary index expansion yields and later resumes, and essential discovery and live checks continue. No watch is deleted to lower the backlog.                                                                                                                                                       |
| H14 extension | Redirects, robots checks, and shared pages consume a turn                                                                         | Every issued HTTP request is debited once to its selected event and host. Shared evidence advances all supported enumerations without duplicate charges. Event turns cannot bypass history or host gates.                                                                                                                                            |
| H14 extension | Query an event while budget-exhausted, paused, retrying, and making no successful progress                                        | Human and JSON views agree on counts, enumeration, blockers, selection reason, and next action. Wall age continues; eligible age excludes blocked intervals and remains unknown for unsupported legacy history.                                                                                                                                      |
| H16 extension | All pages are acquired, one is unsupported, the event map is unresolved, and publication is delayed                               | Stage counts and source-event coverage preserve each gap with the pinned enumeration. No canonical map is required to report the source event. Only an acknowledged release advances published progress; page counts do not become round or identity counts.                                                                                         |
| H18 extension | Restore a checkpoint with partial event acquisition and new parent evidence awaiting interpretation                               | Enumeration support, turn position, request usage, blockers, and stage progress survive. Outstanding work resumes under ordinary gates; the final supported output matches an uninterrupted run.                                                                                                                                                     |
| H15           | Crash around derivation completion                                                                                                | Output, materialized fingerprint, and revision bumps commit together or not at all; after restart the scope is pending exactly when the fingerprints differ.                                                                                                                                                                                         |
| H15           | Desired derivation inputs change while a worker is running                                                                        | The old completion cannot overwrite newer output or make the scope current; the scope remains pending under the new desired fingerprint.                                                                                                                                                                                                             |
| H15           | A code change without a manual version bump                                                                                       | Affected output replays; old and new mappings invalidate together.                                                                                                                                                                                                                                                                                   |
| H16           | A release selects a join derived from event or dancer generations different from those in its manifest                            | The inconsistent candidate is rejected or the affected dependency closure is rebuilt before publication.                                                                                                                                                                                                                                             |
| H16           | New source evidence arrives after a coherent release's cutoff while it is building                                                | With selected support still admissible and correction inputs unchanged, the pinned candidate validates and publishes without restarting or importing the newer generation. Repeated harmless arrivals cannot starve publication; the next release considers them.                                                                                    |
| H16           | A release contains incomplete or withheld scopes                                                                                  | Coverage names the missing scopes, reasons, and explicit denominators or an unknown-universe label. Incomplete coverage alone does not invalidate a coherent release.                                                                                                                                                                                |
| H16           | A repair is complete locally but its release has not published                                                                    | Doctor reports it awaiting publication, retains the previous release ID and time, and shows correction age; only a confirmed publication receipt advances published progress.                                                                                                                                                                        |
| H17           | Representative accepted-link and unresolved-entry review samples are drawn and adjudicated                                        | Sampling and adjudication are reproducible; precision estimates state cohort, sample size, and uncertainty; missing candidates are examined.                                                                                                                                                                                                         |
| H18           | Backup restore with outstanding repairs and decisions                                                                             | Decisions and their provenance survive; reconciliation restores outstanding repairs, execution resumes, and final semantic output matches a clean rebuild.                                                                                                                                                                                           |
| H18           | Back up a paused system, restore it, change relevant evidence or policy, and resume                                               | Controls and progress history survive; old checkpoints cannot skip new inputs or restore revoked links. Repair resumes from valid boundaries, explains invalidated work, and converges to a clean rebuild with attributable publication progress.                                                                                                    |

State-machine tests vary ordering, failures, and retries. Compare final
semantic output with a clean rebuild from the same evidence, policy, and
journal; ignore incidental run timestamps. Check publication correction history
and receipts separately. Independent source review is still required: matching
rebuilds can share the same interpretation bug.

## Operational acceptance

Each measure is emitted by the revision that owns it and has a fake-clock
alert test before activation. Service-gap and correction-latency objectives are
set from measured workloads and recorded in configuration.

| Measure                                                                      | Revision |
| ---------------------------------------------------------------------------- | -------- |
| Oldest usable verification age                                               | H1       |
| Staged-generation age and failed guards by contract                          | H6       |
| Pending reference migrations                                                 | H8       |
| Unresolved contradictions                                                    | H9       |
| Correction publication latency                                               | H10      |
| Requirement age, time since progress, and status age                         | H11      |
| Acquisition and derivation lag                                               | H11      |
| Pause duration, control-service latency, and draining attempts               | H13      |
| Reviewed acceptance precision with numerator, denominator, and review method | H17      |

A no-progress alert names the requirement, failed guard or decision, last
remedy, and next permissible action. Review backlog and source unavailability
remain visible even when no automatic remedy exists.
