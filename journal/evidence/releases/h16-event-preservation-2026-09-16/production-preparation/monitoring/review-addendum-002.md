# Memory guard lifecycle corrections

Version 002 is prepared for independent review; no VM staging or execution occurred. Use `guard-initialization-002.py`, not the original adapter. The original adapter, tests, README and receipt 001 retain their reviewed bytes, including the two defects discovered after its offline tests.

The overlap check now inspects every matched H16 unit. It permits a retained historical unit only when its live state is a clean exited record with no PID and no cgroup, or it has become inactive/absent with neither. `RemainAfterExit=yes` alone no longer makes a completed worker appear live. A running PID, retained cgroup, activating/deactivating unit, unknown state, or unclean retained exit still blocks launch. The regression reads all 13 exact terminal unit records from `replay-terminal-resource-verification.json`; it does not infer their present VM state from that old receipt.

Each new initializer invocation separately tracks whether a live unit with readable cgroup telemetry was observed. Once observed, a later collected unit is allowed to wait for the actual launcher result and normal receipt/settlement checks, even after 30 seconds. Collection does not become success by itself. A worker never observed live still has the 30-second absent-start bound. Tests cover both the long-worker collection race and resetting this observation before the next invocation.

The pinned supervisor remains SHA-256 `9d48562395750244fb64c8420390c3402c8af2aea2019e46bb66577c1b3c4312`. Its driver/source/gate/marker checks, 12 serial invocation cap, exact-unit cleanup and writer-lock verification remain unchanged. Private samples and the 6 GiB anonymous-memory stop guard are unchanged. Sampling is still a cooperative stop threshold, not a kernel memory ceiling.

After review, use the parent packet's frozen environment and actual reviewed gate/marker hashes with this corrected command shape:

```text
python OPS/monitoring/guard-initialization-002.py \
  --supervisor OPS/supervise-initialization.py \
  --gate OPS/initialization-gate.json --gate-sha256 ACTUAL_GATE_SHA \
  --marker OPS/initialization-marker.json --marker-sha256 ACTUAL_MARKER_SHA \
  --driver OPS/initialize.py \
  --driver-sha256 b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233 \
  --session FRESH_REVIEWED_SESSION --max-invocations 12
```

These placeholders supply no execution authority. Verify the separately reviewed version-002 adapter hash before staging or launch. Receipt `offline-checks-002.json` binds its actual bytes, its test file, the unchanged supervisor, and the retained terminal-proof fixture used by the regression. This preparation is not production acceptance.
