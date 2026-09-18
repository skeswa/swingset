# D-0169: Agents decide and record; the owner is asked only for high-impact choices

Recorded: 2026-09-18  
Decided by: owner, 2026-09-18, Sandile Keswa; source: session instructions "the decision file existing is itself proof of acceptance" and "agent makes most decisions and only prompts the owner for high impact or controversial choices"  
Topic: Documentation  
Supersedes: [D-0006](0006-record-every-decision.md) for the approval step only  
Superseded by: —

## Decision

Decision records carry no status and no approval step: a record's existence
is its acceptance. The agent makes and records most decisions itself,
including routine implementation choices, and asks the owner first only when
a choice is high impact or controversial, as the criteria in
[README.md](README.md) define. Every record says who decided in a
`Decided by` line, with the owner's instruction as the source when the owner
decided. Existing records were converted mechanically: the former `Accepted`
and `Acceptance source` fields became `Decided by: owner, ...` where an
acceptance had been recorded and `Decided by: agent` otherwise; the `Status`
field was dropped, and a qualifier it carried moved into the index's Choice
column or was already stated in the record. Records the earlier rule called
"Proposed" are now simply decisions.

## Why

The status field made every agent decision wait for a review that rarely
happened: 138 of 168 records were still "Proposed" on 2026-09-18, including
choices already implemented and tested. The owner wants agents to own routine
choices and to be interrupted only where a wrong call is costly or contested.

## Alternatives

- Keep `Status` and have agents mark their own records accepted. Rejected: a
  field that is always the same value says nothing.
- Drop provenance entirely. Rejected: knowing whether the owner was asked
  matters when a later choice would reverse a record.

## Consequences

Agents must judge impact honestly; the README criteria are the test. A record
written by an agent can still be reversed by a new record. Tools that read
the old `Status:` line were updated
(`journal/tools/admission/build_dcn_score_pdf_lookup.py`).

## Links

- [Decision index and criteria](README.md)
- [Template](TEMPLATE.md)
- [Repository instructions](../../AGENTS.md)
