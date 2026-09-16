# Project decisions

A decision record states a choice, the reason for it, and who accepted
it. Start with the summary in each record; evidence and implementation detail
are linked below it.

## Decision index

| ID                                            | Choice                                                       | Status                                         | Topic         |
| --------------------------------------------- | ------------------------------------------------------------ | ---------------------------------------------- | ------------- |
| [D-0001](0001-documentation-and-decisions.md) | Separate current docs, project history, and formal decisions | Accepted; partly replaced by D-0005 and D-0006 | Documentation |
| [D-0002](0002-confirmed-public-identities.md) | Publish person IDs only for confirmed matches                | Historical; acceptance not yet verified        | Identity      |
| [D-0003](0003-preserve-source-evidence.md)    | Keep source evidence separate from derived records           | Historical; acceptance not yet verified        | Architecture  |
| [D-0004](0004-postgres-migration.md)          | Port to PostgreSQL before moving the worker to Dokploy       | Historical; acceptance not yet verified        | Hosting       |
| [D-0005](0005-research-under-journal.md)      | Keep research tools and evidence inside the journal          | Accepted; replaces D-0001 research placement   | Research      |
| [D-0006](0006-record-every-decision.md)       | Record every decision in the decision log                    | Accepted; expands D-0001 decision scope        | Documentation |

The [legacy decision log](legacy-design-review.md) preserves the original
review notes. It is historical evidence, not a second list of current rules.
In particular, its old rule allowing probable public identity matches was
replaced by the behavior described in D-0002.

## When to write a decision

Record every decision in the same change, including architecture, behavior,
documentation, operations, and routine implementation choices. Do not filter
by size, cost, or ease of reversal. Chat, code comments, and change descriptions
do not replace a record here. See [D-0006](0006-record-every-decision.md).

Use the [template](TEMPLATE.md). Assign the next unused four-digit number;
never reuse an ID. Give each record one choice and a descriptive filename.
Keep small decisions to a few sentences; use more detail only when needed.
Retain the template's status and acceptance fields; omit empty body sections.
Link supporting research and add every record to the index.

Logging a choice does not require a new approval step for already authorized
work. Record the actual authority and source; never invent acceptance. When
older decisions surface, record what is known and mark missing evidence.

## Acceptance and lifecycle

| Status     | Meaning                                                                                                  |
| ---------- | -------------------------------------------------------------------------------------------------------- |
| Proposed   | Under consideration; does not establish a rule.                                                          |
| Accepted   | Explicitly agreed by the project owner or a named delegate. Record who, when, and the acceptance source. |
| Rejected   | Considered and declined. Preserve the reason.                                                            |
| Superseded | Replaced by a later accepted decision. Link both records.                                                |

An agent's recommendation is not acceptance. Accepted does not mean implemented,
tested, deployed, or published. Put those claims and their evidence in
[current status](../../docs/status.md) and the related plan or outcome record.

For imported history only, **Historical; acceptance not yet verified** means
the choice appears in old design or code, but the acceptance record has not
been established. Existing implementation is not proof of who approved it.
This migration label creates no new approval gate for ordinary authorized work.
Resolve it when evidence is found; do not invent a date or approver.

Once accepted, preserve the substance of the record. Fix typos and links;
record a changed choice in a new decision. If only part changes, state the
scope of replacement in both records. Update the index with every status change.

## Keep current docs in step

When implementing an accepted decision, update its current reference page in
the same change. A proposed alternative must not silently replace the current
rule. The reference explains what applies; this journal preserves the reasons.
