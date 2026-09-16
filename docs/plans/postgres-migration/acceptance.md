# Prove integration and acceptance

Part of the [PostgreSQL migration plan](README.md). These are target
requirements, not evidence that migration has happened. Keep the numbered
steps in order and preserve the recorded operating holds.

## 10. M6 — Test integration and measurable acceptance

`tools/test_postgres.py` starts a uniquely labeled PostgreSQL container with
test-only credentials and no production mounts, provisions per-test databases
and role sets, runs its child command, and always cleans up only its resources.
Use a random loopback port for host tests. CI may use an isolated Docker
network instead. Parallel tests get separate databases because advisory lock
keys are database-scoped. Concurrency/crash tests must use actual commits and
independent processes; do not wrap the entire test in a rollback transaction.

Keep parser/model fixtures offline. Convert existing persistence fixtures and
subprocess helpers to PostgreSQL configuration; no silent SQLite fallback.
Retain SQLite only for legacy migration and differential oracle tests. Add
typed test helpers rather than duplicating setup SQL in every test. Run tests
with source networking blocked/mocked and no HF token. Assert zero unexpected
HTTP requests in the integration suite.

These commands must work after M6; the test launcher sets the temporary DB
configuration and destroys it after its child exits:

```sh
mise run fmt
nix develop --command uv sync --frozen
nix develop --command uv run ruff check .
nix develop --command uv run mypy
nix develop --command uv run python tools/test_postgres.py -- uv run pytest -q
docker build --platform linux/amd64 -t swingset-migration:local .
```

CI retains Ruff/mypy/full-suite checks and adds final-image tests on Linux
AMD64. Add libpq/compiler/PostgreSQL client dependencies to Nix where the C
driver needs them. Format only through `mise run fmt`. Verify unrelated
pre-existing edits remain byte-identical except for specifically intended
additive index changes. Do not add snapshot tests that merely mirror DDL;
tests must demonstrate invariants or realistic failure recovery.

Required acceptance groups and their pass conditions:

| ID      | Evidence                                                                                                                                                                                                           |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| DB1     | Commit, rollback, nested failure recovery, RC writes, coherent RR reads, and row access parity pass on both semantic profiles.                                                                                     |
| DB2     | Separate processes prove A across commits, B/C pause ordering, timer skip, control timeout without mutation, and lock release after process death.                                                                 |
| DB3     | Sleeping SQL, lock wait, CPU-bound Python, failed cancellation, and lost commit acknowledgment produce the specified bounded/uncertain result without a second external effect.                                    |
| DB4     | Every trigger mapping has an invariant test; control cannot mutate evidence; worker cannot alter schema or disable guards.                                                                                         |
| PORT    | Differential fixture action sequences match logical state and expected failures; all existing behavior tests pass.                                                                                                 |
| IMPORT  | Exact per-table and artifact audit, rowid/sequence preservation, no source mutation, inactive interrupted targets, and no accepted new input.                                                                      |
| BACKUP  | Complete round trip including concurrent controls, pending publication, legacy artifact recovery, and GC protection.                                                                                               |
| RUNTIME | Persistent scheduling, all holds, duplicate worker exclusion, secret-free provenance/logs, SIGTERM, and database/container restarts pass.                                                                          |
| OBS     | Structured logs, cheap probes, hold-aware status/conditions, redaction, restart persistence, and documented Dokploy capability/notification coverage.                                                              |
| ADMIN   | Pinned CLI identity; noninteractive invocation; authenticated list and parameterized reads; verified booleans; safe JSON/error handling; reconciled timeouts; CLI/HTTP fallback parity; and restart from receipts. |
| SCALE   | Full-checkpoint import/audit/restore/build and manifest bounds meet section 11; record actual timings and peaks.                                                                                                   |
| HOST    | Actual Dokploy-generated mounts, networks, limits, image identity, held supervisor, and recreated-volume persistence match the specification.                                                                      |

**M6 exit:** local groups through RUNTIME and local OBS tests pass with retained
receipts, including ADMIN tests against a local fake API and the actual pinned
CLI binary. Capture requests to prove `freshVolumes` is never true, false-valued
updates use a working route, and cleanup cannot delete volumes. Test timeout
after remote acceptance, old successful deployment records, malformed/error
JSON, nonzero exit, missing credentials, stale IDs, and secret-bearing responses.
Tests must not contact sandile.dev or send notifications. SCALE, HOST, actual
ADMIN compatibility, and actual Dokploy visibility require the authorized
checkpoint/target; mark them pending if absent.
