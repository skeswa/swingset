# New proof-source scratch replay preparation

Prepared on 2026-09-16. **Not executed.** The coordinator must first supply
the explicit frozen-tests-pass gate. This preparation starts no unit and creates
no scratch database.

The old scratch marker identifies the actual original H15 checkpoint:

- Path: `/var/lib/swingset/checkpoints/h15-before-h16-20260913`.
- Manifest SHA-256: `700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444`.
- Original database SHA-256: `d81b2e459471c25c430a57c2a4d096180f1094e57c1b8b1e896d66774f12e8dc`.

`supervise-proof-replay.py` runs on the VM as root but launches only new scratch
units as user/group `swingset`. It uses the immutable source
`/nix/store/4c4q8rkiz6jqizcy93bid6cz8mfvp20b-source`, source receipt
`71dc14101ca19880f3addfa2dc0073dbe50bce0e4e2be27d7bc91811b6d1e338`, and the
source's unchanged `research/replay_derivations.py`. Native libraries,
`PYTHONPATH`, source revision, config and overrides are explicitly pinned.

The new destination is `/var/tmp/swingset-h16-proof-replay`. Existing scratch
state, output receipts or unit names are refused. The script uses
`--prepare-from` once in a separately bounded unit, then runs at most 20 replay
invocations with `--max-seconds 600`, `RuntimeMaxSec=900`, memory accounting,
and a 45-second stop grace period. It checks aggregate cgroup anonymous memory
every 20 seconds and stops the current scratch unit at 6 GiB. This sampled
threshold is not a kernel-enforced memory ceiling. Production units are never
started, stopped or reconfigured.

Before each launch and during monitoring, the production operator hold must
exist and all six collection/backup/summary units and timers must be inactive.
New attempt outcomes must remain running or succeeded; none may remain running
after exit. Nonprogress, interrupted work, unsafe receipts, resource limits or
worker failure stop the supervisor without launching another invocation.
Successful bounded invocations must increase generation count. A `current`
receipt must explicitly have no unfinished scopes and ends supervision.

The first actual replay bundle digest is recorded once in
`replay-input-binding.json`, with its source and scratch marker hashes. Every
subsequent receipt must use that same digest. No old runtime's bundle hash is
substituted. Commands, samples, worker receipts, journal output and the final
supervisor receipt are retained beside this note with exclusive creation and
flushed writes. The supervisor does not claim independent final replay
verification or candidate-build acceptance; those follow separately.

The gate JSON must include `passed: true`, `source` equal to the immutable path
above, and `source_receipt_sha256` equal to the receipt above. It should include
the coordinator's actual frozen test receipts. Supply its actual file SHA-256.
Only after that gate is explicitly released:

```sh
orb -m swingset -u root \
  /nix/store/s5rij9y3vb4gxbh0iqz3k0ndmsac7h02-python3-3.12.13/bin/python3.12 \
  /Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16/supervise-proof-replay.py \
  --frozen-tests-receipt /absolute/path/to/frozen-tests-gate.json \
  --frozen-tests-sha256 ACTUAL_GATE_SHA256
```

The script received a syntax parse only during preparation. No replay or
preparation unit was launched. An initial read of the old scratch marker as the
ordinary VM user was denied; the subsequent root read succeeded. A read-only
unit lookup confirmed the new preparation unit did not already exist.
