# Exact fixture execution preparation, 2026-09-17

The owner approved the five-body fixture exception in
[D-0053](../../decisions/0053-approve-exact-new-source-fixture-exception.md).
The new [preparation helper](../../tools/admission/prepare_fixture_exception.py)
constructs the authorization and execution gate for the unchanged, reviewed
schema-14 wrapper. Preparation performs no acquisition, migration, policy
activation, production parsing or publication.

The helper verifies the retained wrapper and exact helper closure, then all
638 files declared by the frozen runtime receipt. It holds the existing state
writer lock while reading the actual schema, acknowledged baseline, service
hold, control rows and current UTC-day Archive usage. It rejects changed
publication pins, schema changes, an absent hold, a restore marker, an exhausted
daily budget, overlapping quarantine paths and reused preparation outputs.
It does not reset paid usage or manufacture 12 available requests. A partially
consumed daily budget remains partially consumed at execution.

The new files are an authorization, pinned execution gate, preparation receipt
and explicit `execute.sh`. The coordinator executes that script once after
reviewing the observation. The reviewed wrapper rechecks live controls and
charges each actual request through the shared Archive gate. Its quarantine
does not add production parse work, and the wrapper does not call ordinary
scheduler backpressure. Existing H13 pauses and host restrictions still apply.
This records the behavior of the accepted wrapper; it does not create a new
general exemption from production admission.

The initial operation destination was
`/var/lib/swingset/operations/v2-continuation-20260917/fixture-001`, with separate
quarantine `/var/tmp/swingset-new-source-fixtures-20260917-001`. The interpreter
is `/var/lib/swingset/venv/bin/python` and the runtime remains
`/nix/store/z689qy41inndill3d92ym8im852x3649-source`. The coordinator observed
the owner response at `2026-09-17T15:32:49+00:00`; the authorization labels that
timestamp as an observation, not the time of the owner's click. Its short
execution window starts at preparation. A stopped acquisition is single-use;
changing its path or window does not grant another finite request allowance.

## Verification and handoff

Thirteen offline preparation tests passed, including altered packet bytes,
undeclared helper files, symlinks, invalid runtime receipt, changed baseline,
migrated schema, absent hold, restore marker, exhausted budget, worker lock
contention and preserved remaining paid usage. Ruff passed for the helper and
its test file. A separate read-only verification checked the retained local
runtime mirror's 638 source files and all nine declared helper files. These
checks do not establish a successful acquisition or source-kind acceptance.

The fresh local handoff packet is
`/tmp/swingset-fixture-preparation-20260917-001`, with 12 declared payload files.
Its preparer SHA-256 is
`50b2bcf623cf26482e8497bf4cd9afd579789bab39b4f020c5338cbc76359786` and staging
receipt SHA-256 is
`90cc4f7b37e256cc98921dada07f82567a69f136e186441fdc2e41da0354e366`.
It contains no production authorization derived from guessed state: the
coordinator must run preparation against the actual worker to create that
record. Execution receipts and parser findings belong in a subsequent dated
record; none are claimed here.

The coordinator's first transfer introduced macOS AppleDouble `._*` files.
Preparation rejected the undeclared helper files before creating authorization,
gate or quarantine, and before any network request. The failed staged packet
remains retained. The corrected transfer uses `COPYFILE_DISABLE=1` and a fresh
`fixture-002` operation directory, with the same intended quarantine. This is a
packaging correction before acquisition, not another acquisition allowance or
a retry of a stopped request. The execution gate will bind the corrected
packet's actual absolute helper path.

The coordinator prepared the corrected packet at `2026-09-17T15:42:00Z`:
638 source files verified, exact schema-14 baseline, no active pauses or
cooldown, and zero shared Archive requests that day. The first transient-unit
launch exited 127 before Python because `env` was absent from its `PATH`;
it created no acquisition attempt. The next launch supplied
`PATH=/run/current-system/sw/bin` and reused the same packet, authorization,
gate and quarantine. Its operational outcome is recorded separately by the
coordinator.

The maintained preparation helper now names
`/run/current-system/sw/bin/env` explicitly, because this runner is pinned to
NixOS and transient-unit environments need not have a default search path.
The command assertion is covered by the same 13 offline tests. This correction
does not change the staged helper or the two staging hashes above; those bind
the earlier bytes used for this operation. Parser edits remain frozen pending
the coordinator's integrated source freeze and real-body analysis.
