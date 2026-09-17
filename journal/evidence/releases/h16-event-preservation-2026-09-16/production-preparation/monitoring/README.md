# Prepared initialization memory guard

Prepared locally on 2026-09-16; not staged or executed in the VM. This adapter needs independent review before launch. It adds a memory stop guard to the unchanged production initializer supervisor. It does not establish any production prerequisite, successful initialization, or release acceptance.

`guard-initialization.py` loads only supervisor SHA-256 `9d48562395750244fb64c8420390c3402c8af2aea2019e46bb66577c1b3c4312`. That supervisor retains its exact driver/source/bundle pins and actual gate/marker hash checks. The adapter executes the checked supervisor bytes and supplies a guarded Backend; its existing `supervise()` loop owns all launches, authority checks, receipt validation, stops, and settlement. No frozen driver was modified.

## Stop behavior and evidence

- Each of the supervisor's at most 12 serial initializer units gets a separate exclusive, mode-0600 `initializer-NNN.resources.jsonl` in its private VM-local supervision directory. Each record is flushed and fsynced. The adapter detects a replaced or unlinked sample pathname and stops; it does not silently keep writing to an invisible descriptor.
- The existing 20-second wait is split into waits of at most five seconds. Each sample records systemd unit state, `MemoryCurrent`, `MemoryPeak`, and the actual unit cgroup's `memory.stat` anonymous bytes. The cgroup charge includes worker descendants. It is not a Python allocation estimate or the supervisor's RSS.
- At **anonymous bytes >= 6 GiB**, or unreadable/missing telemetry for a live process, the adapter raises into the existing supervisor cleanup. That cleanup stops the exact initializer unit, waits for inactivity and its actual writer lock to be released, writes unsuccessful final evidence, and launches no next batch. The guard is disabled during cleanup so the same telemetry failure cannot interrupt the stop/wait path.
- No-cgroup startup is allowed only while no process is running, for at most 30 seconds. Completed units need no live cgroup. A cgroup removed during normal exit is checked against a second terminal unit-state reading.
- A pre-launch check rejects active/activating/deactivating `swingset-h16-*` services. The adapter also refuses another own launch before the preceding unit settles. The coordinator must still serialize all heavy work: this is not a global lock against unrelated manual processes or a race-free exclusion contract with other launchers.

The threshold is the existing operating stop policy, not a calibrated initializer memory requirement. Five-second sampling plus systemctl/read time cannot prevent brief between-sample overshoot. It is a stop guard, not a kernel anonymous-memory ceiling. Existing `TimeoutStopSec=180` and the supervisor's external deadline remain unchanged. `MemoryPeak` is total cgroup memory, not historical anonymous peak. A stopped run requires review; the wrapper does not retry it automatically.

## Why not launch the old build monitor separately?

The read-only inspection of `/var/tmp/h16-changelog-build-monitor.py` found a single fixed-unit monitor that checks only MainPID `RssAnon`, accepts outputs only outside production state, and replaces its output file. It does not own initializer cleanup or automatically attach to all dynamic units. The retained scratch supervisor already uses cgroup anonymous memory and synchronous exception cleanup, but treats absent memory data as zero. This adapter reuses the production supervisor's existing cleanup and records missing active telemetry as a failure.

## Command shape after review

Stage this new adapter in the private operation directory, verify its separately reviewed SHA-256, and keep the reviewed supervisor bytes unchanged. Use the frozen Python, source environment, gate and marker documented by the parent preparation packet. Do not use the current schema-21 checkout as the production runtime.

```text
python OPS/monitoring/guard-initialization.py \
  --supervisor OPS/supervise-initialization.py \
  --gate OPS/initialization-gate.json --gate-sha256 ACTUAL_GATE_SHA \
  --marker OPS/initialization-marker.json --marker-sha256 ACTUAL_MARKER_SHA \
  --driver OPS/initialize.py \
  --driver-sha256 b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233 \
  --session FRESH_REVIEWED_SESSION --max-invocations 12
```

`OPS`, `ACTUAL_GATE_SHA`, `ACTUAL_MARKER_SHA`, and `FRESH_REVIEWED_SESSION` are placeholders, not execution authority. The original supervisor requires root and creates a new private session directory exclusively. Each resource header binds the supervisor and adapter file hashes. Review the per-unit resource file together with the supervisor's start/final receipts; a clean resource log alone is not initialization success.

## Offline checks

`test_memory_guard.py` runs the actual frozen `supervise()` cleanup against mocked process/systemd reads and a real temporary writer-lock file. It covers high anonymous memory, missing and unreadable live telemetry, dynamic serial units, ordinary start/exit races, bounded startup, existing evidence protection, log-path replacement, overlapping H16 units, and supervisor hash mismatch. These checks do not run VM workers or read production state.
