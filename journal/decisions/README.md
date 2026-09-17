# Project decisions

A decision record states a choice, the reason for it, and who accepted
it. Start with the summary in each record; evidence and implementation detail
are linked below it.

## Decision index

| ID                                                                           | Choice                                                          | Status                                         | Topic                           |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------- | ------------------------------- |
| [D-0001](0001-documentation-and-decisions.md)                                | Separate current docs, project history, and formal decisions    | Accepted; partly replaced by D-0005 and D-0006 | Documentation                   |
| [D-0002](0002-confirmed-public-identities.md)                                | Publish person IDs only for confirmed matches                   | Historical; acceptance not yet verified        | Identity                        |
| [D-0003](0003-preserve-source-evidence.md)                                   | Keep source evidence separate from derived records              | Historical; acceptance not yet verified        | Architecture                    |
| [D-0004](0004-postgres-migration.md)                                         | Port to PostgreSQL before moving the worker to Dokploy          | Historical; acceptance not yet verified        | Hosting                         |
| [D-0005](0005-research-under-journal.md)                                     | Keep research tools and evidence inside the journal             | Accepted; replaces D-0001 research placement   | Research                        |
| [D-0006](0006-record-every-decision.md)                                      | Record every decision in the decision log                       | Accepted; expands D-0001 decision scope        | Documentation                   |
| [D-0007](0007-bounded-closure-proof-reuse.md)                                | Reuse closure proof only after rechecking its exact evidence    | Proposed                                       | Build completion                |
| [D-0008](0008-local-validation-checks.md)                                    | Make focused validation checks run independently                | Proposed                                       | Development checks              |
| [D-0009](0009-retained-source-event-enumerations.md)                         | Retain source-event page obligations from admitted parents      | Proposed                                       | Event completion                |
| [D-0010](0010-bounded-event-turns.md)                                        | Rotate source events after a bounded request turn               | Proposed                                       | Scheduling                      |
| [D-0011](0011-explicit-event-evidence-drilldown.md)                          | Verify event artifacts in an explicit doctor drill-down         | Proposed                                       | Event reporting                 |
| [D-0012](0012-protect-listed-page-capacity.md)                               | Reserve new-work capacity for listed result pages               | Proposed                                       | Scheduling                      |
| [D-0013](0013-preserve-historical-migration-tests.md)                        | Keep historical migration tests pinned to their reviewed schema | Proposed                                       | Migration validation            |
| [D-0014](0014-explain-recorded-event-service.md)                             | Explain event service from issued-request receipts              | Proposed                                       | Event reporting                 |
| [D-0015](0015-bound-event-expansion-observations.md)                         | Bound verification before gating event-index expansion          | Proposed                                       | Scheduling                      |
| [D-0016](0016-stream-event-body-verification.md)                             | Stream body verification in event diagnostics                   | Proposed                                       | Event evidence                  |
| [D-0017](0017-report-current-event-blockers.md)                              | Report current blockers without inferring eligibility           | Proposed                                       | Event reporting                 |
| [D-0018](0018-gate-expansion-with-conservative-observations.md)              | Gate new event indexes with conservative local observations     | Proposed                                       | Scheduling                      |
| [D-0019](0019-retain-observed-event-blocker-changes.md)                      | Retain observed changes in event blockers                       | Proposed                                       | Event reporting                 |
| [D-0020](0020-pin-event-enumerations-in-release-evidence.md)                 | Pin event enumerations in release evidence                      | Proposed                                       | Release coverage                |
| [D-0021](0021-verify-page-evidence-at-a-release-cutoff.md)                   | Verify page evidence at a release cutoff                        | Proposed                                       | Release coverage                |
| [D-0022](0022-preserve-supported-event-bases.md)                             | Preserve supported event bases                                  | Proposed                                       | Event preservation              |
| [D-0023](0023-include-build-tests-in-default-discovery.md)                   | Include build tests in default discovery                        | Proposed                                       | Test discovery                  |
| [D-0024](0024-compare-restored-event-work-with-an-uninterrupted-run.md)      | Compare restored event work with an uninterrupted run           | Proposed                                       | Restore validation              |
| [D-0025](0025-bind-local-coverage-to-artifact-evidence.md)                   | Bind local release coverage to artifact evidence                | Proposed                                       | Release coverage                |
| [D-0026](0026-record-stage-outputs-and-verified-event-progress.md)           | Record stage outputs and verified event progress separately     | Proposed                                       | Event progress                  |
| [D-0027](0027-compare-turn-policies-with-all-modeled-pages-ready.md)         | Compare turn policies with all modeled pages ready              | Proposed                                       | Scheduling validation           |
| [D-0028](0028-reconstruct-retained-snapshot-parse-hints.md)                  | Reconstruct retained snapshot parse hints                       | Proposed                                       | Queue recovery                  |
| [D-0029](0029-resume-h16-production-release-after-validation.md)             | Resume H16 production release after validation                  | Accepted                                       | Release operations              |
| [D-0030](0030-retain-live-operation-evidence-outside-workspaces.md)          | Retain live operation evidence outside workspaces               | Proposed                                       | Operation evidence              |
| [D-0031](0031-guard-production-initialization-memory.md)                     | Guard production initialization memory                          | Proposed                                       | Release supervision             |
| [D-0032](0032-handle-missing-legacy-runtime-recipe-during-initialization.md) | Handle missing legacy runtime recipe during initialization      | Proposed                                       | Initialization compatibility    |
| [D-0033](0033-repair-production-control-lock-ownership.md)                   | Repair production control-lock ownership                        | Proposed                                       | Release operations              |
| [D-0034](0034-record-bounded-event-accounting.md)                            | Record bounded observations of known event completion           | Proposed                                       | Event accounting                |
| [D-0035](0035-prove-immediate-event-page-retirements.md)                     | Prove page retirements on one enumeration edge                  | Proposed                                       | Event retirement evidence       |
| [D-0036](0036-count-pinned-unavailable-origin-observations.md)               | Count pinned unavailable-origin observations                    | Proposed                                       | Release coverage                |
| [D-0037](0037-account-for-supported-unavailable-page-gaps.md)                | Account for supported unavailable page gaps                     | Proposed                                       | Event accounting                |
| [D-0038](0038-pin-fixture-helpers-beside-frozen-runtime.md)                  | Pin fixture helpers beside the frozen runtime                   | Proposed                                       | Fixture acquisition preparation |
| [D-0039](0039-exercise-integrated-event-accounting-offline.md)               | Exercise integrated event accounting offline                    | Proposed                                       | Event completion validation     |
| [D-0040](0040-allow-service-read-access-to-verifier-receipts.md)             | Allow service read access to verifier receipts                  | Proposed                                       | Release operations              |

| [D-0041](0041-consolidate-agent-workspace-drafts.md) | Consolidate the integrated agent workspace drafts | Proposed | Repository maintenance |

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
