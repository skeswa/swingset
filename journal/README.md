# Project journal

The journal brings together research, its supporting files, and the decisions
that follow. Start with a question or finding, then open the tools and evidence
only when you need to check the details. For current behavior, use the
[documentation](../docs/README.md).

| Question                                       | Start here                                                     |
| ---------------------------------------------- | -------------------------------------------------------------- |
| What did we investigate or learn?              | [Investigations by topic](investigations/README.md)            |
| What did we choose, and who accepted it?       | [Formal decisions](decisions/README.md)                        |
| How can I reproduce a check?                   | [Research tools by purpose](tools/README.md)                   |
| Where are the captures, reports, and receipts? | [Evidence by topic](evidence/README.md)                        |
| What happened during earlier work?             | [Archived plans and implementation records](archive/README.md) |

## Add research without creating another pile

Start an [investigation](investigations/TEMPLATE.md) with a clear question and a
short conclusion. Link it from the relevant topic index. Put its evidence in a
named run under `evidence/<topic>/`; link that run to the investigation.

Reusable scripts belong in `tools/<topic>/`. Link them to their inputs and the
investigations they support. Frozen copies of scripts that produced a retained
receipt stay with that evidence. A runnable script is not permission to change
a worker or contact a source site.

Record every decision using the [decision process](decisions/README.md), including routine implementation choices.
This is the project journal. The application's identity decision journal in
`overrides/identity_overrides.csv` is a separate data source.
