# Schema14 fixture runner002: offline review packet

This packet is preparation only. It makes no owner authorization, publication-readiness,
quota-capacity, staging, execution, or production acceptance claim. No network/VM request
was made. All execution tests used httpx.MockTransport and disposable local schema14 state.

## Exact runtime and helpers

The runner targets `/nix/store/z689qy41inndill3d92ym8im852x3649-source`, schema14,
source receipt `h16-source.json` SHA256
`0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb`.
The host mirror contains all638 receipt files with matching hashes. Installed host test
dependencies are not claimed to be a deployed interpreter/dependency receipt.

`helper-closure/closure.json` pins every supplemental helper and evidence file. The
stdlib bootstrap checks that receipt before importing helpers, hashes every declared file,
and rejects undeclared files (including bytecode/extensions) and all symlinks. Imports of
Swingset remain bound to the frozen runtime. No schema23/runtime source is in the bundle.

The two helper deltas change only module imports and retained-evidence lookup: old
research paths resolve inside the separately verified closure. The exact five-body/CDX
manifest and evidence bytes are unchanged. Transport, limits, robots, redirects,
classifications, no-retry rules, authorization matching, and byte reservation/refund
remain the existing code. Three delta patches make review explicit.

## Gates preserved and tightened

The existing H13 wrapper keeps per-resource operations, per-issued-request admission,
all/source/host/requirement-kind pauses, draining settlement, writer lock, RESTORE_PENDING,
restricted SQLite authorizer, original POSIX deadline, and grant-day accounting.
Only control lifecycle tables and existing host accounting can change. There are no
watches, snapshots, source generations, input acceptance, migration, or parser calls.

The manual operation requires the scheduled-service `operator-hold` file to remain present
before work and every debit. It never removes the hold or bypasses DB pauses. Main sets
umask077 before output creation. Quarantine remains fresh, separate, and single-use.

A new coordinator gate must have format `fixture-h13-coordinator-v2`, exact driver hash,
helper_root (the actual sibling helper-closure path), helper_manifest_sha256, source,
source_receipt, source_receipt_sha256, schema14, state, baseline_path, published_sha256,
and manifest_sha256. Both baseline files and imported source are checked under the
writer lock. Schema must equal14, the gate, and the frozen runtime's SCHEMA_VERSION.
No gate or owner authorization record is manufactured in this packet.

Only after owner approval and independent engineering review may an operator prepare
actual pins and a current execution window. Root still owns scheduling after production
work; this packet grants no overlap with running workers. Do not substitute main23,
relax the exact schema check, or use the base helper without the H13 wrapper.

## Offline validation

`offline-tests-003.log`:63 passed. Covers44 adapted existing transport/H13 tests plus
frozen receipt verification, exact code/helper pins, extra bytecode/extension/data files,
symlinks, schema23 rejection, missing approval before DB/HTTP, hold removal, preserved
received body and settled admissions. Source/host/kind pauses, process crash charges,
short deadline handling, redirects, budgets, and forbidden domain writes remain covered.
`offline-pyflakes-002.log`:Ruff F checks passed with F401 ignored for retained test imports.
All Python sources also compile. No full repository test or formatting run was requested.

Reproduce from this packet with the project venv Python, PYTHONDONTWRITEBYTECODE=1,
and PYTHONPATH containing helper-closure plus the exact host mirror's src and root:
`pytest -c /dev/null -q -p no:cacheprovider tests`.

Review limitations: production state/baseline, VM dependency closure, available archive
quota, current owner authorization, and actual final deployment paths were not inspected.
Production capture remains pending. The old retained operational driver is unchanged.
