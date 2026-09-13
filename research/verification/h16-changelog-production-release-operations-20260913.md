# H16 production release driver for coordinator review

`/tmp/h16-changelog-production-release.py` has not been executed against production. It has two separate modes: `build` and `publish`. Both require actual reviewed evidence; this preparation creates no future gates or success receipts.

The driver fixes these values:

- State: `/var/lib/swingset`, existing schema 14 only.
- Runtime: `/nix/store/093lylp4naslq5yb2mygbkkgkbsar3ym-source`.
- Source receipt SHA256: `3930dbc9f6023250a5253043ade60bd3fc90c57bda27e2c64f8520aa4c588a92`.
- Accepted bundle: `9370ccc35058661037c78b2dbcea797932dce1e4211638826149661a4e37e683`.

Run as the `swingset` service account. Set `PYTHONPATH` to the fixed source's `src` directory followed by its source root, and `SWINGSET_REVISION=uncommitted:093lylp4naslq5yb2mygbkkgkbsar3ym-source`. Use the existing VM virtualenv, standard GCC/zlib library paths and private publication credential setup. Do not run with the working tree's schema-15 package on the import path. The initializer's existing verifier checks both the complete frozen source inventory and imported schema/runtime locations before any migrating opener is reached.

## Gate construction after real prerequisites exist

Each gate is private JSON with format `h16-production-release-v1-gate`, `mode` equal to its one authorized operation, `authorized: true`, `reviewed_by`, timezone-aware `reviewed_at`, and the exact `state`, `source`, `source_receipt_sha256`, `input_bundle_hash` values above. `driver_sha256` hashes the actual reviewed release driver bytes.

Every evidence reference is an object containing `path` and `sha256`. Paths may be absolute or relative to the gate directory. The following references are required in both gates:

- `initializer_driver`: the separately reviewed actual production initializer file. It retains its own original `__file__` hash authority when imported.
- `initialization_gate`: the initializer's exact gate. Its existing validation checks verified H15 checkpoint/V4 baseline, actual same-pin full scratch replay, full scratch build, substantive scratch audit and exact accepted-input authority.
- `initialization_marker`: the actual immutable preparation marker, including any reviewed continuation chain. The initializer validates that chain; the release driver does not invent a new control basis.
- `initialization_receipt`: the actual final production `run` receipt with status `current`, empty `unfinished_by_scope`, finished time, exact gate/marker/driver/source hashes, and all source/control/input/parse preservation checks true.

The publish gate additionally references:

- `build_gate`: the reviewed build-mode gate with the same initialization-chain paths and hashes.
- `build_receipt`: a successful actual production build receipt from this driver, linked to that build gate and unchanged source/bundle/driver. A scratch candidate receipt is rejected.
- `audit`: the independent substantive audit of that production candidate. It must bind `/var/lib/swingset`, the exact candidate path/ID/manifest hash and original V4 parent. Every emitted check must pass. The driver requires all table row-count checks, structural uniqueness/no-orphan checks, every substantive identity/default/reference/journal/null-judge check, all 4,931 named baseline judges, 34,955 baseline default entry IDs, registry occurrence mapping, 17-year coverage, no unreviewed accepted years, actual coverage counts, and the 66 original coverage-row transitions.
- `audit_driver`: the separately reviewed audit script bytes. The gate's reviewer attests that the referenced audit receipt came from this script. The current audit format does not contain its own executable hash; the gate supplies that binding explicitly.

The driver never generates its own substantive acceptance audit or upgrades a partial positive report to a full review. Freeze/copy all referenced scripts and receipts before hashing gates. Retain scratch prerequisite artifacts because the initializer revalidates their immutable files and committed generation proof.

## Build mode

Invocation shape, after the reviewed build gate exists:

```sh
/var/lib/swingset/venv/bin/python /tmp/h16-changelog-production-release.py build \
  --gate "$h16_reviewed_build_gate" --output "$h16_new_build_receipt"
```

The driver takes the existing exclusive state writer lock, verifies schema 14 without migration, checks all six persistent service/timer holds, the initialized V4 baseline, original accepted-input authority, extractor-cache state, source evidence and operator-control hashes. Actual project/link desired-versus-materialized checks must also be current; the earlier receipt alone is insufficient. Parse retry tokens may remain unresolved, consistent with H16 closure selection.

It captures the same bundle without accepting it, starts one run, and calls ordinary `build_release(remote=None, correction_only=False)` inside the existing H13 build-operation admission. It performs no derivation, parse, fetch, input acceptance, publication reconciliation or remote request. The real builder retains proof and completes the artifact generation. Full candidate file verification and semantic publication preflight must pass before the build receipt is successful. The baseline and source/control/input/parse/revision digests must remain unchanged.

The candidate is not authorized for publication by a build receipt alone. Run the independent substantive candidate auditor serially after the build, then construct the publish gate from those actual results.

## Publish mode and lost-response recovery

First invocation:

```sh
/var/lib/swingset/venv/bin/python /tmp/h16-changelog-production-release.py publish \
  --gate "$h16_reviewed_publish_gate" --output "$h16_new_publish_receipt"
```

The publication credential must be supplied through the existing private service setup as `HF_TOKEN`; do not put the token in command text, a gate or an operation receipt. The repository is fixed to `skeswa/swingset`.

The driver checks the same initialized source/input/control/baseline authority, exact reviewed production candidate files, ordinary closure mode and committed build proof, and rejects any unrelated active work or publication intent. Before a fresh submission it validates current corrections/support and verifies the actual remote parent. It calls normal `publish`, which uses `publication_boundary`, H13 admission, semantic fences and remote commit/file verification. A late pause remains effective. The driver never writes operator controls, clears a hold or manually manufactures a publication receipt.

After an interrupted/lost response, retain the original gate and candidate and use a new output receipt:

```sh
/var/lib/swingset/venv/bin/python /tmp/h16-changelog-production-release.py publish --resume \
  --gate "$h16_reviewed_publish_gate" --output "$h16_new_resume_receipt"
```

Only the reviewed candidate's pending publication may be recovered. Exclusive writer ownership is obtained first; normal admission recovery converts an abandoned active publication to uncertain. Normal `reconcile(dry_run=True)` verifies a landed commit and promotes its exact receipt without resubmission. If it proves unlanded, submission still passes through ordinary `publish` and its fresh publication boundary. An unrelated remote head, unrelated pending candidate or other active work is rejected. An already promoted reviewed candidate is verified against its actual remote head and files, then normal publication returns unchanged. Source/input/control changes require coordinator review outside this unchanged-gate driver; the driver does not silently rewrite authority to continue.

A paused submission leaves its normal pending intent intact and does not count as successful publication. A post-submit preservation failure can coexist with a remotely published candidate; always inspect the explicit `published` and `publication_receipt` fields and use normal reconciliation. Do not infer that an interrupted operation means no remote commit happened.

## Bounded operation and memory observation

Use one transient systemd unit per invocation with `User=swingset`, `MemoryAccounting=yes`, `RuntimeMaxSec=3600`, normal SIGTERM stop and the fixed runtime environment. Keep the persistent six collection/repair units held. Do not launch another heavy job during the build. This driver records PID, systemd invocation ID, start/end, elapsed seconds and process maximum RSS; it writes a durable `running` receipt before expensive work and a final receipt on ordinary exceptions/SIGTERM. A kernel kill can leave that running receipt; candidate files and proof are interpreted by normal builder/publication recovery, never by inventing success.

The coordinator monitors the unit's `MainPID`, `/proc/<pid>/status` RSS/anonymous memory, cgroup `MemoryCurrent`/`MemoryPeak`, CPU and journal. Distinguish reclaimable cache from RSS. Alert before RSS approaches 6 GiB; a controlled stop is an operational coordinator decision, not an automatic broad kill in this driver. Persistent build admissions after a hard kill require normal coordinator recovery before another build invocation; this driver does not settle unrelated abandoned work.

All outputs must be new files beneath private `state/operations`, outside symlink paths and reviewed inputs. Each invocation retains its own receipt. No evidence, marker, prior receipt or published candidate is overwritten. The driver receipt records only error type and aggregate/digest evidence. Runtime tracebacks stay in the private unit journal; do not publish that diagnostic output.

## Focused validation

The final tests run from `/tmp/swingset-h16-changelog-tests` with `PYTHONPATH=/tmp/swingset-h16-changelog-tests/src:/tmp/swingset-h16-changelog-tests` and the host repository’s `.venv/bin/pytest -q /tmp/test_h16_changelog_production_release.py`. This frozen mirror exercises H16 schema 14 rather than future worktree schema 15. The suite exercises changed pin/bundle/gate authority, actual current initialization requirements, evidence hash/symlink rejection, incomplete/failed substantive audits, exact build-gate descent, unrelated active/pending work, real lost-response recovery without a second commit, a real H13 publication pause, changed corrections before remote access and a changed remote parent. Tests use local disposable databases and a fake Hub; no production or source requests occur.
