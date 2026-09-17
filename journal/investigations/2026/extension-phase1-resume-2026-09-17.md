# Phase-one resume on a frozen extension runtime, 2026-09-17

Status: Implemented locally; source-bound tests and independent review are
recorded below. No production preparation, acquisition, input acceptance,
deployment or publication occurred in this work. No historical year is accepted.

The new [prepare_extension_phase1_resume.py](../../tools/collection/prepare_extension_phase1_resume.py)
packages the exact retained September 17 `resume.py` and original 17-target
scope, plus the new wrapper. The original driver hash is
`1ddbe1ebae293dc7f97ec1de50a809c93588066d4f26ddb5afa9a59359f93752`;
the original gate used only as immutable scope evidence has hash
`c575347878de8ea5181919e26e8607d5d4aa31bfc08c899b82c4c0c25c3ecd98`.
Its old schema and accepted inputs never become current authority.

Build accepts a reviewed frozen source receipt, exact deployed Nix source path
and exact schema number. It verifies every source file and rejects extra files,
symlinks, an altered receipt or a different schema literal. Schemas below 28
are unsupported. It seals three files in `packet.json`; each prepare or run
requires the packet hash and verifies its complete helper closure. The packet
does not itself grant permission to acquire or accept inputs.

Preparation requires the actual production state, the hold, no restore marker,
both schema markers matching the frozen runtime, six inactive ordinary units,
no unsettled execution admissions and the acknowledged public baseline. It
reads SQLite under writer-then-control locks. It independently captures the
expected runtime/environment/configuration/override bundle into a temporary
directory, then checks that exact bundle is already accepted in production.
The retained bundle's manifest and files and all current semantic input rows
must match. Deleted optional overrides may remain as explicit empty digests;
unreviewed accepted inputs fail. Preparation has zero database changes and
writes a new gate only in the real operations directory.

The external configuration and complete CSV override inventory must equal the
frozen files. The calendar source must already be enabled and the history floor
must remain `2010-01-01`. This closes the old mismatch between external paths
and the frozen bundle without accepting, copying over or silently normalizing
production inputs.

Run defaults to preflight. Execution uses the original driver and ordinary
`history.intake.run_intake` / `FetchClient`, under the writer lock. The new
authority checks run at its locked preflight and each ordinary request-debit
boundary. The six-unit check runs at preparation and the locked preflight;
each debit rechecks hold bytes, restore state, schema, accepted inputs,
external files and public baseline, alongside ordinary H13 controls.
Hold/input changes during acquisition return a pause so the target
stays pending. H13 source, host, kind and global pauses, shared budgets, robots,
cooldowns, parsing backpressure, request spacing and source-kind interpretation
policy remain ordinary runtime decisions. No baseline authority is initialized.

The original ceilings remain 48 additional paid Archive requests and a
600-second cooperative intake window, using the smaller current host limit
and no more than 200 Archive requests per UTC day. Robots, redirects and HTTP
retries consume that same limit under the original FetchClient policy. There
is no new retry policy; finding targets are skipped with `retry_failures=False`.
Only the reviewed pending subset of the original 17 calendar captures can
receive requests. The original driver preserves its initial and completion
receipts and requires a fresh continuation gate after ledger changes. Its time
limit is cooperative; the coordinator must provide the original external hard
timeout rather than treating it as a hard HTTP cancellation guarantee.

Existing successful phase-one ledger rows must report the current parser
version. A drained ordinary parse queue alone does not refresh that ledger.
Before this finite resume, separately reconcile stale ledger receipts against
actual replay evidence through reviewed offline intake. This helper deliberately
does not rewrite those receipts or spend its finite target budget replaying old
rows. Findings, missing dates and source warnings remain explicit.

Build without production access:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  journal/tools/collection/prepare_extension_phase1_resume.py build \
  --source REVIEWED_LOCAL_FROZEN_SOURCE \
  --deployed-source /nix/store/REVIEWED_HASH-source \
  --source-receipt extension-source.json \
  --source-receipt-sha256 EXACT_SOURCE_RECEIPT_SHA \
  --schema EXACT_REVIEWED_SCHEMA --output NEW_PACKET_DIRECTORY
```

After independent review, the coordinator stages that packet and uses the
actual frozen runtime and production interpreter. `PACKET` below is a new
immutable helper directory within operations; gates and receipts live outside
it. Shell variables must name the exact reviewed values, not an unpinned alias.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SOURCE/src:$SOURCE" \
  /var/lib/swingset/venv/bin/python "$PACKET/runner.py" prepare \
  --packet "$PACKET/packet.json" --packet-sha256 "$PACKET_SHA" \
  --state /var/lib/swingset --config "$CONFIG" --overrides "$OVERRIDES" \
  --coordinator-reviewed --output "$NEW_GATE"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SOURCE/src:$SOURCE" \
  /var/lib/swingset/venv/bin/python "$PACKET/runner.py" run \
  --packet "$PACKET/packet.json" --packet-sha256 "$PACKET_SHA" \
  --gate "$NEW_GATE" --gate-sha256 "$GATE_SHA" --output "$NEW_PREFLIGHT"
```

Execution adds `--execute` and uses a distinct new receipt path inside the
bounded coordinator service. Preserve the hold and controls throughout; do not
start ordinary collection or infer historical-year acceptance from success.

Offline tests cover packet/source tampering, old schema markers, original target
identity changes, a partial remainder, stale parser receipts, accepted bundle
and input corruption, hold changes, active units, normal controls and actual
mocked intake. A robots request followed by the ordinary 500 retry and successful
capture consumes three paid requests with completion spacing of at least ten
seconds, parses one target and leaves sixteen pending. Backpressure consumes
zero requests. Source pause, input or hold changes after robots stop before a
second request. No test creates a year acceptance.
The full wrapper-to-base-main preflight is also exercised with only the
immutable/live filesystem bindings simulated: it verifies the sealed packet,
actual accepted bundle and schema, reads the disposable database without
changing its bytes, writes a new preflight receipt and never calls intake.

The [frozen runtime 003 check](../../evidence/collection/extension-phase1-resume-2026-09-17/check-001/checks.json)
passed all 31 tests in 9.26 seconds and Ruff with unchanged helper/test hashes.
It built a [schema 28 packet](../../evidence/collection/extension-phase1-resume-2026-09-17/packet-001/packet.json)
with manifest SHA-256
`1c75670ec1dc57e10770cf85a1922c80fbafd49dc47d1ce77f64fbff6114ccb0`.
This is a local packaged artifact; no production gate was prepared. The same
31 tests passed separately against the current schema 29 development runtime,
and helper mypy passed. Those results do not replace a future complete frozen
schema 29 validation or operational acceptance.

[Independent review](../../evidence/collection/extension-phase1-resume-2026-09-17/independent-review-001/checks.json)
passed 31 tests in 8.35 seconds and Ruff with unchanged wrapper, test, retained
driver and scope hashes. It inspected the actual wrapper-to-base preflight,
accepted input authority and ordinary request boundaries. No blocking finding
remains for the prepared helper; production gates and operating receipts remain
separate work.

See [D-0078](../../decisions/0078-bind-phase1-resume-to-frozen-extension-inputs.md).
