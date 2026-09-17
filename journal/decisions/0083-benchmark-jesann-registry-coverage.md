# D-0083: Benchmark JesAnn registry coverage against individual results

Status: Accepted  
Recorded: 2026-09-17  
Accepted: 2026-09-17  
Acceptance source: Project owner approved the Jes Test plan in conversation  
Topic: Dataset quality  
Supersedes: —  
Superseded by: —

## Decision

Maintain an offline Jes Test benchmark for WSDC #7849. Its primary metric is
registry-entry coverage by uniquely corresponding, provenance-verified
individual result evidence. Report final results and judge marks separately.
Measure agreement only when independent final evidence exists. Show the report
in the JesAnn history page and use baseline comparisons to canary progress and
regressions between dataset releases. Do not make the benchmark an automatic
publication gate.

## Why

JesAnn has registry history across many years and individual result evidence in
recent years, making her a useful coverage check. Matching must not depend on
placement, so a disagreement remains measurable and visible. Explicit aliases
are benchmark-only and do not update production identity links.

## Consequences

Reports are reproducible from a pinned local dataset and expose their coverage
denominators. Release comparisons identify changes that merit review. Agreement
remains secondary and may have a small denominator. This benchmark measures
one person and does not establish whole-dataset accuracy.

## Links

[Jes Test implementation](../tools/quality/jes_test/README.md) ·
[History page outcome](../investigations/2026/jesann-history-page-2026-09-17.md) ·
[Baseline report](../evidence/quality/jesann-nail-2026-09-17/jes-test/initial-baseline/report.md)
