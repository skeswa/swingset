# Exact DCN index runner preparation, 2026-09-17

The [offline builder](../../tools/admission/build_dcn_index_fixture.py) creates
a separate runner for the one index body accepted in
[D-0060](../../decisions/0060-approve-exact-dcn-index-fixture.md). The
[implementation choice](../../decisions/0061-build-a-separate-schema14-dcn-fixture-runner.md)
keeps the production schema-14 boundary and the old retained packet unchanged.
No execution, acquisition or production acceptance is claimed here.

The builder first verifies the old driver, helper inventory and exact new
proposal/CDX hashes. It copies verified code with explicit checked substitutions:
new manifest and authorization marker; no CDX execution or allowlist entries;
only the selected index body and robots; and a completion-anchored host floor.
It then constructs a new hash-pinned closure, driver and gate preparer. The
build receipt pins every output and both builder/preparer inputs. Rebuilding
requires a new destination. Formatting maintained source requires rebuilding
the packet and validating those new bytes before use.

Each transport call records `transport_dispatched_at` at handoff to the
underlying HTTP transport. After the response stream closes or fails, the
runner records `exchange_completed_at` and persists `next_dispatch_not_before`
at least ten seconds later, preserving any stricter existing floor or crawl
delay. Retention and H13 settlement follow. These are measured process
boundaries, not claimed packet-on-wire timestamps. A completed request cannot
gain a shorter following gap through variable reservation or receipt-write
latency. The schema-14 host table carries the deadline; no later-schema table
or migration is used.

Independent review found that a UTC-only completion floor could still be
shortened by a forward wall-clock adjustment. The final helper therefore also
waits on `time.monotonic`, with an independent injectable clock for offline
tests. Its first request waits an initial host floor; later requests wait a
full elapsed floor after completion. Receipts include process-relative elapsed
dispatch, completion and deadline values alongside UTC timestamps. Neither
forward nor backward wall adjustments can shorten that process-local gap.

Fresh offline checks: 20 tests passed against the receipt-verified schema-14
local mirror, with MockTransport and disposable databases. They cover variable
reservation/save/response delays, all/source/host/kind H13 pauses, actual shared
budget exhaustion, cached robots disallow, service/restore interlocks, three
archive redirects within five HTTP requests, rejected origin/CDX/PDF redirects,
no retry after failure, settled admissions, forbidden domain writes, obsolete
authorization rejection and migrated-schema rejection. The tests are run
explicitly from [the check module](../../tools/admission/dcn_index_fixture_checks.py);
they deliberately refuse a runtime whose schema version is not 14. Independent
packet review and coordinator execution remain distinct gates.

The final run adds independent forward/backward wall-clock adjustments, exact
8 MiB response-prefix retention within the 16 MiB total ceiling, and deadline
interruption during a response. Ruff passed for the builder and check module.
The actual new driver dry-run reports exactly one target, zero metadata queries
and zero requests. Before the monotonic correction, separate copied legacy
suites passed 22 transport and 32 H13/isolation checks; those are historical
receipts for the earlier development packet, not validation of later bytes.

## Coordinator invocation

Build into a fresh local directory:

```
.venv/bin/python journal/tools/admission/build_dcn_index_fixture.py /tmp/swingset-dcn-index-fixture-<unique>
```

Run the checks with `DCN_FIXTURE_PACKET` pointing there and
`PYTHONDONTWRITEBYTECODE=1`; set `PYTHONPATH` to that packet's `helper-closure`
plus the verified frozen mirror's `src` and root, then use:

```
.venv/bin/python -m pytest -c /dev/null -q -p no:cacheprovider journal/tools/admission/dcn_index_fixture_checks.py
```

The coordinator stages the exact reviewed packet with AppleDouble metadata
disabled and verifies its build receipt. Use a fresh operation directory and
separate `/var/tmp` quarantine. On the worker, run its
`prepare_fixture_exception.py` with `/var/lib/swingset/venv/bin/python`, supplying
the actual packet/state/quarantine paths, owner Sandile Keswa, decision
`journal/decisions/0060-approve-exact-dcn-index-fixture.md`, approval observation
`2026-09-17T16:00:18+00:00`, and the acknowledged candidate/commit receipt.
That timestamp is the coordinator's observation, not a claimed owner click time.
The exact runtime remains `/nix/store/z689qy41inndill3d92ym8im852x3649-source`.

Review actual remaining quota and controls in the generated preparation receipt,
then execute its `execute.sh` once. It names the absolute NixOS `env` binary.
The original hold remains, all requests use ordinary shared host accounting and
H13 admission, and no source-kind or production parse queue is created. A failed
or incomplete acquisition does not authorize another output path or request
allowance. Actual execution and independent body audit need their own receipts.
