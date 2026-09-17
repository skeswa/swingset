# Exact Riga results body runner, 2026-09-17

Status: Implemented locally, tested offline and independently reviewed.
This record claims no acquisition, parser acceptance, deployment or publication.

[D-0073](../../decisions/0073-approve-exact-riga-results-html-fixture.md)
authorizes the exact body identified by the completed metadata lookup.
[D-0077](../../decisions/0077-build-a-separate-exact-riga-body-runner.md)
records the separate packet choice. The
[builder](../../tools/admission/build_dcn_results_body.py) derives its code from
the retained final schema-28 metadata packet, whose build receipt is
`645d5d364c2ac261ec847cc47c44818e6afe4ff165d12d054a106938a13cf97d`.
No retained packet or runtime source was changed.

The new manifest permits only Archive robots and the exact replay of Riga
results capture `20190719204919`. It permits at most two HTTP requests, 2 MiB
per response, 4 MiB total and 900 seconds. No CDX, PDF, redirect, retry, alternate,
origin or child request is possible through its allowlist. Returned page links
remain inert evidence. The packaged, independently reviewed CDX row is checked
against its exact response hash. A changed or absent response Memento time stops the run; the requested replay
URL alone does not independently prove the observed capture.

The accounting and streaming transport retain the final metadata runner's
reviewed fixes. Every exposed body chunk is counted before deadline checks;
capacity rounds down to 64 KiB, the reader stops without an extra EOF probe at
capacity, and incomplete responses keep their full reservation. Socket and
wire buffering are not measured. Dispatch and completed-exchange receipts
include monotonic elapsed time; the full initial and completion gaps remain at
least ten seconds, including wall-clock jumps. Ordinary schema-28 accounting,
H13 controls and stricter host policy continue to apply.

Build a new packet offline:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m journal.tools.admission.build_dcn_results_body \
  --source /tmp/swingset-extension-freeze-20260917-003 \
  /tmp/swingset-dcn-results-body-20260917-dev003
```

This invocation requires the byte-exact reviewed source 003 inventory. The
result already exists; a new invocation must choose a fresh directory.
Runtime source receipt SHA-256 is
`60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6`.
The packet manifest SHA-256 is
`ac0ab2c8ae0f76084c4c91cb4f3ab22f006f6892e1239ea97de341e1cf0ac27e`.

The dedicated [offline checks](../../tools/admission/dcn_results_body_checks.py)
passed **46 tests in 2.84 seconds**, using that packet and frozen source 003:

```sh
DCN_BODY_PACKET=/tmp/swingset-dcn-results-body-20260917-dev003 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/tmp/swingset-dcn-results-body-20260917-dev003/helper-closure:/tmp/swingset-extension-freeze-20260917-003/src:. \
.venv/bin/python -m pytest -c /dev/null -p no:cacheprovider -q \
  journal/tools/admission/dcn_results_body_checks.py
```

[The retained offline receipt](../../evidence/admission/dcn-results-body-runner-2026-09-17/offline-check-001/checks.json)
binds the tested builder, dependency, checks and packet. Ruff passed for the
builder and dedicated checks. These tests include exact
capture and wrong Memento, URL and child exclusion, pure dry-run, sealed CDX
proof, request and byte limits, variable latency and wall jumps, debit across
midnight, legacy spacing, shared budget, cached and future robots, paused
controls, hold and restore interlocks, both schema fields, malformed closure,
limited SQL writes, and preparation preserving state bytes. All HTTP is fake.
The prior packet's approvals and gates are deliberately excluded from the new
packet; the coordinator must prepare fresh current-state gates after review.

Independent review passed **46 tests in 2.85 seconds** and Ruff, verified a
byte-for-byte deterministic rebuild of the complete packet and receipt, and
confirmed unchanged builder, dependency and test pins. The
[independent receipt](../../evidence/admission/dcn-results-body-runner-2026-09-17/independent-review-001/checks.json)
records the review and resolved capture-proof finding. The initial derivative
allowed the requested URL to supply capture time when Memento was absent. The
final packet instead retains that response and stops incomplete, because the
requested timestamp alone does not prove the returned capture. No remaining
review blocker was found. Coordinator staging and fresh execution gates remain
separate from these offline results.
