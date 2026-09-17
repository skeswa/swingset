# Exact-source build and separately gated audit

Prepared on 2026-09-16. **Not executed.** Use
`launch-proof-build-audit.py` only after coordinator review. It wraps the three
unchanged, hash-verified VM drivers identified in `build-audit-preparation.md`.

The build gate is the final independent replay verification JSON. It must have
`passed: true`, the exact frozen source and receipt, new scratch path, marker
`f2001ab21849adfd1b3bc46b1f19d189e40b284baa438d848f64b984a4a4db5f`, input bundle
`410bd096dec912921c41702e1dc8fa5ef610385fd07afef52104561069eadcb8`, and empty
`unfinished_by_scope`. Supply the separately retained final `current` replay
receipt and both files' actual hashes. The completed replay supervisor receipt
is an additional prerequisite. A running or merely bounded replay cannot launch
the build.

```sh
orb -m swingset -u root \
  /nix/store/s5rij9y3vb4gxbh0iqz3k0ndmsac7h02-python3-3.12.13/bin/python3.12 \
  /Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16/launch-proof-build-audit.py \
  build --gate FINAL_INDEPENDENT_REPLAY_VERIFICATION \
  --gate-sha256 ACTUAL_VERIFICATION_SHA256 \
  --replay-receipt FINAL_CURRENT_REPLAY_RECEIPT \
  --replay-receipt-sha256 ACTUAL_REPLAY_RECEIPT_SHA256
```

The new state is `/var/tmp/swingset-h16-proof-build`; output is
`/var/tmp/swingset-h16-proof-build.json`. The worker uses `Type=exec`,
`RuntimeMaxSec=35min`, a three-minute stop grace, memory accounting and pinned
source/native-library environment. It deliberately omits `RemainAfterExit`:
the retained monitor expects the worker to become inactive when finished.
`systemd-run --wait` supplies direct exit evidence. The durable root monitor
starts immediately after the worker becomes active, samples every five seconds,
and enforces its existing sampled 6 GiB anonymous-memory threshold.

The launcher requires a zero `systemd-run --wait` exit, an actual running-worker
resource sample, no monitor termination reason, no build error, semantic preflight and matching
candidate/manifest fields. The application's 45-second completion deadline is
unchanged. Raw receipts, commands, exit log, monitor evidence and both unit
journals are retained here. `build-orchestration.json` binds the raw build
receipt hash and candidate identity. Preparation errors and unsuccessful units
remain failures even when some candidate files exist.

A tiny isolated `Type=exec` success probe confirmed the transient unit is
garbage-collected immediately after `--wait` returns. `systemctl show` then
returns default success/zero fields even though `LoadState=not-found`. The
launcher records this case explicitly and relies on the direct `--wait` exit,
not those defaults. A retained unit, when available, must also show success/zero.
`systemd-exit-probe.json` preserves the probe, including its five-second check.

**Audit is a separate invocation after the coordinator accepts the build.**
Nothing invokes it automatically. The coordinator can explicitly approve the
successful `build-orchestration.json` by supplying its path and hash as the
audit gate; its `build_receipt_sha256`, candidate ID, manifest and source must
match the actual successful build. An additional approval receipt may carry
those same fields instead.

```sh
orb -m swingset -u root \
  /nix/store/s5rij9y3vb4gxbh0iqz3k0ndmsac7h02-python3-3.12.13/bin/python3.12 \
  /Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16/launch-proof-build-audit.py \
  audit --gate EXPLICITLY_ACCEPTED_BUILD_PASS_GATE \
  --gate-sha256 ACTUAL_BUILD_PASS_GATE_SHA256
```

The audit uses candidate/baseline paths from the build receipt, requires both
inside the new build state, and verifies the actual candidate manifest hash.
It runs alone with the same frozen source and its own durable monitor. All
audit checks, exit evidence and candidate bindings must pass.

Both modes require the production hold and all six scheduled units inactive,
refuse reused states/receipts/units, and launch no production services. Neither
mode deploys, initializes, publishes or clears the hold. The supervisor received
syntax review only at preparation; no build/audit launcher was executed.
