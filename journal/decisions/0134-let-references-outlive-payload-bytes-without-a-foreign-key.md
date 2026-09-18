# D-0134: Let row references outlive payload bytes, with no foreign key between them

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Derivation output storage  
Supersedes: —  
Superseded by: —

## Decision

`derivation_row_refs.payload_sha256` names a row in `derivation_payloads` but
carries no SQL foreign key. The index on that column stays, because the removal
plan needs to ask which generations still name a payload.

Residency is therefore a fact code checks, not one SQLite enforces:

- `retain_output` asks whether any of a generation's references name bytes that
  are gone, and puts missing bytes back
  ([D-0130](0130-retain-output-is-the-one-derivation-output-writer.md)).
- plan step 3's removal plan decides which payloads may go, under the writer and
  control locks, and plan step 4 only removes payloads it has archived twice.

A reference whose payload is not local drops out of the view's join. So a reader
of the `derivation_rows` view sees no rows for that generation, never some of
them: release rebuilding raises `selected_output_digest_mismatch` because the
count and digest do not match the label, and the build receipt and artifact
checks report the generation as absent.

The scope pointer is not a reader of the view. `derivations.current` answers
from the pointer, its signature, and the input fingerprint, so a current
generation whose bytes were removed is still reported current and nothing
recomputes it. Keeping every current generation's bytes local is plan step 3's
job; the view is not a second line of defence for that case.

## Why

Payload bytes are the one derivation record the plan may remove, and references
are permanent. A foreign key says the opposite: while any reference names a
payload, SQLite refuses to delete it. With the key in place the permission-row
delete gate in [D-0131](0131-gate-payload-removal-with-a-permission-row.md)
could never fire for a payload a generation actually uses, so plan sections 7
and 8 could never remove a single byte, which is the reason migration 30 exists.
Deferring the key only moves the same failure to commit time, because the
reference is still there then. The alternative order, deleting references first,
is not open: references are labels, and the plan never deletes a label.

Removing the key also keeps `PRAGMA foreign_key_check` honest. That check runs
after every migration, in checkpoint verification, and in recovery. With the key
present, an archived generation would report violations forever.

The cost is that nothing stops a reference naming a payload that was never
stored. `retain_output` is the only writer, it stores the payload before it
writes the reference, and it checks the whole generation against its label
before its transaction commits, so a reference with no payload cannot be
created. Plan step 3 reports the mirror case, a payload nothing references, as
unknown, and never removes it.

## Alternatives

- Keep the foreign key and delete references along with payloads. Rejected: the
  plan's third safety rule keeps labels and references forever, and doctor needs
  them to say who owned removed bytes.
- Keep the foreign key and make it `DEFERRABLE INITIALLY DEFERRED`. Rejected:
  deferring moves the check to commit, where the reference still exists, so the
  delete still fails.
- Turn foreign keys off in the removal transaction. Rejected: a pragma that
  disables a constraint for one statement makes the schema's promise untrue for
  every reader, and `foreign_key_check` would still report the result.
- Make the view a `LEFT JOIN` so a non-resident row appears with no payload.
  Rejected: readers would get a null where JSON text belongs and fail with a
  type error deep inside the rebuild, instead of the row-count and digest check
  each of them already has.

## Consequences

Payload bytes can be archived and removed, which is what the rest of the plan
depends on. The database no longer proves that every reference has its bytes;
`retain_output` and the removal plan carry that job, and the view hides a
generation whose bytes are gone rather than showing part of it. Every reader of
the view must keep checking the row count and digest against the label, which
they already do. Readers that answer from the pointer instead get no protection
from the view, so the removal plan has to keep their data local.

## Links

- [Implementation plan, sections 6 to 8](../../docs/plans/bounded-state-and-archive.md)
- [Layout](0128-intern-derivation-payloads-behind-a-view.md)
- [Output writer](0130-retain-output-is-the-one-derivation-output-writer.md)
- [Removal gate](0131-gate-payload-removal-with-a-permission-row.md)
- [Grant fence](0136-make-a-payload-removal-grant-impossible-to-commit.md)
- [State contract](../../docs/reference/state.md)
- Renumbered: interning is migration 32, not 30, since [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md).
