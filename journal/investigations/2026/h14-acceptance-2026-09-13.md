# Checking fair scheduling (H14)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Fair scheduling shares time between competing tasks. This record concerns measured service or deployment checks. The original work ID is H14.

H14 gives new pages, current refresh, identity confirmation and old evidence
explicit shares of existing host capacity. Issued requests, including robots,
redirects and retries, retain the existing durable budget debit. Selection cannot
increase host limits or create catch-up burst credit. Offline stage and kind
rotation reserves processing time; builds run before spare time returns to
collection. Shared projections can rerun in one cycle only for changed inputs.

The [shadow census](h14-shadow-load-2026-09-13.md) records 35,417 retained watches
and the basis for the initial allocation. It is not a throughput measurement.
Doctor and daily summaries separate initial objectives from observed requests,
bytes, offline attempts and pressure. Request wall ages include pauses.

Dateless metadata gets three recovery attempts and then a 30-day recheck.
Unavailable evidence gets a 90-day recheck. Repeated snapshots from an existing
parent cannot reset this policy; a newly discovered parent relationship or
recovered dates can reactivate work while preserving explicit pauses.

## Validation

Independent scenarios cover sustained current demand beside old evidence,
partial-day quota exhaustion, cooled and paused hosts, pause/admission races,
the two-issued-request pressure reserve, restart rotation, bounded metadata
recovery, and publication time under sustained acquisition. The retained-state
picker benchmark reports selection cost separately from achieved service.

Runtime Ruff and mypy (148 modules) pass. Crash injection before and after every
cycle transaction and SIGTERM recovery pass (two tests, 81.91 seconds). The integration selection passes 981 tests (34.03 seconds); it excludes the
separately tested crash harness and bootstrap helper. The historical-cycle tests
now assert reserved acquisition and durable supersession under the new order.
Six bootstrap tests pass, including exact preservation of alternate-parent
controls and rollback after interruption. Together with the crash harness, the
verified selections cover 989 tests.

## Production gate

The verified schema12 checkpoint contains 66,993 files at private commit
`48a625781cb580e36cc0f2a99ab06740ef5f6c6f`, manifest SHA256
`4ae650570ed1561573edf11edd842324ed527d3bae9f0e82431a6d6facd8e084`.
Remote privacy, head and immutable-manifest verification passed at 10:45:26 UTC.
The [checkpoint closure correction](h13-checkpoint-closure-fix-2026-09-13.md)
is included in the H14 release.

Production migration acceptance passed in 41.178 seconds. Schema12→13 preserved
all original table contents, seeded 6,274 exact parent relationships, left the
three service/metadata tables empty, and installed 183 publication fence triggers.
Read-only selection and fresh-process doctor comparison passed at fixed time
2026-09-13 10:55:30.160228 UTC. The complete doctor report hash is
`e951910fa16ad2167f9d97ca764a5c90a8bc392c8f68a20a90658a0b293bb526`.
Human and JSON requirement inventories agree.

The deployed source is `/nix/store/2q702cgg7rxw7nydlh97mjh4wggp5ia8-source`,
539 declared files, receipt SHA256
`e70f7f1d29534836a06a14af1422cd609e871998f610643e5e67ed0c20554822`.
NixOS is `/nix/store/gb11xg7015fiw9x9jl1ldbm4s05hdz0v-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
The separately retained operations driver hash is
`198cca5070d7cf761064b9b16bb61973eaf58dfa56d6977f394df0957d9a4d82`.
[Gate](../../evidence/runtime/h14/h14-production-gate.json),
[preflight](../../evidence/runtime/h14/h14-production-preflight.json), and
[execution](../../evidence/runtime/h14/h14-production-execution.json) retain the comparisons.
Full doctor outputs remain under `/var/lib/swingset/operations/h14-20260913`.

The public baseline remains `81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`.
Scheduled workers remain held. Acceptance made zero source requests, executed no
simulated service and published no dataset. No year acceptance or repair
activation is inferred from these tests. H15 now follows the frozen H14 release.
