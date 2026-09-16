# Finding files inside nested checkpoint manifests (H13)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Pause controls let an operator stop selected work and inspect its status. This record concerns their checks and recovery behavior. The original work ID is H13.

The first private backup after H13 acceptance failed verification on
2026-09-13 at 10:23:18 UTC, before upload. Its declared files included
`operations/h13-20260913/execution.control-probe/checkpoint/checkpoint.json`,
but the verifier excluded every file whose basename was `checkpoint.json`.
That nested manifest is retained acceptance evidence; only the outer checkpoint
manifest is outside its own file list.

The correction compares the complete path with the outer manifest path.
Checkpoint verification, restore copying, local artifact recovery and the
operations helpers now use that rule. Nested manifests still require their
recorded size and SHA256. Missing, extra or altered files remain failures.
Regression tests verify and restore a nested manifest, reject its modification,
and recover an exact historical body from a checkpoint that contains one.
Thirty-four backup/recovery tests passed; the broader 56-test selection also
passed before the final artifact-recovery regression was added.

Production remains on the accepted H13 schema12 runtime. To capture the backup
without importing unfinished schema13 code, an immutable verification-only
source copies that H13 runtime and changes only the two checkpoint/recovery
modules:

- Source: `/nix/store/0yq6dbr63yldsr1fkgfzyvqpm2vax1sg-swingset-h13-checkpoint-fix-source`.
- Receipt SHA256: `e359136cb07b2174c8ddd2394bceedb615abeb64e4ca1ec9a157e63c92320f5e`.
- Parent: `/nix/store/mn94ln72schglx9fb1qq8k48bv8nl37a-source`.
- New checkpoint: `/var/lib/swingset/checkpoints/h13-before-h14-20260913-closure-fix`.

The corrected runtime and backup script are retained in operations before the
new checkpoint is captured. The failed checkpoint remains available for
diagnosis and was not altered. The replacement contains 66,993 files and uploaded
at 10:40:31 UTC to private commit
`48a625781cb580e36cc0f2a99ab06740ef5f6c6f`. Its manifest SHA256 is
`4ae650570ed1561573edf11edd842324ed527d3bae9f0e82431a6d6facd8e084`.
Remote privacy, head and immutable manifest verification passed at 10:45:26 UTC;
the receipts are retained in `journal/evidence/runtime/h14/h14-production-backup.json`
and `h14-production-private-verification.json`. Production backup metadata now
records that verified checkpoint. H14's deployed runtime will include the same
correction.
