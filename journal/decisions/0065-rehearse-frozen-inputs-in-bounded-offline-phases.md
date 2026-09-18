# D-0065: Rehearse frozen inputs in bounded offline phases

Recorded: 2026-09-17  
Decided by: agent  
Topic: Extension input acceptance rehearsal  
Supersedes: —  
Superseded by: —

## Decision

Use separate prepare, accept and bounded drain phases on a full independent
checkpoint copy. Pin frozen runtime 003, the current schema-14 checkpoint and
the reviewed helper. Require external configuration and the complete CSV override
inventory to equal the frozen bundle before accepting inputs.

Reuse ordinary input acceptance, offline selection, control scopes and
parse/project/link workers. Keep admission policy and all source findings. Do not
call collection, build, publication or automatic repair activation. A network and
subprocess audit interlock prevents external operations in the helper process.

## Why

The retained backlog contains 31,821 pending parse hints before new runtime
invalidation. The H16 project/link scratch driver omits raw artifacts and cannot
exercise ordinary parsing. A full independent copy preserves those artifacts
without risking writes to the checkpoint. Separate phases expose input changes
and bound each replay turn; an empty eligible selection is not proof that every
derivation is current.

## Consequences

Every phase retains the hold, baseline files, raw evidence, paid requests,
original admissions and controls. Existing admission policies stay exact;
ordinary initialization may add a shadow policy but cannot activate or weaken a
reviewed policy. Named judges, including null-ID names, are compared against the
checkpoint after each mutation phase. A difference requires evidence review.

Failed parses and blocked admission remain findings. Bounded turns can resume
from durable normal attempts, but the helper does not claim fleet completion,
clean-rebuild equivalence, production input acceptance or publication. The time
option bounds further selection, not the entire turn: an already-started worker
can use multiple separately bounded write transactions and unbounded parser
computation. Receipts report actual elapsed time and selection-budget overrun.
Full retained-file verification adds operating time outside that budget.

## Links

- [Preparation and validation](../investigations/2026/extension-input-rehearsal-2026-09-17.md)
- [Derivation contract](../../docs/reference/recovery/derivation.md)
- [Admission contract](../../docs/reference/recovery/admission.md)
