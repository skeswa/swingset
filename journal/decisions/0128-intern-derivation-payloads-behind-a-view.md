# D-0128: Intern derivation payloads behind a `derivation_rows` view

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Derivation output storage  
Supersedes: —  
Superseded by: —

## Decision

Split `derivation_rows` into two tables in migration 30 and keep the old name as
a view over them:

- `derivation_payloads(payload_sha256, payload_json)` holds one copy of each
  distinct output row. The key is the sha256 of the canonical row text, the same
  digest `state/derivation_dependencies.py` already computes for observation
  payloads.
- `derivation_row_refs(generation_id, ordinal, table_name, record_key,
payload_sha256)` says which generation uses which row, in what order. It keeps
  the old primary key and unique key and the same deferred foreign key to
  `derivation_generations`. It has no foreign key to `derivation_payloads`,
  because references outlive the bytes they name
  ([D-0134](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)).
- The view `derivation_rows(generation_id, ordinal, table_name, record_key,
payload_json)` joins them.

Readers in `state/derivations.py`, `build/closure_rows.py`,
`build/generations.py` and `build/closure_manifest.py` are unchanged. The
no-update and no-delete triggers move to `derivation_row_refs`, and the history
dispatch fence's triggers move to both new tables.

## Why

Every generation stored a full copy of each output row, so recomputing a scope
that changed ten rows of a thousand cost a thousand rows. Interning by digest
makes growth track how much actually changed, which is the whole of what
"chunk sharing" and "deltas" meant in the replaced design
([D-0119](0119-bound-state-by-interning-and-one-closure.md)).

Keeping the old name as a view is what makes the change small. Four modules read
retained output, one of them (`closure_rows`) in the middle of the release
rebuild that must stay byte for byte. A view leaves their SQL, their column
order, and their digest arithmetic untouched, so the migration is the only thing
under test.

Unverified: how much this saves on production. The ratio of distinct rows to
total rows is measured in plan step 1, not here.

## Alternatives

- Change every reader to join the two tables. Rejected: more edited code in the
  release rebuild path for no gain, since the join is the same either way.
- Keep the payload inline and compress it. Rejected: it shrinks each copy but
  still stores one copy per generation, so growth still tracks generations
  rather than change.
- Intern with an integer surrogate key instead of the digest. Rejected: the
  digest is already computed for the same bytes elsewhere, so a surrogate adds a
  second identity for one thing.

## Consequences

Readers keep working unchanged, and a recomputation costs what it changed. The
view cannot be written to, so writes go through one function
([D-0130](0130-retain-output-is-the-one-derivation-output-writer.md)) and a few
tests that inserted rows directly now insert into the reference table. Row
references still grow at a few hundred bytes per row; plan step 1 says whether
that matters. The join drops a row whose payload is not stored locally, so a
generation whose bytes were archived reads as absent rather than short; every
reader checks the row count and digest against the label, so absent is caught
([D-0134](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)).
Anything counting application tables sees three more and one fewer: migration 30
adds `derivation_payloads`, `derivation_row_refs` and
`derivation_payload_removal_authority` and removes `derivation_rows`, so a
database goes from 117 tables to 119.

## Links

- [Implementation plan, section 6](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [Schema history](../../docs/reference/schema-history.md)
- [Direction](0119-bound-state-by-interning-and-one-closure.md)
- [No foreign key to payloads](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)
- Renumbered: interning is migration 32, not 30, since [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md).
