# Current status

Recorded from repository evidence on 2026-09-15. This documentation review did
not contact the running worker or Hugging Face. The dates below describe the
latest retained evidence, not a fresh check of production.

## What is available

The dataset has published identity corrections, an event inventory, and checks
that reject unsafe source interpretations. These are stages V1–V4 in the
[history and recovery plan](plans/history-and-recovery.md#3-stages).
The retained public baseline is `81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`.
Detailed historical score-sheet coverage remains incomplete.

## What is running

The last operating handoff records an OrbStack NixOS worker using SQLite
schema 14. Scheduled collection, backup, and summary jobs are held by
`/var/lib/swingset/operator-hold`. Preparing documentation or a migration does
not clear that hold.

The worker has the original H16 release-evidence code installed. Its full
production initialization and next publication have not been accepted.
H16 means the work to check that a release has all its supporting evidence;
it does not mean that a release has completed.

## What is blocked

| Work                    | Evidence and next requirement                                                                                                                                                                                         |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Next release            | Scratch rebuilding completed, but the full candidate build exceeded the 45-second completion transaction limit and rolled back. A fresh full build and independent audit must pass before deployment and publication. |
| Historical score sheets | Source fixtures, event aliases, and year reviews remain necessary. The retained handoff records no years accepted for phase-two acquisition.                                                                          |
| Identity evaluation     | Human review of the representative sample remains pending. Model-generated review notes do not satisfy it.                                                                                                            |
| Automatic repairs       | Activation follows the remaining history, recovery, and review gates.                                                                                                                                                 |
| PostgreSQL and Dokploy  | A detailed migration plan exists. It is a target, not the recorded production setup.                                                                                                                                  |

## Where to continue

- [Release handoff](../journal/investigations/2026/2026-09-15-release-handoff.md): exact source pins, scratch paths, holds, and release gates.
- [Build validation investigation](../journal/investigations/2026/h16-validation-investigation-2026-09-15.md): why isolated timings did not prove full-build completion. Its VM measurements are dated 2026-09-16 UTC / 2026-09-15 Denver.
- [Active plans](plans/README.md): remaining work and acceptance criteria.

Update this page when new evidence changes a current claim. Put detailed
commands, measurements, and receipts in the journal. Local tests, deployment,
and publication are separate milestones; do not infer one from another.
