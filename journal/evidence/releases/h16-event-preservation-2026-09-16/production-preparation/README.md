# H16 event-preservation production preparation

Prepared offline on 2026-09-16. The owner has authorized deployment and publication after H16 acceptance. This packet does not execute those actions or create successful prerequisite receipts. Root coordinates the gates and owns the current status/handoff. Old drivers, markers, checkpoints and failed builds are preserved.

## Exact authority

- Source: `/nix/store/z689qy41inndill3d92ym8im852x3649-source`.
- Source receipt: `0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb`.
- NixOS system: `/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
- Accepted replay bundle: `fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a`.
- Replay: `/var/tmp/swingset-h16-event-preservation-replay`; scratch marker SHA `a2eb6255beddabe1917808da3916e219433fb9bf7fc85dd6427d9e28bad1a1e2`.
- Independent rehearsal build: `/var/tmp/swingset-h16-event-preservation-build`.
- Production state: `/var/lib/swingset`; schema 14, no migration.
- New private operation directory: `/var/lib/swingset/operations/h16-event-preservation-release-20260916` (not created by this preparation).

`adaptation-manifest.json` records exact predecessor hashes, prepared hashes and every literal replacement. `initialize.py` is byte-identical to the reviewed b54c02a1 driver. `production-release.py` changes only source, source receipt and bundle. `supervise-initialization.py` changes those pins and the operation directory, retaining v2 launch-interruption cleanup. `activate-system.py` changes source, receipt, target system and operation directory. Its expected OLD system is deliberately unchanged and fails closed on drift.

The copied tests change only imported driver paths, matching pins and the activation-driver hash assertion. `offline-guards-001.json` and its log record 105 passing tests against the exact frozen host mirror; no VM or production jobs ran. These are guard tests, not operational acceptance.

## Execution phases for the coordinator

1. **Finish the exact same-pin scratch evidence.** Require the final actual replay receipt with `status=current`, empty unfinished project/link inventory, no running attempts, and released writer lock; retain independent replay verification. Then run the already prepared serial build/audit launcher. The actual ordinary build must commit its build generation within the existing 45-second write bound, and the independent substantive audit must pass for that exact candidate ID, manifest and build state. Do not substitute diagnostic reconstruction, an older-pin replay, a BUILT marker without durable completion, or the previous failed candidate audit. Actual judge preservation, event/registry association and identity checks remain mandatory.

2. **Rebind and execute production preflight.** Stage reviewed files under the new private directory, service-owned mode0700 with private files. Use the frozen `research/accept_h16.py`, SHA `8ed4b2384fb946100934a14b57025b6cc6272fb7927632eaede5c19eb2519f50`. Create a fresh operational gate binding the new source receipt and actual coordinator review, preserving the existing exact V4/private-backup evidence below. Run its non-execute preflight to a fresh `preflight.json`. No new gate is manufactured in this packet. The helper verifies checkpoint bytes, live protected-state equality, schema14, baseline, no active intent and actual persistent holds. Do not bypass any mismatch or replace the required predecessor with a convenient newer backup.

3. **Activate while retaining holds.** Verify both active and persistent system links still equal the expected OLD `/nix/store/r29kapzbjk0ch5h0vkk288jd47dvg90j-nixos-system-swingset-lxc-25.11.20260630.b6018f8`. If they differ, stop and review the actual predecessor before adapting a new driver; do not silently change OLD at launch. Place exact same-pin successful `scratch-build.json` and `scratch-audit.json`, plus `gate.json` and `preflight.json`, in the operation directory. Root runs `activate-system.py` without Python optimization. It writes an exclusive deployment intent before changing the persistent profile/activating and always attempts to stop all six cycle/backup/summary services and timers. Require its actual successful final receipt and both system links. It never removes `operator-hold`.

4. **Read-only acceptance of the active pin.** Run frozen `research/accept_h16.py --execute` with the same actual operational gate and new output `acceptance.json`. Despite the flag, this H16 helper performs read-only acceptance: require executed=true, passed=true, migrated=false, service_executed=false, zero requests and identical before/after protected state. It checks doctor/restart/currentness without running derivations. This is separate from preflight and deployment.

5. **Prepare actual production initialization once.** Build a new `initialization-gate.json` with format `h16-production-initialization-gate-v1`, exact new source/receipt/bundle and unchanged initializer hash, stages project/link, explicit capture acceptance authority, actual `production_acceptance`, `preflight_gate`, `scratch_replay`, `scratch_build`, `scratch_audit` paths and every evidence hash. Use both config and overrides from the frozen source, matching replay; a differing accepted bundle must fail. Include the operational gate's nested backup evidence with preserved paths/hashes. Run service-owned `initialize.py prepare` to new `initialization-marker.json` and `prepare-001.json`. The marker durably binds pre-acceptance source/control/baseline evidence and prepared input authority. Preparation may legitimately invalidate extractor labels and enqueue parse work when accepting the new runtime; it never executes parse work. Do not copy scratch generations or reuse another source's production marker.

6. **Bounded production project/link initialization.** After successful preparation, root launches `supervise-initialization.py` with the actual immutable gate, marker and unchanged driver paths plus their separately reviewed hashes. It runs at most 12 service-owned 900-second workers with RuntimeMaxSec=1500, 20-second monitor boundaries, unchanged 45-second atomic limits, and exclusive per-batch receipts. Continue only clean exit0 bounded_stop with positive completed count, attempted==completed, successful outcomes and all preservation/authority/hold checks intact. No retry reset, prepare, continuation, acceptance, fetch, parse, build or publication is part of this supervisor. A cap, stop, changed control or error is not current. After actual current, independently verify empty unfinished scopes, no running attempts/admissions and released writer lock. A second bounded session is only appropriate after reviewing a clean cap receipt and retaining the same authority. A real control change uses the initializer's separately reviewed continuation path and a new chained marker; the supervisor does not create it.

7. **Actual production build and independent audit.** Create a build gate for `production-release.py` binding the same initializer driver/gate/marker and actual current production initialization receipt. Run build mode to `build-001.json`. It recaptures but does not accept inputs, independently checks desired/materialized currentness, then calls the normal closure build under controls. Audit this actual production candidate separately using the reviewed substantive audit driver; bind its exact script hash, state, candidate, manifest and baseline parent. Scratch candidate evidence cannot substitute for this production audit. Keep the 6 GiB anonymous-memory stop monitor and serialize heavy VM work.

8. **Publication and acknowledgment.** Create a distinct publish gate binding the exact successful build gate/receipt and the actual production audit plus audit-driver hash. Run publish mode to `publish-001.json` using existing private HF credentials and fixed repository `skeswa/swingset`. Ordinary remote-parent, late control, source/identity/suppression and file-verification guards remain. Claim publication only from actual remote verification and the publication receipt/baseline acknowledgment. A held or unpublished unchanged result is not success. After a lost response, preserve the candidate/gate and use `--resume` with a new output only for that candidate's existing intent; do not submit another candidate or invent acknowledgment. Schedule/acquisition holds remain in place through this release packet; it does not authorize H17 expansion, schema21 deployment or background source requests.

## Retained checkpoint and baseline identities

The frozen operational helper intentionally requires the verified H15 predecessor checkpoint `/var/lib/swingset/checkpoints/h15-before-h16-20260913`, manifest `700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444`, private backup commit `c656e88c775b23ae5879661924d57fa1f93cfa7d`. Original evidence is `/tmp/h16-closure-operation-preparation-20260914/{backup.json,private-verification.json,v4-verification.json}` with hashes recorded in its old gate and `preparation.json`. Retained verification is not a new remote backup check. Checkpoint readers remain `mode=ro&immutable=1`; no sidecars or files may be added.

V4 baseline is commit `81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`, candidate `cand_7f8cf9bcbf7e4a60`, manifest `87ac286a1ae452f09f7e5cea646f260ac34035f3d05bb83de17c7d9cc8f08884`. Production preflight/initializer must establish that it remains the actual baseline. Backup/restore acceptance beyond this preserved predecessor is separate work; this preparation creates no new backup requirement.

## Launch environment and command shapes

Use `/var/lib/swingset/venv/bin/python` and the exact source for PYTHONPATH=`SOURCE/src:SOURCE`, SWINGSET_REVISION=`uncommitted:z689qy41inndill3d92ym8im852x3649-source`, and PYTHONDONTWRITEBYTECODE=1 for both workers and root supervisor. Set LD_LIBRARY_PATH to `/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib`. Do not import main's schema21 runtime. Never put token values in commands, gates or receipts.

Below, OPS means the new private operation directory; replace it with that actual absolute path in the service/root environment. These are shapes, not executed commands:

```text
python SOURCE/research/accept_h16.py --state /var/lib/swingset --source SOURCE --source-receipt-sha256 SOURCE_RECEIPT --gate OPS/gate.json --output OPS/preflight.json
python OPS/activate-system.py
python SOURCE/research/accept_h16.py --state /var/lib/swingset --source SOURCE --source-receipt-sha256 SOURCE_RECEIPT --gate OPS/gate.json --output OPS/acceptance.json --execute
python OPS/initialize.py prepare --gate OPS/initialization-gate.json --marker OPS/initialization-marker.json --output OPS/prepare-001.json
python OPS/supervise-initialization.py --gate OPS/initialization-gate.json --gate-sha256 ACTUAL_SHA --marker OPS/initialization-marker.json --marker-sha256 ACTUAL_SHA --driver OPS/initialize.py --driver-sha256 b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233 --session reviewed-001 --max-invocations 12
python OPS/production-release.py build --gate OPS/build-gate.json --output OPS/build-001.json
python OPS/production-release.py publish --gate OPS/publish-gate.json --output OPS/publish-001.json
```

Use the existing service-account/transient-unit and external memory-monitor procedure for initializer preparation/build/audit/publication. Initializer prerequisites/final streaming hashes are outside its worker deadline; the supervisor provides the larger external bound. Supervisor root environment is explicit because its own authority checks import frozen runtime. Activation is root and its assertions require ordinary Python, never `-O`.

## Still unresolved by offline preparation

- The running replay's final current receipt and independent verification, followed by the same-pin ordinary build/audit outcome and exact final candidate hashes.
- Actual live active/persistent predecessor, all six inactive units, operator-hold, current controls, schema, baseline and protected-state equality to the required H15 checkpoint. This packet read no live state.
- Actual production gates, immutable prepared marker, initialized currentness and production candidate/audit. No placeholders here are executable authority.
- External overrides in generated service units still point to `/Users/skeswa/repos/skeswa/swingset/overrides`; initialization/release gates use frozen override bytes. Do not resume scheduled units or accept a differing external override bundle incidentally.
- Current private credential availability and remote parent at publication time. No network or credential inspection occurred during preparation.
