# Active plans

Plans describe remaining work and how to prove it is complete. They do not
establish that a feature is deployed. Start with [current status](../status.md)
for the latest retained operating evidence.

| Goal                                            | Plan                                                     | Read first                                      |
| ----------------------------------------------- | -------------------------------------------------------- | ----------------------------------------------- |
| Add historical results while improving recovery | [History and recovery sequence](history-and-recovery.md) | Stage order and gates                           |
| Preserve corrections and recover safely         | [Recovery rollout](recovery/README.md)                   | Work packages and acceptance scenarios          |
| Fill historical gaps from event sites           | [Historical source collection](historical-sources.md)    | Source restrictions and review requirements     |
| Move to PostgreSQL and Dokploy                  | [Migration](postgres-migration/README.md)                | Approach, then the relevant implementation step |

The [project milestones](milestones.md) define dataset outcomes across these
plans. Earlier rollout documents live in the [archive](../../journal/archive/README.md).

## Reading older work IDs

- **V1–V7:** stages in the history and recovery plan.
- **H1–H18:** recovery and data-quality changes in the recovery rollout.
- **WP:** an implementation work package. Early packages are in the
  [first implementation plan](../../journal/archive/v1-implementation-plan.md);
  historical packages are in the [backfill reference](../reference/backfill.md#work-packages).
- **G1:** the controlled registry probe rehearsal.
- **M:** a milestone; the PostgreSQL plan has its own explicitly scoped M0–M9 steps.

Use descriptive titles in new writing and retain these IDs only to connect
existing plans and evidence. A new proposal belongs in a
[decision record](../../journal/decisions/README.md) when it changes a lasting choice.
