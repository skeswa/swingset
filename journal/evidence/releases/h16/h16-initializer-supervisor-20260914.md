# H16 initializer supervisor — offline preparation only

This wrapper has not run against the VM or production. It does not prepare or accept inputs. The coordinator must first complete actual same-pin rehearsal, deployment/read-only acceptance and initializer preparation, review the real gate and immutable prepared marker, and supply their SHA256 values. It never creates those prerequisites or replaces a failed continuation gate.

Files:

- Supervisor: `/tmp/h16-initializer-supervisor.py`, SHA256 `810479b1db1cdda88620d11b227b184ff813191cb98bd5ec14b2b13c317149e4`.
- Unchanged initializer: `/tmp/h16-live-initialize.py`, SHA256 `b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233`.
- Tests: `/tmp/test_h16_initializer_supervisor.py`; actual result: **40 passed in 0.13s**. Log: `/tmp/h16-initializer-supervisor-tests.log`.

Pinned source: `/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source`; source receipt `f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f`; prepared bundle `558bb5f4e88b47fe80a691254d3b21ac9e491296e80bb18f4599b64a88bfe9c0`. Existing operation directory: `/var/lib/swingset/operations/h16-closure-release-20260914`, owned by swingset with mode0700. No runtime, production or existing evidence file was changed during preparation.

## Reviewed launch shape

Execute the supervisor as root so it can create transient units with `User=swingset` and `Group=swingset`. The supervisor itself uses the exact frozen Python import environment below. **PYTHONDONTWRITEBYTECODE=1 is required for the root supervisor**, and is explicitly set on every worker, so imports do not try to write frozen-source caches. Verify the supervisor and initializer hashes before launch. Replace gate/marker locators and their hash placeholders with the actual independently reviewed files; do not generate substitute gates or markers to make this command run.

```sh
sudo env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source/src:/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source \
  SWINGSET_REVISION=uncommitted:2d5q3lgljfkmm78hf44g954lzwx79yhv-source \
  LD_LIBRARY_PATH=/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib \
  /var/lib/swingset/venv/bin/python /tmp/h16-initializer-supervisor.py \
  --driver /tmp/h16-live-initialize.py \
  --driver-sha256 b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233 \
  --gate /var/lib/swingset/operations/h16-closure-release-20260914/ACTUAL-INITIALIZATION-GATE.json \
  --gate-sha256 REVIEWED-GATE-SHA256 \
  --marker /var/lib/swingset/operations/h16-closure-release-20260914/ACTUAL-PREPARED-MARKER.json \
  --marker-sha256 REVIEWED-MARKER-SHA256 \
  --session batch-001 --max-invocations 12
```

The session directory must be new. The supervisor creates `supervision-batch-001` inside the fixed operation directory. It uses exclusive mode0600 files for preflight, batch start/final records, initializer receipts/logs, stop evidence and final summary. Prior files are never overwritten. Initializer receipts still follow the unchanged driver's normal atomic progress updates; the final supervisory record binds each finished receipt's hash. A reused session/output is refused. No HF token or acquisition credentials are needed.

Each worker uses only `run --max-seconds 900`. `RuntimeMaxSec=1500` allows real gate/source verification and streaming preservation hashes outside the worker budget; `TimeoutStopSec=180` permits cleanup. Monitoring waits are at most20seconds. Before launch, at each monitor boundary and after the final receipt, the supervisor verifies reviewed driver/gate/marker bytes, the held services/timers and baseline, current control digests, and prepared input authority. The unchanged initializer independently verifies all substantive gate evidence and the runtime bundle before work. These checks do not clear holds or edit controls.

Only exit0, status `bounded_stop`, positive completed count equal to attempted, all four preservation checks true, zero network requests, no parse/build/publication, unchanged authority/holds, and entirely accounted successful outcomes allow another batch. The initializer currently records successful outcomes as `succeeded` (its durable work ledger uses `output_committed`); both success labels are recognized, while any other reason fails. `current` additionally requires explicit `unfinished_by_scope={}`. Final receipt acceptance requires the transient unit inactive or collected and an independently acquirable writer lock. No build or release follows automatically.

Errors, no progress, changed controls/holds/authority, unexpected execution or interrupted supervision stop the sequence. If a service is active, the supervisor sends a normal stop only to its own transient unit, including when the `systemd-run` launcher died separately. It preserves stop/final evidence and does not reset retries or synthesize a continuation marker. SIGTERM/ordinary Python exceptions follow this cleanup; SIGKILL or host failure cannot run cleanup, so the unit's own runtime limit remains the bound and coordinator recovery must inspect actual state.

Exit0 means verified current. Reaching the12-invocation cap returns exit2 with `status=invocation_cap`, `current=false`; it does not claim convergence or schedule another session. Any error returns nonzero and records `status=stopped`. Review all actual receipts before any separately authorized next step.

## Offline tests

Tests mock command and file boundaries. They cover clean continuation/current, all receipt rejection conditions, explicit empty unfinished scopes, changed controls during an active unit, a dead launcher with an active service, no-progress and cap behavior, exclusive/private outputs, hash tampering, symlink/nonregular rejection, actual local advisory lock contention, and the exact worker command/environment. They make no source requests and launch no systemd or production jobs.

```sh
cd /tmp/swingset-h16-closure-frozen-tests
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/tmp/swingset-h16-closure-frozen-tests/src:/tmp/swingset-h16-closure-frozen-tests \
/Users/skeswa/repos/skeswa/swingset/.venv/bin/pytest -q /tmp/test_h16_initializer_supervisor.py
```
