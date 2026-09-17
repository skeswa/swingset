# Two exact score-PDF metadata queries, 2026-09-17

Status: Implemented and tested offline. No source request or production
operation was made by this preparation. Independent review and coordinator
execution gates remain separate. [D-0087](../../decisions/0087-authorize-remaining-v2-acquisition-and-operations.md)
provides standing authority for the [exact approved scope](dcn-score-pdf-lookup-proposal-2026-09-17.md);
no further fixture permission question is required.

The [new builder](../../tools/admission/build_dcn_score_pdf_lookup.py) creates a
fresh packet bound to frozen runtime 003, schema 28, source receipt
`60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6`.
It reuses the exact retained metadata transport and H13 wrapper, verifies the
complete runtime inventory and seals the two printed score URLs with their
HTML, acquisition receipt, independent audit, locator context and authority.
It does not alter retained packets or the deployed runtime.

The fixed allowlist contains robots plus two page-count probes and two
conditional page-zero queries. The request ceiling is five, with 2 MiB per
response, 8 MiB aggregate and 900 seconds. There are no PDF or archived-body
requests, metadata alternatives, redirects, retries, child requests or origin
requests. Source-kind activation, watches, observations and year acceptance
remain outside this runner.

Each URL receives its own durable receipt entry. A successful first lookup
survives an empty, malformed, blocked or interrupted second lookup. Page zero
is conditional on that URL's positive page count; reported later pages remain
unexamined. The exact returned MIME types are retained for review. Returned
metadata does not establish availability or contents of a PDF.

The existing transport preserves one request in flight, shared paid usage,
unknown legacy spacing, at least ten seconds from response completion to
next dispatch, and any stricter configured or robots delay. It keeps full
byte reservations for incomplete responses. A final grant may remain paid
when the remaining aggregate byte capacity is too small to issue HTTP; that
conservative outcome is tested and reported separately from actual requests.
Byte counts cover exposed response-body bytes, not transport buffering.

The [retained packet](../../evidence/admission/dcn-score-pdf-lookup-runner-2026-09-17/packet-001/build-receipt.json)
has driver SHA-256
`b3e50815456113d44eb96885199c4d6caddef65d408a7ee9d85e3803cb1dd80d`
and helper-closure SHA-256
`2ad23ab7bfd923bf0d9fe7afed49b1410867c187f8f473aeefb73d427c1a1059`.
Its build-receipt SHA-256 is
`0e3c547af30a4e8cfb2f425087f883904869a95f718cbb15595165f5adca510d`.
The [offline check](../../evidence/admission/dcn-score-pdf-lookup-runner-2026-09-17/offline-check-001/checks.json)
records 61 tests passing in 3.63 seconds, Ruff and mypy. Before/after pins
cover the builder, tests, builder dependency and authority record. Tests load
the exact frozen schema-28 runtime; they do not substitute local schema 29.

The checks cover both query outcomes, partial receipts, changing or empty
probe responses, stricter spacing, wall-clock jumps, robots, H13 pauses, hold
loss between queries, shared budget exhaustion, byte ceilings, original debit
day across midnight, missing or old authority, altered helper/evidence files,
source inventory, schema/restore interlocks and rejection of body/PDF URLs.
Fake HTTP is used throughout. The test command requires bytecode writes to
be disabled so the sealed helper directory stays unchanged:

```sh
PYTHONDONTWRITEBYTECODE=1 \
DCN_SCORE_LOOKUP_PACKET="$packet" \
PYTHONPATH="$packet/helper-closure:$frozen_source/src:." \
.venv/bin/python -m pytest -c /dev/null -p no:cacheprovider -q \
  journal/tools/admission/dcn_score_pdf_lookup_checks.py
```

The coordinator must prepare a fresh execution gate against actual held state,
current quota and the acknowledged baseline before running this packet.
The wrapper checks the exact `D-0087` decision reference in the scoped
authorization record; an old completed operation's decision is rejected.
After execution, independently audit metadata and prepare exact PDF capture
scopes under the same standing authority. No result is presumed here.
See [D-0084](../../decisions/0084-seal-two-riga-score-pdf-metadata-queries.md).

The [independent review](../../evidence/admission/dcn-score-pdf-lookup-runner-2026-09-17/independent-review-001/checks.json)
passed a separate 61-test run in 3.82 seconds, Ruff and mypy. The reviewer
reproduced the complete packet and build receipt byte for byte, verified
unchanged source/test/dependency pins, and found no remaining blocker in this
bounded runner. This is an offline review result; production execution and
acquisition audit remain separate receipts.
The reviewer also [bound retained packet-001](../../evidence/admission/dcn-score-pdf-lookup-runner-2026-09-17/independent-review-001/retained-packet-binding.json)
to the reviewed temporary build with an exact file inventory and no extra
files or symlinks.
