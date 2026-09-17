# DCN exact-score origin runner

## Executed acquisition

The coordinator built [packet 001](../../evidence/admission/dcn-origin-runner-2026-09-17/packet-001/build-receipt.json)
from source-003-bound runner bytes. Independent code and exact packet review
passed 37 tests and Ruff; the VM dry preflight passed. The actual operation
closed at 19:24:30 UTC with four requests and 132,764 bytes: the root robots
redirect, the allowed robots body, and both exact PDFs. It created no production
facts or watches and accepted no source kind or historical year.

The [independent acquisition audit](../../evidence/admission/dcn-origin-runner-2026-09-17/actual-review-001/review.json)
verified robots permission, exact body hashes, three completion-based gaps above
ten seconds, shared usage, all eight settled H13 admissions and both durable
sidecars. Production remained schema 28 under the unchanged hold with six
inactive units and the unchanged H16 publication. Archive usage stayed at 20
requests and 2,952,065 bytes. DCN usage is now four requests and 132,764 bytes.

The [quarantine receipt](../../evidence/admission/dcn-origin-runner-2026-09-17/quarantine-001/receipt.json)
and [robots export](../../evidence/admission/dcn-origin-runner-2026-09-17/robots-export-001/receipt.json)
are retained. Successful acquisition is not parser admission, deployment of a
new runtime, publication or acceptance of V5/V6. A fresh verified checkpoint and
actual recovery rehearsal must preserve this later accounting before rollout.

## Earlier implementation record

### Purpose

This investigation records an offline implementation for acquiring the two exact score PDF URLs printed by the retained Riga results page. It does not activate ordinary DCN crawling, interpret the PDFs, create watches, or accept a historical year. The scope remains governed by D-0087, D-0088, the proposed D-0090 policy, and the exact proposal packet.

## Implemented

`journal/tools/admission/dcn_origin_fixture_runner.py` contains a one-event capture runner. It checks schema 28 and the retained production hold, preserves the ordinary DCN source kill switch, uses shared H13 admission and host accounting, refuses robots/cache ambiguity and unapproved redirects, and only writes raw response bodies to a new quarantine. The request ceiling is four including the allowed robots redirect; each decoded response is capped at 2 MiB, total decoded bytes at 8 MiB, operation time at 15 minutes, and host cadence is at least ten seconds plus applicable robots delay. It sends no cookies and retains no `Set-Cookie` value.

The event/day claim is a durable `dcn-origin-event-days.json` file under the state directory. Checkpoint closure includes this file, so restore retains the one-event/day and per-operation request sequence. A claimed operation cannot resume after a crash: it stops rather than replaying a possibly issued request. Paid request admission, byte reservation, and socket-dispatch attempt are recorded separately. A pre-dispatch failure settles the H13 admission as `not_issued`; ambiguous or issued requests keep conservative accounting.

The packet builder binds the exact candidate runtime path, its retained source receipt and complete file inventory, schema 28, source configuration digest, exact proposal and supporting retained evidence, and D-0087/D-0088/D-0090 bytes. The runner CLI checks the entire packet closure and coordinator gate before execution. It has a no-execution preflight mode; execution additionally requires an explicit gate and exact authorization/quarantine bindings.

## Validation

Offline tests use only `httpx.MockTransport`. They cover the missing-versus-disabled source setting, exact redirect scope, source/host/kind/global H13 pauses, fresh robots cache provenance and cached crawl delay, gzip expansion and trailer boundaries, cookie rejection, hostile status and challenge classification, Retry-After, robots 403 pause retention, PDF redirect refusal, unexpected body quarantine, durable event-day replay prevention, checkpoint inclusion, schema binding, and packet closure integrity.

The runner and builder have passed Ruff. The focused test command is:

```sh
.venv/bin/python -m pytest -c /dev/null -p no:cacheprovider -q tests/test_dcn_origin_fixture_runner.py
```

The pinned `/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source` is not present in this development workspace, so the source-bound packet build and preflight have not been executed here. The coordinator must build the packet in the environment containing source 003, independently review its final closure, and recheck the live state before any operation. No request to Archive or danceconvention.net was made by this implementation.

The coordinator recorded the claim-before-commit, cache-provenance, and live-runtime binding choice as [proposed D-0095](../../decisions/0095-preserve-origin-fixture-request-claims.md). It does not create owner acceptance; D-0090 also remains proposed.

## State

Implemented and offline-tested; the pinned packet has not been built or independently approved yet. Nothing from this runner has been deployed or published. No owner acceptance is inferred for proposed D-0090 or D-0095, and no origin request is authorized by this investigation alone. Remaining runtime and acquisition gates must close before execution.
