# Preparing the source-fixture exception runner

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Prepared for the [bounded proposal](v2-new-source-fixture-proposal-2026-09-13.md).
**No authorization is recorded by this document. No source request was made.**
The runner does not activate page-kind policies or grant production acquisition.

## Commands

Run from the repository root using the project Python and its installed dependencies.
The default is a dry run: it reads and verifies retained manifest evidence, prints
exact requests and limits, and does not open state, write files or create an HTTP client.
It therefore does not claim current publication readiness or remaining live quota.

```sh
PYTHONPATH=src .venv/bin/python -m research.fixture_exception \
  --manifest journal/evidence/admission/fixture-exception/v2-new-source-fixture-targets-2026-09-13.json \
  --state /var/lib/swingset \
  --quarantine /var/tmp/swingset-new-source-fixtures-2026-09-14
```

Only after the owner's specific exception is recorded, V2/V4 are published,
and the shared archive quota has capacity, the manual execution command is:

```sh
PYTHONPATH=src /var/lib/swingset/venv/bin/python -m research.fixture_exception \
  --manifest journal/evidence/admission/fixture-exception/v2-new-source-fixture-targets-2026-09-13.json \
  --state /var/lib/swingset \
  --quarantine /var/tmp/swingset-new-source-fixtures-2026-09-14 \
  --authorization /var/tmp/swingset-new-source-fixture-authorization.json \
  --execute
```

Use the actual reviewed quarantine path and dates. The example date is not a
reservation or permission. The caller needs access to the state writer lock and
host accounting. The runner opens the existing database without running migrations
or inserting a pipeline run. It does not check or remove the Nix scheduled-service
`operator-hold` file: this is a manual action. Persisted `operator_pauses`, host
pauses, `RESTORE_PENDING`, and an occupied writer lock remain authoritative.

## Required authorization record

The operator records the actual owner decision and published release receipt in
JSON. The example below is deliberately **non-authorizing**: `approval: null` and
blank references/times fail validation. Only an actual owner decision permits
setting `approval` to `approved_new_source_fixture_exception_only`.

```json
{
  "approval": null,
  "manifest_canonical_sha256": "c51d9810073d5e49c4a1044e30de97bd4409f3ad4c4a109aa6a2fa31dc51dd93",
  "state": "/var/lib/swingset",
  "quarantine": "/var/tmp/swingset-new-source-fixtures-2026-09-14",
  "published_stages": ["V2", "V4"],
  "approved_by": "",
  "owner_decision_reference": "",
  "publication_receipt_reference": "",
  "approved_at": "",
  "execution_window": { "starts_at": "", "expires_at": "" }
}
```

Times must include UTC offsets. `approved_at` records the actual owner decision;
that authorization persists and does not expire after 24 hours. The operator sets
`execution_window` to a current interval lasting at most 24 hours and beginning
after that approval. An
unstarted window may be rescheduled without asking the owner again while the same
authorization remains valid. The runner stops at the window end; this bounds an
execution record, not the lifetime of human permission. State and quarantine paths must be exact
resolved absolute paths and separate directories. The publication reference is
an operator attestation to an already verified receipt, not a substitute for the
release audit. No self-issued or inferred authorization is acceptable.

The runner pins the manifest's canonical JSON hash, independently verifies the
retained CDX rows/header hashes, and refuses changed targets or expanded limits.
Formatting the manifest does not change its canonical hash. The quarantine path
is single-use, created exclusively before network activity. Rescheduling an
unstarted window does not reset the one-time proposal ceiling. A stopped or crashed
run cannot be retried by changing the window or deleting its directory. Preserve
that directory and assess any unfinished targets against the original finite
authorization and already charged requests before preparing a continuation; this
runner has no automatic continuation mode.

## Accounting and retained output

The runner takes the pipeline's existing exclusive `state.lock` for its whole
execution and reuses `Gate`. Its SQLite authorizer allows writes only to host
request/byte accounting, scheduling gaps and pause fields; production watches,
snapshots, observations, generations, decisions and canonical facts cannot be
written. Source pauses apply to SRS/DCN, including any robots request on their
behalf. Policy activation and historical year acceptance are never called.

Every HTTP exchange, including robots and redirects, consumes the same persistent
200-request UTC-day budget. The runner also imposes 12 total requests, at least ten
seconds between requests, one request in flight, 32 MiB received overall, 8 MiB per
response and 15 minutes elapsed. An earlier execution-window end also stops it.
HTTP requests use the project User-Agent, disable cookies and request identity
encoding. Unexpected content encoding stops acquisition. Automatic retries and
origin redirects are disabled; archive redirects may change the capture timestamp
but must retain the exact authorized original resource.

Streaming stops at a byte ceiling and marks the retained prefix incomplete. A
response ending exactly at the ceiling is conservatively incomplete too. The
ceiling applies to bytes exposed by the streaming reader and retained as fixture
content; TLS/socket buffering and HTTP headers are not separately metered by the
existing gate. No further request follows a ceiling or throttle response.

Before each exchange, accounting reserves its maximum possible response bytes.
A completed attempt refunds unused capacity; a process killed during I/O leaves
the conservative reservation charged. Request charges are never refunded. The
quarantine receipt records the reservation before I/O so an operator can inspect
an interrupted attempt without guessing that it consumed zero bytes.

Fresh robots responses are stored only in quarantine; their digests are not
written into production's robots cache. A valid cached production robots body is
read and copied into quarantine. This avoids dangling production artifact
references while preserving robots evidence. `receipt.json` records every attempt,
headers, hashes, actual final archive URL/capture time, stop reason and unresolved
CDX pages. Complete and partial bodies are under `bodies/<sha256>`; the exact
manifest and authorization are retained alongside them.

The runner captures bytes only. Offline extraction, independent source-contract
review, real fixture regressions and any later policy activation remain separate.
DCN page 0 is retained as metadata; returned URLs never become requests or watches.
Zero DCN PDF requests and zero 2009 event/round requests are included.

## Offline verification

```sh
.venv/bin/pytest -q tests/test_fixture_exception.py
MYPYPATH=src .venv/bin/mypy --explicit-package-bases \
  journal/tools/admission/fixture_exception.py journal/tools/admission/fixture_transport.py
```

Tests use mock transports and disposable databases. They cover absent/mismatched
approval, dry-run purity, manifest tampering, all production tables remaining
unchanged, shared budgets and source/operator pauses, robots and archive redirects,
response/total/request/time limits, exclusive ownership and a real subprocess crash
leaving its request and conservative byte reservation durable. No test fetches a
source.
