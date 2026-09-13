# Pending WP16 + H17 currentness source update

Preparation only: the full worktree test run is still active. No new successful
validation, source assembly, Nix build, deployment or activation is asserted.
The original sfsm pin, source receipt, plan, script and captured verification
artifacts remain unchanged.

The new paths are /tmp/wp16-h17-assembly-plan.json and
/tmp/prepare_wp16_h17_assembly.py. The base remains immutable093 with receipt
3930dbc9f6023250a5253043ade60bd3fc90c57bda27e2c64f8520aa4c588a92.
Configuration, overrides, Nix files, dependencies and all preexisting migrations
must remain byte-equal to that base. The prior helper closure and seven exact
retained CDX/header resources are unchanged.

Exactly three source/test hashes differ from the previous sfsm overlay:

- src/swingset/link/evaluation_input.py
- tests/test_identity_evaluation.py
- tests/test_identity_evaluation_currentness.py (new)

No included documentation bytes changed during central formatting. There are
currently 540 overlay files. The eventual actual validation receipt adds one
more source artifact; it is intentionally absent until the real run succeeds.

The plan has validation_pending=true and null validation receipt/path hashes.
The script rejects that state even in inspection mode. It does not accept a
placeholder or a claimed future pass. Its required_test_count is1399, supplied
by the reviewed plan, instead of the original script's literal1396. Actual
runtime hashes still must match the separately hashed full validation receipt.

After the actual successful run, root may bind the real
research/verification/h17-currentness-worktree-validation-20260913.json path and
hash, add that exact file/hash to overlay, set validation_pending=false and
update overlay_bytes/changed_or_added. Rehash and review the resulting plan
before invoking even the default verifier. A different actual test count needs
explicit review of the actual result and updated hashed plan, never relabeling.

Later assembly must use a new dedicated /var/tmp/swingset-wp16-... directory.
The source receipt continues to say acquisition_enabled=false,
repairs_activated=false, frozen_validation_pending=true and
 deployment_executed=false. Separate frozen-source tests and later operational
receipts remain necessary; none may overwrite the earlier sfsm evidence.
