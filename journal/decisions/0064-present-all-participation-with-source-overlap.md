# D-0064: Present all recorded participation with source overlap

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Dataset exploration  
Supersedes: —  
Superseded by: —

## Decision

Present JesAnn Nail’s recorded history as a standalone local web page. Default
to all recorded contest-and-role participation, including non-final and
non-registry entries. Show source percentages after grouping corresponding
registry and score-sheet records, and preserve the distinction between confirmed
registry identity and unconfirmed printed-name matches.

Use month, edition, role, division, and compatible placement to group entries
for reading. Five explicit edition aliases bridge differing source labels;
exclude Pro-Am and novelty contests from registry grouping. Show result-only,
both-source, and registry-only counts so overlap is visible. Count judging
separately. An absent registry match does not establish zero points.

## Why

The user requested a web page, then explicitly required all participation and
the percentage supported by detailed results versus registry summaries. A
points-first page obscured 15 result-only entries, including four entries whose
best recorded round was a preliminary or semifinal. A static page embeds the
reviewed extract and works offline without a service or frontend dependency.

The requested behavior is authorized in the conversation. The particular
layout and grouping implementation are agent choices; their implementation
is not recorded as explicit owner acceptance of a project-wide policy.

## Links

- [Page and build instructions](../tools/quality/person-history/README.md).
- [Inputs and provenance](../evidence/quality/jesann-nail-2026-09-17/README.md).
- [Validation outcome](../investigations/2026/jesann-history-page-2026-09-17.md).
