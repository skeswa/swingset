# Independent extension postmigration check, 2026-09-17

The read-only production check passed at `2026-09-17T17:06:23.010331+00:00`.
The coordinator's migration receipt reports completed schema 14→28 execution at
`16:59:15.856849+00:00`. Its SHA-256 is
`9ed991d1389d36d36aa541f45fcd3bcdd2367b18206a429a357d62f079e8b636`.
This verifies deployment and migration under hold; it does not accept ordinary
activation, operating calibration or a subsequent publication.

The [independent receipt](../../evidence/runtime/event-extension-2026-09-17/independent-postmigration-001/check.json)
compares all 71 predecessor table inventories in the pinned completed migration
receipt with the resulting inventories and both rehearsal receipts. All match;
`meta.schema_version` is the documented fingerprint exception and was checked
separately as 28. The live schema contains the expected 116 application tables.
The verifier freshly hashes seven critical live tables: metadata, hosts, paid
usage, pauses, control state/events and execution admissions. All match the
completed migration receipt. It does not repeat the full database hash scan.

The active and persistent system both resolve to
`/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
The source receipt matches frozen runtime 003,
`/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source`, with receipt SHA-256
`60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6`.
Gate, helper, source/schema module and upstream evidence hashes also match.

The operator hold hash is unchanged and all six ordinary cycle, backup and
summary service/timer units remain inactive at both check boundaries. No
execution admission is unsettled. No spacing baseline has been applied. The
Archive paid ledger remains ten requests and 2,934,701 bytes on September 17.
The acknowledged local public baseline remains `cand_8f31cad7226643ae` at commit
`2a6c7dc744fb36eabb5163c0a527d787d3721f4f`; its `PUBLISHED` file matches the
checkpoint. No remote-head network query was performed.

The separately retained [verifier](../../evidence/runtime/event-extension-2026-09-17/independent-postmigration-001/verify.py)
ran through OrbStack as root using the explicit existing
`/var/lib/swingset/venv/bin/python -B -`. SQLite was opened with `mode=ro` and
`query_only`, and its connection reports zero changes. The
[invocation receipt](../../evidence/runtime/event-extension-2026-09-17/independent-postmigration-001/invocation.json)
pins the verifier and output. This operation performed no source requests,
production writes, input acceptance, migration, baseline application, worker
activation or shared-environment modification.
