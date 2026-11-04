# D-0274: Retain accepted semantic inputs and absence tombstones

Recorded: 2026-11-05  
Decided by: agent under D-0172, 2026-11-05; source: the owner's instruction to implement stage 1  
Topic: History backup and recipe identity  
Supersedes: Stage 1's blanket accepted-input bookkeeping reset  
Superseded by: —

## Decision

Keep `accepted_inputs` rows for the `pipeline` consumer as history anchors,
except `recipe/env`, which existing policy excludes from semantic recipes.
Restore all three fields exactly before workers run. Ordinary input acceptance
may advance only `digest`; consumer and input name remain intact. Audit inputs
and the excluded environment row still reset.

## Why

Input acceptance records a removed optional or legacy input with the empty
content digest. The real sample still used that explicit absence for
`recipe/runtime`. Reconstructing only the current bundle's members lost the
token, changed the complete build recipe and prevented reuse of its captured
render inputs. Coverage and changelog bytes then changed despite equal facts.
The current semantic input map is retained context, including absent inputs;
restore must neither guess missing keys nor ignore recipe differences.

## Links

[History row rules](../../docs/plans/postgres-migration/4-hosting.md#backups-and-recovery).
