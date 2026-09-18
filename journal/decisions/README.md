# Project decisions

A decision record states a choice and the reason for it. The file itself is
the acceptance: there is no status and no separate approval step. The agent
makes and records most decisions on its own, including routine implementation
choices. It asks the owner first, and records the answer in `Decided by`, only
when a choice is high impact or controversial:

- it deletes, rewrites, or moves retained evidence, published data, or
  production state, or changes what is kept and for how long;
- it deploys, publishes, spends money, or touches a third party or a live
  source beyond the documented fetching rules;
- it changes a published contract, the data model, an identity rule, or a
  promise made in `docs/reference/`;
- it reverses or narrows an earlier owner-decided record; or
- reasonable engineers would disagree and the cost of being wrong is not
  cheaply undone.

A changed choice gets a new record that links the old one. Start with the
summary in each record; evidence and implementation detail are linked below it.

## Decision index

| ID                                                                                    | Choice                                                                                              | Topic                                             |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| [D-0001](0001-documentation-and-decisions.md)                                         | Separate current docs, project history, and formal decisions (partly replaced by D-0005 and D-0006) | Documentation                                     |
| [D-0002](0002-confirmed-public-identities.md)                                         | Publish person IDs only for confirmed matches (acceptance not yet verified)                         | Identity                                          |
| [D-0003](0003-preserve-source-evidence.md)                                            | Keep source evidence separate from derived records (acceptance not yet verified)                    | Architecture                                      |
| [D-0004](0004-postgres-migration.md)                                                  | Port to PostgreSQL before moving the worker to Dokploy (acceptance not yet verified)                | Hosting                                           |
| [D-0005](0005-research-under-journal.md)                                              | Keep research tools and evidence inside the journal (replaces D-0001 research placement)            | Research                                          |
| [D-0006](0006-record-every-decision.md)                                               | Record every decision in the decision log (expands D-0001 decision scope)                           | Documentation                                     |
| [D-0007](0007-bounded-closure-proof-reuse.md)                                         | Reuse closure proof only after rechecking its exact evidence                                        | Build completion                                  |
| [D-0008](0008-local-validation-checks.md)                                             | Make focused validation checks run independently                                                    | Development checks                                |
| [D-0009](0009-retained-source-event-enumerations.md)                                  | Retain source-event page obligations from admitted parents                                          | Event completion                                  |
| [D-0010](0010-bounded-event-turns.md)                                                 | Rotate source events after a bounded request turn                                                   | Scheduling                                        |
| [D-0011](0011-explicit-event-evidence-drilldown.md)                                   | Verify event artifacts in an explicit doctor drill-down                                             | Event reporting                                   |
| [D-0012](0012-protect-listed-page-capacity.md)                                        | Reserve new-work capacity for listed result pages                                                   | Scheduling                                        |
| [D-0013](0013-preserve-historical-migration-tests.md)                                 | Keep historical migration tests pinned to their reviewed schema                                     | Migration validation                              |
| [D-0014](0014-explain-recorded-event-service.md)                                      | Explain event service from issued-request receipts                                                  | Event reporting                                   |
| [D-0015](0015-bound-event-expansion-observations.md)                                  | Bound verification before gating event-index expansion                                              | Scheduling                                        |
| [D-0016](0016-stream-event-body-verification.md)                                      | Stream body verification in event diagnostics                                                       | Event evidence                                    |
| [D-0017](0017-report-current-event-blockers.md)                                       | Report current blockers without inferring eligibility                                               | Event reporting                                   |
| [D-0018](0018-gate-expansion-with-conservative-observations.md)                       | Gate new event indexes with conservative local observations                                         | Scheduling                                        |
| [D-0019](0019-retain-observed-event-blocker-changes.md)                               | Retain observed changes in event blockers                                                           | Event reporting                                   |
| [D-0020](0020-pin-event-enumerations-in-release-evidence.md)                          | Pin event enumerations in release evidence                                                          | Release coverage                                  |
| [D-0021](0021-verify-page-evidence-at-a-release-cutoff.md)                            | Verify page evidence at a release cutoff                                                            | Release coverage                                  |
| [D-0022](0022-preserve-supported-event-bases.md)                                      | Preserve supported event bases                                                                      | Event preservation                                |
| [D-0023](0023-include-build-tests-in-default-discovery.md)                            | Include build tests in default discovery                                                            | Test discovery                                    |
| [D-0024](0024-compare-restored-event-work-with-an-uninterrupted-run.md)               | Compare restored event work with an uninterrupted run                                               | Restore validation                                |
| [D-0025](0025-bind-local-coverage-to-artifact-evidence.md)                            | Bind local release coverage to artifact evidence                                                    | Release coverage                                  |
| [D-0026](0026-record-stage-outputs-and-verified-event-progress.md)                    | Record stage outputs and verified event progress separately                                         | Event progress                                    |
| [D-0027](0027-compare-turn-policies-with-all-modeled-pages-ready.md)                  | Compare turn policies with all modeled pages ready                                                  | Scheduling validation                             |
| [D-0028](0028-reconstruct-retained-snapshot-parse-hints.md)                           | Reconstruct retained snapshot parse hints                                                           | Queue recovery                                    |
| [D-0029](0029-resume-h16-production-release-after-validation.md)                      | Resume H16 production release after validation                                                      | Release operations                                |
| [D-0030](0030-retain-live-operation-evidence-outside-workspaces.md)                   | Retain live operation evidence outside workspaces                                                   | Operation evidence                                |
| [D-0031](0031-guard-production-initialization-memory.md)                              | Guard production initialization memory                                                              | Release supervision                               |
| [D-0032](0032-handle-missing-legacy-runtime-recipe-during-initialization.md)          | Handle missing legacy runtime recipe during initialization                                          | Initialization compatibility                      |
| [D-0033](0033-repair-production-control-lock-ownership.md)                            | Repair production control-lock ownership                                                            | Release operations                                |
| [D-0034](0034-record-bounded-event-accounting.md)                                     | Record bounded observations of known event completion                                               | Event accounting                                  |
| [D-0035](0035-prove-immediate-event-page-retirements.md)                              | Prove page retirements on one enumeration edge                                                      | Event retirement evidence                         |
| [D-0036](0036-count-pinned-unavailable-origin-observations.md)                        | Count pinned unavailable-origin observations                                                        | Release coverage                                  |
| [D-0037](0037-account-for-supported-unavailable-page-gaps.md)                         | Account for supported unavailable page gaps                                                         | Event accounting                                  |
| [D-0038](0038-pin-fixture-helpers-beside-frozen-runtime.md)                           | Pin fixture helpers beside the frozen runtime                                                       | Fixture acquisition preparation                   |
| [D-0039](0039-exercise-integrated-event-accounting-offline.md)                        | Exercise integrated event accounting offline                                                        | Event completion validation                       |
| [D-0040](0040-allow-service-read-access-to-verifier-receipts.md)                      | Allow service read access to verifier receipts                                                      | Release operations                                |
| [D-0041](0041-consolidate-agent-workspace-drafts.md)                                  | Consolidate the integrated agent workspace drafts                                                   | Repository maintenance                            |
| [D-0042](0042-separate-link-evidence-resolution-and-persistence.md)                   | Separate link evidence, resolution, and persistence                                                 | Identity linking                                  |
| [D-0043](0043-explain-linking-terms-in-docstrings.md)                                 | Explain linking terms beside the code                                                               | Identity documentation                            |
| [D-0044](0044-observe-bounded-acquisition-waiting.md)                                 | Observe bounded ordinary-acquisition waiting                                                        | Event service timing                              |
| [D-0045](0045-account-for-evidence-backed-unsupported-pages.md)                       | Account for evidence-backed unsupported pages                                                       | Event accounting                                  |
| [D-0046](0046-separate-phase1-acquisition-from-review-gaps.md)                        | Separate phase-one acquisition from review gaps                                                     | Historical review                                 |
| [D-0047](0047-continue-from-published-h16-with-one-operation-owner.md)                | Continue from published H16 with one operation owner                                                | Integration                                       |
| [D-0048](0048-ignore-explicit-newsletter-planning-prose.md)                           | Ignore explicit newsletter planning prose                                                           | Historical parsing                                |
| [D-0049](0049-rehearse-current-schema-against-published-checkpoint.md)                | Rehearse current schema against the published checkpoint                                            | Migration validation                              |
| [D-0050](0050-give-backup-access-to-retained-h16-receipts.md)                         | Give backup access to retained H16 receipts                                                         | Backup operations                                 |
| [D-0051](0051-prove-event-withdrawal-and-page-recorded-history.md)                    | Prove event withdrawal and page recorded history                                                    | Event progress reporting                          |
| [D-0052](0052-package-checkpoint-archives-on-disk.md)                                 | Package checkpoint archives on disk                                                                 | Backup resource use                               |
| [D-0053](0053-approve-exact-new-source-fixture-exception.md)                          | Approve exact new-source fixture exception                                                          | Fixture acquisition                               |
| [D-0054](0054-keep-step-right-history-at-the-2010-floor.md)                           | Keep Step Right history at the 2010 floor                                                           | Historical scope                                  |
| [D-0055](0055-defer-human-adjudication-to-a-review-website.md)                        | Defer human adjudication to a review website                                                        | Identity evaluation                               |
| [D-0056](0056-anchor-host-spacing-to-request-completion.md)                           | Anchor host spacing to request completion                                                           | Fetch politeness                                  |
| [D-0057](0057-parse-step-right-real-controls-conservatively.md)                       | Parse Step Right real controls conservatively                                                       | Source parsing                                    |
| [D-0058](0058-rehearse-the-real-restore-protocol-under-hold.md)                       | Rehearse the real restore protocol under hold                                                       | Recovery operations                               |
| [D-0059](0059-bind-validation-subprocesses-and-preserve-corruption-tests.md)          | Bind validation subprocesses and preserve corruption tests                                          | Validation                                        |
| [D-0060](0060-approve-exact-dcn-index-fixture.md)                                     | Approve exact DCN index fixture                                                                     | Fixture acquisition                               |
| [D-0061](0061-build-a-separate-schema14-dcn-fixture-runner.md)                        | Build a separate schema-14 DCN fixture runner                                                       | Fixture execution                                 |
| [D-0062](0062-bind-legacy-spacing-baselines-to-retained-requests.md)                  | Bind legacy spacing baselines to retained requests                                                  | Request spacing                                   |
| [D-0063](0063-guard-live-extension-migration-with-closed-receipts.md)                 | Guard live extension migration with closed receipts                                                 | Runtime migration                                 |
| [D-0064](0064-present-all-participation-with-source-overlap.md)                       | Present all recorded participation with source overlap                                              | Dataset exploration                               |
| [D-0065](0065-rehearse-frozen-inputs-in-bounded-offline-phases.md)                    | Rehearse frozen inputs in bounded offline phases                                                    | Runtime inputs                                    |
| [D-0066](0066-require-complete-dispatch-fences-for-historical-timing.md)              | Require complete dispatch fences for historical timing                                              | Event timing                                      |
| [D-0067](0067-build-an-exact-schema28-dcn-metadata-lookup.md)                         | Build an exact schema-28 DCN metadata lookup                                                        | Fixture acquisition                               |
| [D-0068](0068-decode-dcn-source-data-without-running-scripts.md)                      | Decode DCN source data without running scripts                                                      | Source interpretation                             |
| [D-0069](0069-approve-exact-dcn-results-metadata-lookup.md)                           | Approve exact DCN results metadata lookup                                                           | Fixture acquisition                               |
| [D-0070](0070-fence-historical-archive-timing-proofs.md)                              | Fence historical Archive timing proofs                                                              | Event timing                                      |
| [D-0071](0071-retain-undated-map-markers-as-review-gaps.md)                           | Retain undated map markers as review gaps                                                           | Historical calendar evidence                      |
| [D-0072](0072-profile-offline-selection-before-changing-replay.md)                    | Profile offline selection before changing replay                                                    | Scheduler performance                             |
| [D-0073](0073-approve-exact-riga-results-html-fixture.md)                             | Approve exact Riga results HTML fixture                                                             | Fixture acquisition                               |
| [D-0074](0074-review-exact-newsletter-colour-rows.md)                                 | Review exact newsletter colour rows                                                                 | Historical backfill                               |
| [D-0075](0075-reuse-dancer-readiness-only-within-owned-read-snapshots.md)             | Reuse dancer readiness within owned read snapshots                                                  | History and recovery                              |
| [D-0076](0076-checkpoint-held-schema28-without-accepting-inputs.md)                   | Checkpoint held schema 28 without accepting inputs                                                  | Recovery operations                               |
| [D-0077](0077-build-a-separate-exact-riga-body-runner.md)                             | Build a separate exact Riga body runner                                                             | Historical backfill                               |
| [D-0078](0078-bind-phase1-resume-to-frozen-extension-inputs.md)                       | Bind phase-one resume to frozen extension inputs                                                    | Historical acquisition operations                 |
| [D-0079](0079-propose-exact-riga-score-pdf-metadata.md)                               | Prepare exact DCN PDF capture metadata review                                                       | History and recovery                              |
| [D-0080](0080-parse-only-owned-legacy-dcn-results-tables.md)                          | Parse only owned legacy DCN results tables                                                          | Offline DCN legacy results interpretation         |
| [D-0081](0081-check-shared-link-prerequisites-before-event-expansion.md)              | Check shared link prerequisites before event expansion                                              | History and recovery                              |
| [D-0082](0082-give-backup-read-access-to-extension-receipts.md)                       | Give backup read access to extension receipts                                                       | History and recovery                              |
| [D-0083](0083-benchmark-jesann-registry-coverage.md)                                  | Benchmark JesAnn registry coverage against individual results                                       | Dataset quality                                   |
| [D-0084](0084-seal-two-riga-score-pdf-metadata-queries.md)                            | Seal two exact Riga score-PDF metadata queries                                                      | Fixture runner implementation                     |
| [D-0085](0085-check-shared-link-readiness-before-candidate-scans.md)                  | Check shared link readiness before candidate scans                                                  | Measured ordinary offline selection cost          |
| [D-0086](0086-seal-schema29-successor-rehearsals.md)                                  | Seal successor rehearsals for the schema 28 checkpoint                                              | Recovery and runtime operations                   |
| [D-0087](0087-authorize-remaining-v2-acquisition-and-operations.md)                   | Authorize remaining v2 acquisition and operations                                                   | V2 implementation authority                       |
| [D-0088](0088-scope-origin-score-pdf-controls-after-empty-archive-lookups.md)         | Scope origin score-PDF controls after empty Archive lookups                                         | DCN preliminary and final PDF fixture acquisition |
| [D-0089](0089-test-fixed-spacing-helper-on-reviewed-schemas.md)                       | Test the fixed spacing helper on its reviewed schemas                                               | Source-bound validation                           |
| [D-0090](0090-bind-an-explicit-quarantine-source-policy.md)                           | Bind an explicit quarantine source policy                                                           | Bounded fixture acquisition                       |
| [D-0091](0091-pause-v2-after-current-validation.md)                                   | Pause v2 after the current validation (Superseded by D-0093)                                        | Implementation pause                              |
| [D-0092](0092-raise-the-jj-snapshot-size-limit-for-retained-evidence.md)              | Raise the jj snapshot size limit for retained evidence                                              | Repository tooling                                |
| [D-0093](0093-resume-v2-from-candidate005.md)                                         | Resume v2 from candidate 005                                                                        | V2 continuation and operational ownership         |
| [D-0094](0094-measure-schema29-cost-on-a-disposable-copy.md)                          | Measure schema-29 cost on a disposable copy                                                         | Operating measurements                            |
| [D-0095](0095-preserve-origin-fixture-request-claims.md)                              | Preserve origin fixture request claims                                                              | Bounded origin fixture accounting                 |
| [D-0096](0096-bind-rehearsals-to-current-held-checkpoint.md)                          | Bind rehearsals to the current held checkpoint                                                      | Schema-29 recovery evidence                       |
| [D-0097](0097-keep-manual-registry-artifacts-on-their-replay-path.md)                 | Keep manual registry artifacts on their replay path                                                 | Input invalidation routing                        |
| [D-0098](0098-separate-held-schema29-migration-from-input-acceptance.md)              | Separate held schema-29 migration from input acceptance                                             | Held production migration                         |
| [D-0099](0099-parse-dcn-score-pdfs-as-scoped-source-observations.md)                  | Parse DCN score PDFs as scoped source observations                                                  | DCN PDF interpretation                            |
| [D-0100](0100-index-numbered-schema-changes.md)                                       | Index numbered schema changes                                                                       | State documentation                               |
| [D-0101](0101-retain-exact-operation-review-receipts.md)                              | Retain exact operation review receipts                                                              | Retained operating evidence                       |
| [D-0102](0102-read-sealed-checkpoints-without-sqlite-sidecars.md)                     | Read sealed checkpoints without SQLite sidecars                                                     | Checkpoint inspection                             |
| [D-0103](0103-measure-retained-replay-attempt-intervals.md)                           | Measure retained replay attempt intervals                                                           | Replay diagnosis                                  |
| [D-0104](0104-maintain-runtime-candidate-history.md)                                  | Maintain runtime candidate history                                                                  | Runtime documentation                             |
| [D-0105](0105-reduce-new-evidence-volume.md)                                          | Reduce new evidence volume ((direction; thresholds are implementation choices))                     | Evidence retention                                |
| [D-0106](0106-archive-large-files-and-scrub-local-history.md)                         | Archive large files and scrub local history                                                         | Repository size and evidence preservation         |
| [D-0107](0107-use-candidate006-for-schema29-rollout.md)                               | Use candidate 006 for the schema-29 rollout                                                         | Runtime rollout candidate                         |
| [D-0108](0108-build-a-separate-step-right-body-runner.md)                             | Build a separate Step Right body runner                                                             | Step Right fixture acquisition                    |
| [D-0109](0109-mask-ordinary-units-through-the-schema29-switch.md)                     | Mask ordinary units through the schema-29 switch                                                    | Held production deployment                        |
| [D-0110](0110-use-runtime-hold-conditions-through-nixos-switch.md)                    | Use runtime hold conditions through the NixOS switch                                                | Held production deployment                        |
| [D-0111](0111-seal-phase-one-replay-before-execution.md)                              | Seal phase-one replay state before execution                                                        | Phase-one reconciliation                          |
| [D-0112](0112-bind-phase-one-replay-to-retained-identities.md)                        | Bind phase-one replay to retained identities                                                        | Phase-one reconciliation                          |
| [D-0113](0113-seal-held-input-acceptance-on-disk.md)                                  | Seal held input acceptance on disk                                                                  | Held production input acceptance                  |
| [D-0114](0114-export-year-review-from-sealed-replay.md)                               | Export year review from the sealed replay                                                           | Historical review                                 |
| [D-0115](0115-bind-phase-one-packet-test-to-fixture-day.md)                           | Bind the phase-one packet test to its fixture day                                                   | Validation                                        |
| [D-0116](0116-refresh-archive-robots-separately.md)                                   | Refresh Archive robots separately                                                                   | Fixture acquisition                               |
| [D-0117](0117-bound-operational-state-with-durable-archives.md)                       | Bound operational state with durable archives (Superseded by D-0119)                                | State retention and recovery cost                 |
| [D-0118](0118-retry-step-right-after-response-decoding-fix.md)                        | Retry Step Right after the response decoding fix                                                    | Step Right fixture acquisition                    |
| [D-0119](0119-bound-state-by-interning-and-one-closure.md)                            | Bound state by interning payloads and one closure ((plan direction only))                           | State retention and recovery cost                 |
| [D-0120](0120-scope-step-right-events-to-the-main-result-panel.md)                    | Scope Step Right events to the main result panel                                                    | Step Right event parsing                          |
| [D-0121](0121-withhold-step-right-preliminary-callback-projection.md)                 | Withhold Step Right preliminary callback projection                                                 | Step Right canonical projection                   |
| [D-0122](0122-version-step-right-admission-without-removal-authority.md)              | Version Step Right admission without removal authority                                              | Step Right source admission                       |
| [D-0123](0123-reconcile-step-right-through-the-common-projector.md)                   | Reconcile Step Right through the common projector                                                   | Step Right canonical projection                   |
| [D-0124](0124-continue-v2-acquisition-beyond-prior-request-allowances.md)             | Continue v2 acquisition beyond prior request allowances                                             | V2 acquisition operations                         |
| [D-0125](0125-rehearse-step-right-admission-on-disposable-state.md)                   | Rehearse Step Right admission on disposable state                                                   | Step Right source admission                       |
| [D-0126](0126-pause-v2-overnight-after-integrated-local-validation.md)                | Pause v2 overnight after integrated local validation                                                | V2 continuation                                   |
| [D-0127](0127-measure-state-storage-read-only-on-a-copy.md)                           | Measure state storage read-only on a copy, with digests never row bodies                            | State retention and recovery cost                 |
| [D-0128](0128-intern-derivation-payloads-behind-a-view.md)                            | Intern derivation payloads behind a `derivation_rows` view                                          | Derivation output storage                         |
| [D-0129](0129-verify-every-generation-before-dropping-the-old-rows.md)                | Verify every generation before dropping the old derivation rows                                     | Derivation output storage                         |
| [D-0130](0130-retain-output-is-the-one-derivation-output-writer.md)                   | `retain_output` is the one writer of derivation output                                              | Derivation output storage                         |
| [D-0131](0131-gate-payload-removal-with-a-permission-row.md)                          | Gate payload removal with a permission row in the deleting transaction                              | Derivation output storage                         |
| [D-0132](0132-time-a-held-checkpoint-by-restoring-it-first.md)                        | Time a held checkpoint by restoring it first, then backing up what came out                         | State retention and recovery cost                 |
| [D-0133](0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md)                 | Bound the worker disk and remove rehearsals and old checkpoints eagerly                             | Worker disk space                                 |
| [D-0134](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)          | Let row references outlive payload bytes, with no foreign key between them                          | Derivation output storage                         |
| [D-0135](0135-fence-every-path-the-measurement-writes-and-exit-on-the-gates.md)       | Fence every path the measurement tool writes, and make its gates the exit status                    | State retention and recovery cost                 |
| [D-0136](0136-make-a-payload-removal-grant-impossible-to-commit.md)                   | Make a payload removal grant impossible to commit                                                   | Derivation output storage                         |
| [D-0137](0137-one-module-owns-the-closure-the-collector-and-the-walk.md)              | One module owns the file closure, the collector, and the retention walk                             | State retention                                   |
| [D-0138](0138-recovery-markers-pin-current-pointers-and-nothing-more.md)              | Recovery markers pin the current pointers and nothing more                                          | State retention                                   |
| [D-0139](0139-findings-declare-the-support-they-rely-on.md)                           | Findings declare the support they rely on                                                           | State retention                                   |
| [D-0140](0140-a-hold-is-one-checked-file-under-the-control-lock.md)                   | A hold is one checked file written under the control lock                                           | State retention                                   |
| [D-0141](0141-two-retention-limits-and-the-collector-age-become-policy.md)            | Two retention limits and the collector's age floor become policy                                    | State retention                                   |
| [D-0142](0142-the-size-cap-sets-one-whole-pipeline-pause-and-never-rewrites-one.md)   | The size cap sets one whole-pipeline pause and never rewrites one                                   | State retention                                   |
| [D-0143](0143-an-undeclared-file-is-unknown-in-the-plan.md)                           | An undeclared file is unknown in the plan, and the plan is named by its digest                      | State retention                                   |
| [D-0144](0144-unowned-bytes-are-unknown-and-a-wrong-declaration-is-reported.md)       | Bytes no label owns are unknown, and a declaration that names a missing file is reported            | State retention                                   |
| [D-0145](0145-a-hold-file-is-read-strictly-and-only-under-its-own-name.md)            | A hold file is read strictly, and only a file named like a hold is read as one                      | State retention                                   |
| [D-0146](0146-a-release-that-says-it-pinned-a-closure-must-produce-one.md)            | A release that says it pinned a closure must produce one                                            | State retention                                   |
| [D-0147](0147-only-a-planned-locked-apply-removes-anything.md)                        | Only a planned, locked apply removes anything                                                       | State retention                                   |
| [D-0148](0148-a-receipt-is-the-operator-copy-of-a-note-that-lives-in-the-database.md) | A receipt is the operator's copy of a note that lives in the database                               | State retention                                   |
| [D-0149](0149-no-generation-is-eligible-until-a-table-says-it-is-archived.md)         | No generation is eligible until a table says it is archived                                         | State retention                                   |
| [D-0150](0150-reclaim-rewrites-in-place-on-the-one-writer-connection.md)              | Reclaim rewrites the file in place, on the one writer connection                                    | State retention                                   |
| [D-0151](0151-a-resumed-note-removes-only-what-the-fresh-plan-still-names.md)         | A resumed note removes only what the fresh plan still names                                         | State retention                                   |
| [D-0152](0152-the-backup-command-prunes-its-own-checkpoints.md)                       | The backup command prunes its own checkpoints, by the retention table                               | Worker disk space                                 |
| [D-0153](0153-checkpoint-cleanup-never-fails-the-thing-it-cleans-up-after.md)         | Checkpoint cleanup never fails the backup or the health check                                       | Worker disk space                                 |
| [D-0154](0154-policy-removes-only-what-the-code-wrote-and-sizes-it-once.md)           | Checkpoint policy removes only what the code wrote, and sizes it from the manifest                  | Worker disk space                                 |
| [D-0155](0155-the-plan-digest-covers-what-is-archived.md)                             | The retention plan digest covers what is archived                                                   | State retention                                   |
| [D-0156](0156-capture-the-retention-limits-as-values.md)                              | Capture the retention limits as values, and keep them out of every recipe                           | State retention                                   |
| [D-0157](0157-an-operator-report-names-the-items-not-only-the-count.md)               | An operator report names the items, not only the count                                              | State retention                                   |
| [D-0158](0158-build-steps-2-and-3-before-the-measurement-and-gate-the-rest-on-it.md)  | Build steps 2 and 3 before the measurement, and gate the rest on it                                 | State retention                                   |
| [D-0159](0159-an-expired-pause-is-not-an-existing-pause.md)                           | An expired pause is not an existing pause                                                           | State retention                                   |
| [D-0160](0160-an-empty-declaration-never-clears-a-findings-recorded-references.md)    | An empty declaration never clears a finding's recorded references                                   | State retention                                   |
| [D-0161](0161-a-requirement-finding-relies-on-the-snapshot-pin.md)                    | A requirement finding relies on the snapshot pin, not on a declaration                              | State retention                                   |
| [D-0162](0162-the-residency-check-covers-only-newly-declared-generations.md)          | The residency check covers only newly declared generations                                          | State retention                                   |
| [D-0163](0163-every-retention-value-is-checked-where-the-table-is-read.md)            | Every retention value is checked where the table is read                                            | State retention                                   |
| [D-0164](0164-a-checkpoint-holds-the-control-lock-across-its-closure-and-copy.md)     | A checkpoint holds the control lock across its closure and copy                                     | State retention                                   |
| [D-0165](0165-reduce-tests-by-behavior-and-tool-lifetime.md)                          | Reduce tests by behavior and tool lifetime                                                          | Test suite maintenance                            |
| [D-0166](0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md)         | Hold interning back until rows repeat, and cut indexes and JSON first                               | State retention and recovery cost                 |
| [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md)                    | Intern derivation payloads in the last migration                                                    | State retention and recovery cost                 |
| [D-0168](0168-run-core-tests-by-default-and-keep-an-extended-suite.md)                | Run core tests by default and keep an extended suite                                                | Test suite maintenance                            |
| [D-0169](0169-agents-decide-and-record-and-ask-only-for-high-impact-choices.md)       | Agents decide and record; the owner is asked only for high-impact choices                           | Documentation                                     |

The [legacy decision log](legacy-design-review.md) preserves the original
review notes. It is historical evidence, not a second list of current rules.
In particular, its old rule allowing probable public identity matches was
replaced by the behavior described in D-0002.

## When to write a decision

Record every decision in the same change, including architecture, behavior,
documentation, operations, and routine implementation choices. Do not filter
by size, cost, or ease of reversal. Chat, code comments, and change descriptions
do not replace a record here. See [D-0006](0006-record-every-decision.md).

Use the [template](TEMPLATE.md). Assign the next unused four-digit number;
never reuse an ID. Give each record one choice and a descriptive filename.
Keep small decisions to a few sentences; use more detail only when needed.
Retain the template's status and acceptance fields; omit empty body sections.
Link supporting research and add every record to the index.

Logging a choice does not require a new approval step for already authorized
work. Record the actual authority and source; never invent acceptance. When
older decisions surface, record what is known and mark missing evidence.

## Acceptance and lifecycle

| Status     | Meaning                                                                                                  |
| ---------- | -------------------------------------------------------------------------------------------------------- |
| Proposed   | Under consideration; does not establish a rule.                                                          |
| Accepted   | Explicitly agreed by the project owner or a named delegate. Record who, when, and the acceptance source. |
| Rejected   | Considered and declined. Preserve the reason.                                                            |
| Superseded | Replaced by a later accepted decision. Link both records.                                                |

An agent's recommendation is not acceptance. Accepted does not mean implemented,
tested, deployed, or published. Put those claims and their evidence in
[current status](../../docs/status.md) and the related plan or outcome record.

For imported history only, **Historical; acceptance not yet verified** means
the choice appears in old design or code, but the acceptance record has not
been established. Existing implementation is not proof of who approved it.
This migration label creates no new approval gate for ordinary authorized work.
Resolve it when evidence is found; do not invent a date or approver.

Once accepted, preserve the substance of the record. Fix typos and links;
record a changed choice in a new decision. If only part changes, state the
scope of replacement in both records. Update the index with every status change.

## Keep current docs in step

When implementing an accepted decision, update its current reference page in
the same change. A proposed alternative must not silently replace the current
rule. The reference explains what applies; this journal preserves the reasons.
