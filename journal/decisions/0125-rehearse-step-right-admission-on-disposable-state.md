# D-0125: Rehearse Step Right admission on disposable state

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Step Right source admission  
Supersedes: —  
Superseded by: —

## Decision

Exercise all three Step Right contracts on a fresh schema-29 state built from
the five retained fixtures. Require a separately supplied review of the exact
prepared request before enabling policies in that disposable state. Do not use
the rehearsal to activate production, create live watches, accept a year or
publish data.

## Why

Parser and contract unit tests do not prove generation staging, review import,
policy activation and atomic admission together. A disposable rehearsal checks
that path without changing the held production database or turning quarantine
fixtures into ordinary acquisition.

## Links

- [Compact rehearsal receipt](../evidence/admission/stepright-admission-rehearsal-2026-09-18/receipt.json)
- [D-0122](0122-version-step-right-admission-without-removal-authority.md)
- [Admission reference](../../docs/reference/recovery/admission.md)
