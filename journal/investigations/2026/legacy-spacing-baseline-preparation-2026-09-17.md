# Legacy spacing baseline preparation, 2026-09-17

The [maintained helper](../../tools/runtime/legacy_spacing_baselines.py) is
implemented and tested offline. It has not prepared or applied a production
baseline in this branch. The coordinator owns actual review and execution.

## Exact supported evidence

The first proof kind covers only the retained eight-request Archive fixture
operation. It checks pinned hashes for the complete receipt, proposal manifest,
execution admissions, preparation receipt and executed transport/wrapper. It
also checks every retained response body and the original H16 `hosts.toml`.

The original Archive floor was ten seconds. The retained robots request returned
HTTP 404 with body SHA-256
`55f7d9e99b8e2d4e0e193b2f0275501e6d9c1ebd29cadbea6a0da48a8587e3e0`.
The reviewed runner gives that outcome no added robots delay. Its receipt names
eight requests, 130,059 bytes and a finish time of
`2026-09-17T15:44:06.251242+00:00`. These are historical retained facts, not a
fresh claim about the live ledger.

The helper requires the actual Archive daily paid ledger and all request
admissions since the run began to match those exact records. Later paid rows,
different admissions or changed protected state leave the candidate stale or
blocked. In particular, a later DCN fixture request means the earlier
eight-request proposal cannot authorize a baseline afterward. New request proof
must first be reviewed and supported explicitly.

The helper now also supports the exact retained
[DCN follow-up](../../evidence/admission/dcn-index-fixture-2026-09-17/quarantine/receipt.json)
with `--dcn-bundle`. It pins its executed wrapper/transport, preparation receipt,
manifest, response bodies, cumulative admissions and subsequent production
observation. The predecessor had eight requests and 130,059 bytes; the two new
requests bring the retained daily total to ten requests and 2,934,701 bytes.
The last exchange completed at `2026-09-17T16:16:12.692906+00:00`. Its newly
retained robots response is the same 404 body, and the executed transport enforces
ten seconds after exchange completion. These exact later controls establish
the original gap for the new baseline proposal; they do not erase the earlier
fixture's spacing findings. The old eight-request proof remains stale at the new
ledger. Any later paid request or admission also blocks the combined proof.

Other paid hosts appear in the proposal with their effective current policy and
cached robots metadata, but their proposed gap stays null. Cached metadata alone
does not establish which robots policy governed their last request. No host gets
an invented original gap or blanket permission to resume.

## Two separate operations

`prepare` takes a writer lock and control lock, checks the operator hold and all
six inactive ordinary units, and reads schema 14 or 28 without migration. It
requires all execution admissions to be settled. The resulting immutable packet
contains source/configuration/helper pins, the observed stopped time, hold hash,
per-host assessment and protected-table fingerprints.

`apply` requires the exact packet SHA-256 and `--coordinator-reviewed`. This
means the coordinator has reviewed a concrete operational packet under existing
authority; it does not invent owner acceptance. The command opens a raw writable
connection only after proving schema 28 under the existing locks. It verifies
the packet against current evidence and then uses
`Gate.establish_spacing_baseline`. Its SQLite authorizer permits only the spacing
records and the API's no-op insertion of an already-existing host.

Host rows, budgets, operator pauses, control revisions/events and execution
admissions must remain byte-for-byte equivalent as table receipts. The helper
never reduces `next_allowed_at`, refunds paid requests, changes pauses or removes
the operator hold. Ordinary acquisition must still start and complete a fresh
monotonic wait. Reapplying an unchanged packet returns its existing baseline ID;
a different spacing authority is refused.

Both modes require these explicit paths and pins:

```text
--state <held-state>
--source <reviewed-schema28-runtime>
--source-receipt <runtime>/extension-source.json
--source-receipt-sha256 <reviewed-runtime-receipt-sha256>
--helper-sha256 <separately-frozen-helper-sha256>
--config <reviewed-effective-config-directory>
--hosts-sha256 <hosts.toml-sha256>
--sources-sha256 <sources.toml-sha256>
--fixture-bundle <retained-fixture-exception-2026-09-17-directory>
--dcn-bundle <retained-dcn-index-fixture-2026-09-17-directory>
--prior-hosts <original-H16-runtime>/config/hosts.toml
--output <new-output-file>
```

`--dcn-bundle` is optional only when assessing the historical eight-request
ledger. Current ten-request evidence requires it. Apply additionally takes
`--proposal`, `--proposal-sha256` and
`--coordinator-reviewed`. Run with `PYTHONDONTWRITEBYTECODE=1` and `PYTHONPATH`
bound to the reviewed runtime's `src` directory. The fixture bundle uses the
retained repository layout with `packet/`, `quarantine/` and
`execution-admissions.json`. The original H16 host file is pinned to
`6c0b8d7b34e1c4a068374c59d0f41d52459f0113883b9868a6750c2753123122`.

## Offline checks

The earlier twelve-test receipt remains retained at
[offline-check-001](../../evidence/runtime/legacy-spacing-baseline-2026-09-17/offline-check-001/checks.json).
The current source-bound
[offline-check-002](../../evidence/runtime/legacy-spacing-baseline-2026-09-17/offline-check-002/checks.json)
passed sixteen tests in 0.91 seconds against frozen runtime 003, plus Ruff and
mypy. Helper and test hashes were unchanged throughout the run.
The expanded tests cover
read-only preparation on actual schemas 14 and 28, real retained fixture body
verification, a separately migrated 14-to-28 packet, pre-migration refusal,
protected budgets/controls/deadlines, same-packet idempotence, later requests or
admissions, changed policy/robots, unsettled controls and a malicious budget-write
trigger rejected by the authorizer. The unknown host stays blocked after apply.
Four additional tests cover the actual combined DCN evidence, stale predecessor
rejection without refunds, missing controls, and later admissions with offset or
invalid timestamps. These tests create temporary databases and make no requests.

An independent review found that an allowed spacing-table write from a SQLite
trigger could establish a second, unreviewed host reservation. The authorizer now
requires direct writes (`origin is None`), and a regression verifies that such a
trigger is denied and its transaction rolls back. The source-bound
[independent review](../../evidence/runtime/legacy-spacing-baseline-2026-09-17/independent-review-001/checks.json)
passed seventeen tests and Ruff with unchanged helper and test hashes. It found
no remaining blocker in the reviewed helper; applying a concrete live packet
remains a separate coordinator operation.

The coordinator still must freeze the reviewed helper,
generate a fresh live proposal, resolve any newer request-evidence gap, and
retain an application receipt if a host is eligible. No deployment, activation,
publication or production calibration follows merely from these offline checks.

See [D-0062](../../decisions/0062-bind-legacy-spacing-baselines-to-retained-requests.md).
