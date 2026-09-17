# D-0043: Explain linking terms beside the code

Status: Accepted  
Recorded: 2026-09-17  
Accepted: 2026-09-17, project owner  
Acceptance source: Owner request, “Please add this information in doc comments,” following the linking taxonomy explanation.  
Topic: Identity documentation  
Supersedes: —  
Superseded by: —

## Decision

Document linking terms on their data types and explain inputs, outputs, and
policy roles on the key functions. Use fictional bib 42, Alex Lee, and registry
IDs 100 and 200 to distinguish candidate pairs, scores, review permissions,
assignments, and conclusions. Keep exact rules in the identity reference;
docstrings explain how to read the implementation.

## Why

Readers should be able to understand the terms from their definitions and
call sites. In particular, a candidate is a proposed relationship, a score is
not a probability, and a selected hypothesis need not be confirmed.

## Links

- [Linking design](0042-separate-link-evidence-resolution-and-persistence.md)
- [Candidate definitions](../../src/swingset/link/candidates.py)
- [Resolution types](../../src/swingset/link/model.py)
- [Event workflow](../../src/swingset/link/resolution.py)
- [Identity reference](../../docs/reference/identity-linking.md)
