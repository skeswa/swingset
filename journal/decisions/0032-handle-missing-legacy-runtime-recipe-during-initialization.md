# D-0032: Handle a missing legacy runtime recipe during initialization

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: Coordinator implementation within the owner's authorized H16 release; owner acceptance of this implementation choice is not separately recorded.  
Topic: Initialization compatibility  
Supersedes: —  
Superseded by: —

## Decision

Treat a missing prior `pipeline` / `recipe/runtime` input as unknown when
preparing H16 initialization. Compare it with the captured reviewed recipe;
the normal input-acceptance path must invalidate extractor cache labels and
queue affected parse work. It must not execute that parse work.

## Why

The first production preparation failed because the operational initializer
indexed a missing query result. Failure occurred before marker creation,
bundle capture, or input acceptance. The retained legacy state predates this
accepted recipe entry. An absent prior recipe cannot establish an unchanged
runtime.

## Consequences

Add a narrow optional-row check in a new operational initializer. Exercise
actual frozen-runtime acceptance from a legacy fixture, including unchanged
and changed existing recipes. Preserve the failed attempt and original driver.
Rebind the supervisor, memory guard, final verifier, gate, marker, and outputs
to new versioned files and reviewed hashes before retrying.

The frozen application source and rehearsed bundle remain unchanged. Existing
policy, protected-state, backup, source, and release checks still apply. A
successful preparation must precede the new read-only anchor and guarded run.

The new initializer passed seven tests against the frozen schema-14 runtime,
including actual legacy input acceptance and interrupted-transaction retry.
The rebound supervisor, memory guard, verifier, and unchanged release helpers
passed 180 tests. The coordinator independently reproduced all recorded
literal substitutions and verified old and new hashes. This validates the
operational compatibility fix; it does not establish production initialization.

## Links

- [Production release outcome](../investigations/2026/h16-production-release-2026-09-16.md).
- [Memory supervision](0031-guard-production-initialization-memory.md).
- [Compatibility review](../evidence/releases/h16-event-preservation-2026-09-16/production-preparation/legacy-runtime-fix/coordinator-review-001.json).
