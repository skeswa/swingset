# D-0008: Make focused validation checks run independently

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: Implementation authorized by the owner's instruction to continue the history and recovery plan; these exact routine choices have not been separately accepted.  
Topic: Development checks  
Supersedes: —  
Superseded by: —

## Decision

Add `tests` to pytest's configured import path so a nested test can import the
existing shared release fixture when run alone. Check that the scoring.dance
third-column header attributes are a dictionary before reading their title.
Keep the extraction and parser versions unchanged.

## Why

The completion tests passed only when another collected test happened to add
the top-level test directory to Python's import path. Declaring that directory
removes collection-order dependence. Moving the shared fixture would require a
larger test refactor without improving this fix.

The extractor constructs every attributes value as a dictionary, but mypy
cannot infer that through the mixed text-and-attributes cell structure. The
explicit type check preserves the existing extraction behavior and resolves
the error without suppressing type checking or changing parser versions.

## Links

- [Validation outcome and reproduction](../investigations/2026/local-validation-checks-2026-09-16.md)
- [History and recovery plan](../../docs/plans/history-and-recovery.md)
