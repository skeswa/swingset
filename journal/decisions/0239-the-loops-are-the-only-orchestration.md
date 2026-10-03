# D-0239: The loops are the only orchestration

Recorded: 2026-10-03  
Decided by: agent, 2026-10-03, under D-0175; source: architecture plan section 8 and phase 5 (owner-adopted 2026-09-25)  
Topic: Scheduling  
Supersedes: in part, [D-0215](0215-loops-share-the-state-under-a-reader-writer-lock.md) (the cycle as the service until `loops.enable` is set), [D-0185](0185-deploy-main-as-candidate-010-with-a-forty-minute-cycle.md) (the forty-minute cycle), [D-0188](0188-alert-on-failing-or-stalled-cycles-and-stop-re-queuing-on-config-switches.md) (the cycle checks, now the loop checks) and [D-0238](0238-leased-units-carry-no-admission.md) (`recover_admissions` stays in the locked commands), and amends [D-0189](0189-stop-publishing-until-identity-output-is-recovered.md) (its dry run now means release ticks build and do not publish)  
Superseded by: —

## Decision

The five loops (acquire, plan, derive, release, maintain) are the only way
production runs the pipeline. Phase 5b removes `run_cycle`, `swingset cycle`,
the `swingset-cycle` service and timer, and the phase-share allocator
(`allocation`, `CycleAllocation` and the `reconciliation_share`,
`acquisition_share` and `offline_share` keys, which config now refuses). The
module's loop units are unconditional; `loops.enable`, `cycleBudget` and
`watchdogQuietSeconds` are removed options that fail the build with this
record's number. The watchdog runs only its loop checks: `[cycles-failing]`,
`[cycles-quiet]` and `--timer-active` go. The release loop and the CLI run the
build and publish steps from `schedule/release_steps.py`. The locked CLI
commands keep `recover_admissions`: they hold the state exclusive, so every
active admission is dead, and a dead publication must turn uncertain before
a command's write meets its fence. This narrows D-0238's "until 5b": with
the cycle gone, migrate, restore and these commands run it.
[D-0189](0189-stop-publishing-until-identity-output-is-recovered.md)'s dry
run now means release ticks build and do not publish.

5b changes no schema and no parse or extract scope; it re-runs every project
and link unit once (`recipe_scope.py`, `fair_policy.py` and `versions.py`
are in those scopes). Dropping the three shares changes
`asdict(config.scheduler)`, so the event-turn and blocker-history policy
digests change once: a turn takes the new `scheduler_event_policies` row
when it next rotates, as it would anyway, and no turn's usage or order is
reset, and the next blocker sample of each event writes one new observation
(blocker observations are off in production). Nothing else in `event_*`
changes. Rollback is the previous system generation only when 5a deployed on
its own first. The tree that holds this record also holds 5a (migration 37)
in the same change, so it deploys as one 5a and 5b switch, and its only
rollback is the byte copy taken before migration 37: phase 4 code refuses
schema 37.

## Why

Two orchestrations had to select, attempt and count the same way (D-0215),
and every rule landed twice. The cycle's shares only split one process's
budget; separate timers do that now. 5b deploys only after switch 2b has run
the loops for 7 days with phase 2's acceptance met, so the cycle is no
longer the fallback anyone uses.

## Links

- [Architecture plan, phase 5](../../docs/plans/architecture.md)
