# Exact DCN results metadata runner, 2026-09-17

The [builder](../../tools/admission/build_dcn_results_lookup.py) produces a new
metadata-only fixture packet for the [exact proposed lookup](dcn-results-locator-proposal-2026-09-17.md).
It is implemented and tested offline. These checks establish no acquisition,
source-kind activation, production input acceptance or publication. The
owner approved the exact scope in [D-0069](../../decisions/0069-approve-exact-dcn-results-metadata-lookup.md).
This runner receipt remains separate from that permission.

## Source and authority

The runtime is `/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source`, schema 28,
with source receipt
`60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6`.
Building verifies that entire inventory, including the maintained fixture
helpers. The original H13 wrapper is separately pinned at
`81357b33c8862bdc4bdd2d84f2479a7913a9d783b00b435910cc2328bdcf69f7`.
Explicit guarded substitutions produce the new wrapper and helper closure;
no old packet is changed. The builder also verifies the source-printed locator
body and both retained CDX evidence files against the exact proposal SHA
`c982f5124b396783eff8b4565600358127667e85f51a96a94255190e4a26734c`.

Only these requests are possible: Archive robots if no valid cache exists, the
exact page-count query for the normalized Riga Summer Swing results tab, and
page zero if the probe reports at least one page. Further pages remain
unexamined. The packet grants zero archived-body, PDF or origin requests, zero
redirects, zero automatic retries, and zero child watches. Limits are three HTTP
requests including robots, 2 MiB per response, 4 MiB total and 900 seconds.
The separate authorization window is also limited to 15 minutes.

## Runtime adaptation

The copied wrapper retains H13 admissions, the exclusive writer lock, restore
interlock, source/kind/host pauses, and the service hold. Its limited SQL writer
permits ordinary host usage/spacing and control lifecycle bookkeeping only.
Paid requests and byte reservations use the shared UTC-day budget, capped at the
existing 200 requests; no quota is reset or enlarged.

The old schema-14 wrapper temporarily substituted a clock exposing only wall
time. That is incompatible with schema-28 spacing recovery. The new wrapper
keeps the actual clock and uses `Grant.debited_at` for the reservation/receipt
request day. Midnight tests verify that post-debit dispatch delay does not move
paid usage to another day.

A copied completion anchor adds both UTC and monotonic receipt timestamps and a
full initial gap. The new ordinary gate separately retains durable completion
spacing. No test or operation treats a grant timestamp as actual transport time.
An unknown paid legacy host remains blocked; only a separate reviewed baseline
operation may establish its first safe wait. Cached robots are retained and
checked; a future-dated cache is not trusted. No conditional body request is
needed because this packet has no body targets or previous CDX representation.

Response accounting follows the [fetching contract](../../../docs/reference/fetching.md):
bytes exposed by the response-body reader, not packet-level wire measurements.
The runner rounds each available reservation down to a 64 KiB multiple. It
counts every delivered chunk without slicing; it never reads another chunk or
probes for EOF after the cap. An exactly full reservation is therefore incomplete
unless completeness was already established without another read. Any incomplete
response keeps its entire conservative byte reservation; observed body length
and paid bytes are separate receipt fields. A remaining capacity below one
chunk stops before HTTP without refunding the already-paid grant. Transport
buffering is explicitly unmeasured. These choices never enlarge a budget.

## Packet and invocation

The reviewed candidate is `/tmp/swingset-dcn-results-lookup-20260917-dev007`.
Its generated pins are:

| Artifact                 | SHA-256                                                            |
| ------------------------ | ------------------------------------------------------------------ |
| Exact canonical manifest | `ad3f8584d2f540bab4967c36e1730e91e364f2aeeef19cf40273e3deb5bfe0f6` |
| Driver                   | `606b95e653e212d2ddb88afea3da77bb4da606068ce609de45d93cdce8b49d80` |
| Helper closure manifest  | `09b6ee0bf55c83bbd3ad29fb42b2d5dbee0050ce4d7cf81ce7e74b569ab92e71` |
| Preparer                 | `8ea2794bd2570587ee9799dbb0ece226bf1cc0cfab7766a7bfec5bcaf207c938` |

Offline construction:

```sh
.venv/bin/python journal/tools/admission/build_dcn_results_lookup.py \
  --source /tmp/swingset-extension-freeze-20260917-003 \
  /tmp/<new-packet-directory>
```

After independent review, the coordinator stages the exact packet without
AppleDouble files. The copied `prepare_fixture_exception.py` accepts `--packet`,
`--state`, a new separate `--quarantine`, and the actual `--approved-by`,
`--approved-at`, `--decision` and `--publication` references. It performs no
network requests and does not create the quarantine. It checks the complete
runtime and closure before writing fresh authorization, execution gate,
preparation receipt and `execute.sh` beside the driver. Schema 14, an altered
baseline, missing hold, pending restore or exhausted shared budget fails closed. Both schema markers
(`meta.schema_version` and `PRAGMA user_version`) must equal 28, and execution
rechecks the restore interlock after acquiring the writer lock.
Execution checks the hashed gate again and refuses schema migration.

The generated shell command uses `/run/current-system/sw/bin/env`,
`PYTHONDONTWRITEBYTECODE=1`, the exact source's `src` and root on `PYTHONPATH`,
and `/var/lib/swingset/venv/bin/python`. Only the coordinator may execute it after
stopping other worker operations and reviewing actual remaining shared usage.
A prepared gate is not itself evidence of an acquisition.

## Offline validation

Forty-seven explicit packet checks passed against frozen runtime 003 in 3.28 seconds,
plus Ruff. Checks use mock HTTP and disposable databases. They exercise exact
URL scope, empty/malformed probes, no redirects or retries, conditional page
zero, robots cache/disallow/future dates, controls, response/deadline limits,
unchanged paid usage, unknown legacy spacing, restart waiting, variable response
latency, forward/backward clock jumps, midnight accounting, single-use output,
closure tampering, missing authorization/gate, schema mismatch and denied domain
writes. The preparation test emits exact schema-28 pins without modifying its
state database. A separate check verifies the real complete runtime003 source
inventory. Additional regressions cover mismatched schema markers, a restore flag appearing
at lock acquisition, non-aligned remaining byte capacity, incomplete-stream
reservation preservation, refusal to probe EOF at the cap, and retention of a
chunk delivered as the wall deadline expires. No host is
contacted by these checks.

The [independent review](../../evidence/admission/dcn-results-lookup-runner-2026-09-17/independent-review-001/checks.json)
passed all 47 checks in 3.56 seconds and Ruff. It rebuilt the packet independently
and verified exact equality of every generated file and the complete build
receipt. The final builder and checks stayed unchanged. Review findings on dual
schema checks, the restore boundary and byte-accounting limits are resolved in
this packet. No remaining blocker was found for the authorized bounded lookup;
actual preparation and execution remain separate coordinator operations.

See [D-0067](../../decisions/0067-build-an-exact-schema28-dcn-metadata-lookup.md).
