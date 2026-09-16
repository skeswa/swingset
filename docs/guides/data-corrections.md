# Reviewing and correcting data

Review uncertain records before changing their identity or event assignment.
The [identity reference](../reference/identity-linking.md) owns the exact rules.
Read [current status](../status.md) before rebuilding or publishing production data.

Identity decisions are append-only. Follow the [decision-journal contract](../reference/recovery/identity.md#the-decision-journal)
when adding a correction; do not erase earlier reviewed decisions.

## Review and removals

Review published `review_queue` rows and add narrowly scoped alias, identity,
source URL, or suppression CSV rows. Use `NONE` for an explicitly unmatched
identity override. Rebuild and inspect the candidate before enabling publication.
Suppression runs at build and leaves raw private evidence intact. Replace an
affected committed parser fixture with another event and update its expected
observations; do not merely hide the fixture from the tests.

GC is manual (`swingset gc`). It must retain artifacts referenced by snapshots,
findings, accepted inputs, retained candidates, or checkpoints and remove only
unreferenced files older than one day.
