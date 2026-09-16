# H16 closure production release — prepared only

`/tmp/h16-closure-production-release.py` is a new copy of the reviewed release driver. Only `SOURCE`, `SOURCE_RECEIPT`, and `BUNDLE` changed. It has not been executed against production; no gates or success receipts have been created. The original driver and notes remain intact.

Fixed authority:

- State: `/var/lib/swingset`, existing schema 14 only.
- Source: `/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source`.
- Source receipt SHA256: `f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f`.
- Accepted bundle: `558bb5f4e88b47fe80a691254d3b21ac9e491296e80bb18f4599b64a88bfe9c0`.
- Driver SHA256: `9da822db06cd1fcb4d0119706b7c91bc6e128e1447414b817f6b91ff703a51ca`.
- Private operation directory: `/var/lib/swingset/operations/h16-closure-release-20260914`.

Run only after coordinator review and actual prerequisite receipts exist. Use the `swingset` service account, `/var/lib/swingset/venv/bin/python`, `PYTHONPATH=/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source/src:/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source`, and `SWINGSET_REVISION=uncommitted:2d5q3lgljfkmm78hf44g954lzwx79yhv-source`. Preserve the existing GCC/zlib library paths and private credential setup. Keep all six scheduled services/timers held. Do not import the working tree runtime.

## Required evidence and preserved guards

Separate private `build` and `publish` gates retain format `h16-production-release-v1-gate`, exact operation authorization, reviewer attribution, aware review time, fixed pins, and the new driver hash. Every evidence reference binds a regular file's path and SHA256; symlinks, changed evidence, reused outputs and overlap with reviewed inputs are rejected.

Both gates require the reviewed initializer driver, its exact gate and immutable marker, and an actual completed production initialization receipt: `status=current`, empty unfinished scopes, matching driver/gate/marker/source hashes, and all source/control/input/parse preservation checks true. The initializer revalidates its real same-pin scratch replay, scratch build, substantive audit, predecessor checkpoint and baseline evidence. Scratch progress is not production initialization.

Build mode also rechecks actual desired/materialized project and link work, including lost queue hints, under exclusive writer ownership. It verifies schema, holds, baseline, source evidence, extractor cache, accepted inputs, controls and active work. It captures without accepting inputs and calls ordinary closure build through the normal admission gate. It performs no derivation, parse, fetch, publication reconciliation or remote request. Candidate file/proof and semantic publication checks must pass; preservation digests must remain unchanged.

Publication additionally requires the exact reviewed build gate, successful actual production build receipt and independent substantive audit binding the production candidate path/ID/manifest and baseline parent. All required table-count, structural, coverage, identity/support/journal/default-ID, placement and named null-ID judge checks remain mandatory. Missing or failed checks are rejected. The audit script bytes are separately hash-bound by the reviewed gate; the driver does not generate or upgrade its own audit.

## Invocation shapes — do not execute during preparation

After the respective actual reviewed gates exist, use fresh output filenames:

```sh
/var/lib/swingset/venv/bin/python /tmp/h16-closure-production-release.py build \
  --gate /var/lib/swingset/operations/h16-closure-release-20260914/build-gate.json \
  --output /var/lib/swingset/operations/h16-closure-release-20260914/build-001.json

/var/lib/swingset/venv/bin/python /tmp/h16-closure-production-release.py publish \
  --gate /var/lib/swingset/operations/h16-closure-release-20260914/publish-gate.json \
  --output /var/lib/swingset/operations/h16-closure-release-20260914/publish-001.json
```

Supply `HF_TOKEN` only through the existing private service environment, never command text, gates or receipts. The repository remains fixed to `skeswa/swingset`. Publication uses normal current-correction checks, remote-parent verification, admission, semantic fences, late-pause handling and remote file verification. No control is cleared and no publication receipt is manufactured.

For a lost response, retain the same reviewed gate and candidate, add `--resume` to `publish`, and choose a new receipt such as `publish-002.json`. Only that candidate's abandoned publication may be reconciled. A landed commit is verified without a second submission; a proven unlanded attempt still passes the ordinary publication boundary. Unrelated intents, active work, remote heads or changed authority require coordinator review. A held or unpublished `unchanged` result is not success. Inspect actual `published` and `publication_receipt` fields after any interrupted response.

Use one transient unit per operation with `User=swingset`, memory accounting, `RuntimeMaxSec=3600` and normal SIGTERM stop. Monitor anonymous memory separately from reclaimable cache; the coordinator must stop cleanly above 6 GiB anonymous memory on the 8 GiB VM. Do not overlap heavy jobs. The driver records a durable running receipt before expensive work and a final receipt on ordinary exceptions/SIGTERM. A hard kill may leave a running receipt or admission; use normal recovery, not invented completion.

## Offline validation

Tests: `/tmp/test_h16_closure_production_release.py`, with only the imported driver path changed from the original tests. Run from `/tmp/swingset-h16-closure-frozen-tests` using explicit `PYTHONPATH=/tmp/swingset-h16-closure-frozen-tests/src:/tmp/swingset-h16-closure-frozen-tests` and host `/Users/skeswa/repos/skeswa/swingset/.venv/bin/pytest`. Tests use disposable local databases and a fake Hub. Exact results and file hashes are recorded in `/tmp/h16-closure-production-release-preparation.json`; log: `/tmp/h16-closure-production-release-guards.log`.
