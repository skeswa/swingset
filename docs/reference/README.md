# Reference

These pages own the exact rules and formats. Start with the
[pipeline explanation](../how-it-works/README.md) if the terms are new.
A reference can include a labeled pending extension; the [status page](../status.md)
is the entry point for implementation and operating evidence.

## Collection and interpretation

| Rule                                                 | Owner                                 |
| ---------------------------------------------------- | ------------------------------------- |
| Requests, host limits, responses, and archive        | [Fetching](fetching.md)               |
| Site-specific behavior and permitted host overrides  | [Source guides](sources/README.md)    |
| Work selection, polling, and event turns             | [Scheduling](scheduling.md)           |
| Observed acquisition intervals and diagnostic alarms | [Acquisition timing](event-timing.md) |
| Historical date boundary and archive coverage        | [Backfill](backfill.md)               |
| Parser inputs, outputs, and source admission         | [Parsing](parsing.md)                 |

## Dataset and identity

| Rule                                                   | Owner                                                                 |
| ------------------------------------------------------ | --------------------------------------------------------------------- |
| Published IDs, tables, and relationships               | [Data model](data-model.md)                                           |
| Evidence for person matches and reviewed corrections   | [Identity linking](identity-linking.md)                               |
| Candidate inputs, checks, and contents                 | [Build](build.md)                                                     |
| Public commits, acknowledgment, and baseline promotion | [Publishing](publishing.md)                                           |
| Enum values                                            | [Generated enums](enums.md)                                           |
| Encoded competition rules                              | [WSDC rules](wsdc-rules.md) and [rule history](wsdc-rules-history.md) |

## Runtime and shared terms

| Rule                                                 | Owner                                     |
| ---------------------------------------------------- | ----------------------------------------- |
| Module ownership and evidence flow                   | [Architecture](architecture.md)           |
| Stored evidence, work, and invalidation              | [Local state](state.md)                   |
| Worker lifecycle, controls, and recovery             | [Operations](operations.md)               |
| Cross-module recovery guarantees and target behavior | [Recovery contract](recovery.md)          |
| Runtime tools                                        | [Technology](technology.md)               |
| Data use, removals, and source terms                 | [Ethics and privacy](ethics-and-legal.md) |
| Meaning of shared terms                              | [Glossary](glossary.md)                   |

Use [task guides](../guides/README.md) for commands,
[decisions](../../journal/decisions/README.md) for reasons behind lasting choices,
and [external references](references.md) for background.
