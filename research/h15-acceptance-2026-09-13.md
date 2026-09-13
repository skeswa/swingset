# H15 derivation acceptance

Status: complete. Schema 14 is deployed and production acceptance passed on
2026-09-13 UTC. No repair kind was activated and no source request or dataset
publication occurred.

H15 captures exact runtime recipes and immutable dependency and output
generations for projection, linking, and build. Project and link work follows
desired versus materialized inputs. Queue loss cannot make a scope current.
Completion fences late workers and commits output, revisions, and generation
references together. Candidate reuse verifies its complete file closure.

The dependency audit includes sibling source-index evidence, source envelope
ordering and provenance, registry candidates without prior edges, reviewed
reference migrations, year acceptance, findings, and snapshot transport.
Calendar identity continuity is captured separately and used during replay.
History inventory precedes mapping; trailing association and coverage follow
event projection. The retained H14 history oracle checks the factoring.

Validation covered all 1,040 collected tests: 1,031 passed in the broad run,
the remaining date-only fixture was corrected and its four-test module passed,
six bootstrap tests passed, and both transaction-crash and SIGTERM tests passed
in 163.39 seconds. Ruff passed; mypy checked 155 runtime modules. The central
formatter ran before the source was frozen.

## Production acceptance

The deployed source is
`/nix/store/694311m4fcif32w774bs8vpkc4iv6d9a-source`, with 561 declared files.
Its source receipt SHA-256 is
`be7001a7e3bd3bc496f40513662cb5b561217b55cf37a3e11255fa84deea1c8c`.
The NixOS system is
`/nix/store/0zlgf7zarcylyjik5plfi7f072mpm5k9-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
The separately retained operations driver SHA-256 is
`c6e75bf8ba76460301fdfab235a28ea26213f7e2aa85c81771afe2da631d436a`.

The [execution receipt](verification/h15-production-execution.json) records
60.77 seconds of migration and acceptance after preflight. Every original table
column and row is unchanged except schema metadata. The added snapshot recipe
cache is null. The migration registered 34,983 scopes and six input-change
tokens; all dependency, generation, and output proof tables are empty. No old
output was declared materialized. All 198 publication fence triggers are present.

Doctor at `2026-09-13T11:47:44.815170+00:00` matched a fresh process and its human
requirement report. Its exact report hash is
`8d3bc0435ab7b7f2812dc589f1634508f02aeff98e4122f597344f3120f01c2d`.
It enumerated 34,986 unfinished scopes, including three inferred shared scopes
beyond the migrated catalog. All lag ages without queue hints remain unknown.
The 176,952,221-byte JSON and 150,428,400-byte human reports stay under
`/var/lib/swingset/operations/h15-20260913`. The public baseline was unchanged.
The persistent operator hold and all six stopped service/timer units were
verified again after acceptance.

## Retained workload preparation

A disposable copy of the verified predecessor checkpoint measured 2,641,510,400
SQLite bytes. At 2026-09-13T11:34:39Z, the draft implementation enumerated
34,986 retained scopes in 137 pages of 256 in 0.55 seconds. It counted unfinished
work in 0.44 seconds, selected a project unit in 0.32 seconds, and selected an
offline unit in 0.68 seconds. All 34,986 scopes were unmaterialized. No output
proof was fabricated. A later draft doctor read completed in 11.86 seconds and
reported unknown lag ages where no queue hint survived.

The [final frozen-source measurement](verification/h15-retained-scale-20260913.json)
at 11:43:26.677980 UTC counted unfinished work in 0.449 seconds, selected the
first project unit in 0.00020 seconds, and selected offline work in 0.362 seconds.
Its first exact selection took 0.0224 seconds. It created no output generations.
Receipt SHA-256:
`66e43fc2b6ceca685c8a0a277c6e79740043a0c4e4f6e740ba34950113aa18bf`.
These measurements do not establish steady-state throughput, source availability,
or completed replay. Conservative shared-input changes can require full manifest
comparisons for unaffected scopes; no steady-state cost claim is made.

## Predecessor checkpoint

The verified schema 13 checkpoint is
`/var/lib/swingset/checkpoints/h14-before-h15-20260913`, with 67,544 files.
Its private commit is `a845bd78006fbc30f7bdf6ad1580d35fdec71a8c` and its
manifest SHA-256 is
`f1ade4f3db6a29cf329c01de43db4540e0bd3d6768030fc7ff5fe56d9a275268`.
The checkpoint finished at 11:00:41.405986 UTC; remote privacy, head, and
immutable manifest verification finished at 11:03:09.487264 UTC.

The public baseline remains commit
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`, candidate
`cand_7f8cf9bcbf7e4a60`, manifest
`87ac286a1ae452f09f7e5cea646f260ac34035f3d05bb83de17c7d9cc8f08884`.
The production acceptance driver verified these exact pins, all original SQLite
columns and rows, empty new proof tables, fresh-process doctor equality, and the
operator hold with stopped timers. It made no HTTP requests and published no dataset.

H16 release closure, H17 human adjudication, historical year acceptance, and
kind-by-kind activation retain their separate gates.
