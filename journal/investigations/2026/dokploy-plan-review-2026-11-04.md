# Adversarial review of the Postgres and Dokploy plan

Date: 2026-11-04  
Type: Outcome  
Topic: Migration plan review  
Related plan: [Postgres and Dokploy](../../../docs/plans/postgres-migration/README.md)

## Summary

Three subagents reviewed the plan in two rounds. Round 1 found six high
and six medium issues. The plan was revised, and each reviewer checked
the revised files. Round 2 found no high or medium issues in any of the
three scopes. The six remaining wording issues were then corrected.

This is a maintenance review of a design, not an implementation or a
deployment. No production operation, publication or deletion was run.
The existing uncommitted plan revisions were preserved. The starting
working-copy snapshot was `607dfbbb`, on parent `7206f842`.

## Findings and changes

| Scope and severity  | Failure the reviewer identified                                                                                  | Change to the plan                                                                                                                       |
| ------------------- | ---------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Recovery, high      | Database rows and mutable publication or hold files could describe different moments                             | Establish the snapshot and copy mutable files under B; metadata transitions also take B, with crash reconciliation before backup capture |
| Recovery, medium    | Comparing all history after recovery rejects legitimate run, backup and acknowledgement records                  | Compare history exactly before reconciliation; then check only named, tested recovery transitions                                        |
| Recovery, medium    | A kept backup could outlive the code and schema needed to restore it                                             | Pin and retain recovery images, migration checksums, classification rules and configuration; test an older backup across a migration     |
| Database, high      | PostgreSQL's default isolation could mix different generations across a reader's statements                      | Pin repeatable-read snapshots at reader entry; keep writers read-committed and take C before domain reads                                |
| Database, high      | Re-keying every unit could map a bumped version to its old output                                                | Re-key only proved-equal eligible units; leave bumped transforms and affected consumers queued                                           |
| Database, medium    | An interrupted verification could report no differences and open the release gate                                | Require the complete expected unit inventory, no failures or missing shards, and matching checkpoint and audit identities                |
| Development, medium | Immutable environment identity conflicted with restoring production state into dev, and dev loops were forbidden | Create destination identity separately, audit the explicit exception, and distinguish mock loop runners from production schedulers       |
| Operations, high    | The shared Docker network did not enforce the claimed rehearsal isolation                                        | Require a Rhuidean-owned allowlist proxy and firewall on every worker attachment, with denial tests after recreation                     |
| Operations, high    | A real request in the dry run lost its accounting when that database was discarded                               | Rehearse on owned endpoints; make the first live attempt through the final production database after fencing the Mac                     |
| Operations, medium  | The documented SSH tunnel had no loopback database endpoint                                                      | Provision and test a loopback-only relay before stage 2                                                                                  |
| Operations, high    | Deploys could lift an existing operator hold or migrate while old writers remained                               | Give each deploy its own persistent hold, drain old writers, check readiness, and release only its hold                                  |
| Operations, medium  | The Mac copies could be deleted before an independently protected backup existed                                 | Require a checked current complete backup beyond the server's deletion authority before deleting the Mac copies                          |

The detailed requirements and tests live on the plan's
[database](../../../docs/plans/postgres-migration/3-database.md),
[hosting](../../../docs/plans/postgres-migration/4-hosting.md),
[stage 0](../../../docs/plans/postgres-migration/5-stage-0.md),
[dry run](../../../docs/plans/postgres-migration/7-stage-2.md),
[switch](../../../docs/plans/postgres-migration/8-stage-3.md) and
[development](../../../docs/plans/postgres-migration/10-local-development.md)
pages.

## Final reviewer verdicts

| Reviewer            | Round 1                | Round 2                                                                                              |
| ------------------- | ---------------------- | ---------------------------------------------------------------------------------------------------- |
| `review_recovery`   | One high, two medium   | No high or medium; clarify B-before-C in the diagram and settlement rule                             |
| `review_database`   | Two high, two medium   | No high or medium; align the release rule and switch estimate with the drain requirement             |
| `review_operations` | Three high, two medium | No high or medium; name the release service's backup credential and qualify automatic-release timing |

All six wording corrections were applied. The first round's minor notes
also led to an explicit retention-rowid entry and a distinction between
healthy held services and overdue-hold alerts. No reviewer reviewed
another reviewer's verdict; each rechecked the plan in its own scope.

Moving the live probe to stage 3 is a deliberate tradeoff: stage 2 proves
isolation and the fetch path with an owned source, but cannot prove that a
real source accepts the new address. The first real attempt, including a
timeout or a prerequisite robots request, makes rollback forward-only.
Its accounting stays in production and the Mac stays fenced on failure.

## Validation and limits

The review used local code, the plan and Rhuidean's local platform
reference. External platform details were checked against primary
documentation: [PostgreSQL isolation](https://www.postgresql.org/docs/17/transaction-iso.html),
[Docker networking](https://docs.docker.com/compose/how-tos/networking/),
[Dokploy Compose networking](https://docs.dokploy.com/docs/core/docker-compose/utilities),
[Dokploy database connections](https://docs.dokploy.com/docs/core/databases/connection)
and [R2 bucket locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/).
These sources do not prove the current server configuration.

Validation passed: `mise run fmt`; 298 relative links across 16 files;
and `nix develop -c uv run pytest -q` with 2,147 passed and 1,541
deselected in 545.25 seconds. The status dashboard has 47 lines.
The plan's future Postgres, isolation, backup and cutover tests remain
implementation gates; this review does not claim they have run.
The move remains unstarted.

## Follow-up: iteration speed

Later on 2026-11-04 the owner asked for a development environment that
encourages fast iteration. The plan now makes the everyday tools part of
the first stage 1 slice: a warm native Postgres test service, focused and
watch commands, a small reusable seed and bounded local replay. CI and
server-lifecycle fault tests keep fresh containers; each database test
still owns its database and uses real commits. Native Mac checks are not
claimed as exact AMD64 runtime proof.

The trigger is the measured 545.25-second core run above against the
180-second repository budget. A new focused baseline,
`nix develop -c uv run pytest -q tests/test_normalize.py --durations=5`,
passed all five cases in 18.27 seconds; its slowest reported phase was
0.34 seconds of setup. This does not locate all the remaining time or
measure PostgreSQL overhead. Stage 1 must separate startup, collection,
database setup, execution and teardown before further tuning.

Warm targets are 30 seconds to start work, 10 for the representative
focused check, 30 for a bounded seed replay, and the existing 180-second
core budget. These are requirements to measure, not achieved results.
Replay can execute edited code without a version bump only in disposable
dev state; it cannot authorize a production release or re-key.

Implementation remains unchanged. The earlier passing core result still
covers the same source and tests; the focused run supplies a new latency
baseline. The follow-up changes were checked by `review_database`.

That check found two medium issues: cluster-wide roles/settings can
contaminate a reused test server, and forced replay at an unchanged
fingerprint conflicts with immutable generations. The plan now validates
the warm cluster, gives global-state tests disposable containers, and
gives selected replay units a scratch-only recipe identity while keeping
their original outputs for comparison. The slowest of three warm samples
decides each feedback target.

The recheck found no remaining high or medium issues in the DevEx
revisions. These additional review rounds cover the iteration-speed
changes; the original migration review above remains its own record.
