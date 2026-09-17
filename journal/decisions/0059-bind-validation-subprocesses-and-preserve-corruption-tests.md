# D-0059: Bind validation subprocesses and preserve corruption tests

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Integrated validation  
Supersedes: —  
Superseded by: —

## Decision

Pass the frozen runtime and repository paths through `PYTHONPATH` for the full
validation process and its children. Pytest's configured import path alone does
not bind subprocesses; the shared editable environment can load the changing
checkout even when the parent runs from a frozen directory.

Keep the deliberate incremental-BLOB corruption test by dropping the new,
unrelated retirement expression index before the witness is cached. SQLite
refuses incremental BLOB writes on a table with an expression index. The test
must still corrupt bytes without UPDATE triggers and require actual witness
reconstruction to reject them; an ordinary UPDATE would weaken that test.

Update the real release test to require at least one verified zero unsupported
count and allow unknown counts where assessment is unavailable. Requiring every
unsupported count to stay unknown contradicts the newly implemented verifier.

## Why

The first integration run reported four failures. Two child processes imported
unfrozen source; one reached schema 28 while its parent supported schema 27.
Two older assertions needed adaptation to the new schema and reporting contract.
Preserve that failed receipt, verify each correction separately, and run the next
integrated frozen source afresh. Neither selective passing checks nor the prior
2,088-test baseline establishes a full pass for these changes.

The second full run exposed two tests that attempted requests contrary to the
new spacing contract. Keep the post-crash recovery delay, and construct retained
legacy service receipts without issuing a live request under an unsupported
schema. The third review freeze copies the second inventory and changes only
those two test files. Its runtime bytes are identical; concurrent DCN parser
work remains outside this rollout. A new source receipt and full run bind the
corrected tests without mixing unfinished parser work into operational gates.

## Links

- [Continuation](../investigations/2026/v2-continuation-2026-09-17.md)
- [Failed integration run](../evidence/runtime/event-extension-2026-09-17/validation-001/pytest.log)
