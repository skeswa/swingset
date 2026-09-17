# Step Right one-body runner design, 2026-09-17

## Purpose

Prepare a bounded way to inspect one retained-locator candidate without
turning its related CDX rows into implied children. The proposed operation is
one Wayback replay for the exact Asian Open 2015 event page. The offline runner
and deployment-gate validator are implemented and independently reviewed.
Candidate 006 is deployed at schema 29 under hold. A separate reviewed robots
refresh passed. Body operation 002 then preserved a complete response but
stopped because decoded HTML was passed through HTTPX with its original gzip
header. Packet 003 was blocked before execution because its first proposed fix
weakened wire validation. D-0118 authorized a fresh attempt. The corrected
runner and packet 004 passed independent review, and the capture passed its
post-operation audit.

## Exact target and limits of the evidence

The retained CDX corpus contains one exact candidate row for
`http://steprightsolutions.com:80/events/asianopen2015`, capture
`20150711035813`. Its exact `id_` replay URL is:

```
https://web.archive.org/web/20150711035813id_/http://steprightsolutions.com:80/events/asianopen2015
```

The locator is real, but only as retained CDX metadata. Its body, printed
event dates, round links, and relation to the 12 separate Asian Open 2015 round
CDX rows remain unknown. The capture timestamp alone does not establish that
it is post-event. Those 12 rows are not proof of links on the event page.
Preserve any unavailable, empty, or non-results-bearing response as the
terminal result for this scope. Do not request an alternate capture, redirect,
child round, CDX endpoint, or origin page.

The locator audit is
[`locator-audit.json`](../../evidence/admission/stepright-next-controls-2026-09-17/attempt-001/locator-audit.json),
SHA-256 `dd3cd1048fcad435491f67b48223d4eda60fd5c60892e69a13451db4525d2c97`.
It binds the retained Step Right CDX corpus
(`b9562bc37c62b14161dafd34c570ad55e229a1dff44a833e573f7bf84bf77b46`),
the earlier fixture-exception receipt
(`195c2ddcff02401f780c1591031125fe7d254d0a251179654dc448d55e1ae99a`),
and provenance (`8323e486edb3eb8a4bcde7ba02e5ce1d45179889005c7ace75228224d73fac05`).
That audit made no network requests and read no production state.

## Reuse and isolation

The existing
[`fixture_transport.py`](../../tools/admission/fixture_transport.py) already
implements the shared pieces needed here: the Archive-host `Gate`, response
classification, byte reservation before I/O, durable body receipts, a
freshness-checked robots cache, bounded streaming, cookie clearing, and
same-resource redirect validation. The matching
[`fixture_exception.py`](../../tools/admission/fixture_exception.py) provides
single-use authorization, production-writer locking and an accounting-only
database authorizer. Current helper hashes are respectively
`006306885fb6916ec92be7ae24efcece635c0b981dcec3f9dbf8fc0cd8de5824` and
`14e57b9b78ff40a2f040216f0209a8589e8f44a7ba7eb95f7ec7dacce3b5608e`.

The helpers cannot be invoked unchanged as an SRS runner: their manifest and
authorization constants are sealed to another exact fixture. In particular,
do not mutate those constants or reuse an old manifest/authorization to make
the SRS URL pass. The sealed DCN example shows the intended packaging pattern:
an exact proposal, a builder that copies a reviewed helper closure, a
source-specific wrapper, and an independent deterministic rebuild. The DCN
body wrapper and builder are
[`dcn-results-body-h13-001.py`](../../evidence/admission/dcn-results-body-2026-09-17/packet/dcn-results-body-h13-001.py)
(SHA-256 `103206f27c25f45b43807ef4ab467392b411e9ba51355e4de6ef11a1047227dd`)
and [`build_dcn_results_body.py`](../../tools/admission/build_dcn_results_body.py)
(SHA-256 `60d6d89bbfc7bd19000470b114fe26305dc21f996cce7a4c2a40d30ce30b3d2b`).
Its packet also demonstrates independent review of a rebuilt, sealed helper
closure. Its source pin and schema-28 execution gates are historical examples,
not reusable SRS authorization or current schema-29 bindings.

To avoid changing already sealed callers, the implementation uses the second
design option: a small Step Right adapter around the shared Archive `Gate`,
response classifier, control checks, archive store and durable receipt
primitives. It has one exact target, zero retries, zero body redirects, no
metadata-query phase, a 1 MiB response ceiling, a 2 MiB operation ceiling, at
most one HTTP request, and a 15-minute elapsed deadline. Robots policy must
already have a fresh retained production cache entry; the runner cannot refresh
it. The CLI requires the SHA-256 of a separately reviewed concrete execution
gate and a sealed runner/helper closure before it opens state.

The quarantine retains the exact request URL, actual final URL,
HTTP status, response headers after cookie-header filtering, body SHA-256 and
byte count, `Memento-Datetime`, Archive receipt metadata, robots proof,
shared debit day, and a terminal complete/incomplete status. Require a valid
Memento timestamp consistent with the authorized capture; do not silently
substitute the URL timestamp when the header is absent or malformed. Save raw
bytes only in a new single-use quarantine. Create no `snapshots`, watches,
observations, admissions, or parser artifacts in production.

## Robots, shared usage, and controls

The source-specific guide says Step Right is archive-only; origin requests are
out of scope. Archive policy is owned by
[`wayback-machine.md`](../../../docs/reference/sources/wayback-machine.md):
the shared host is `web.archive.org`, with a 10-second minimum gap and a
200-request daily limit. The source guide records Archive `robots.txt` as 404
and no robots group. This historical fact is not a current cache proof. The
runner requires a fresh, retained host robots body/status. Missing, stale,
future-dated, malformed, unreadable, or disallowing policy stops before the
body and makes no HTTP request. A separate reviewed ordinary operation must
refresh the shared robots cache; this fixture cannot create that production
fact. The runner binds the exact cache row and parsed policy, then rechecks its
timestamp, status, digest and policy immediately before body dispatch after
all crawl-delay and shared-spacing waits.
Honor the standard Archive host rules for 403, 429, 503 and challenge
responses; a fixture wrapper cannot clear or shorten an automatic pause.

The one replay request uses the shared `web.archive.org` `host_budget` and
durable schema-29 spacing gate, with the actual UTC debit day and
completion-based interval. The current host count, bytes, spacing
reservation, robots freshness, operator pauses, and unrelated shared load must
be read immediately before any future operation and rechecked at each request.
This investigation reserves no capacity. A process interruption or ambiguous
dispatch retains its request/byte debit; no retry or alternate is allowed. The
runner durably marks dispatch as ambiguous immediately before socket I/O. Its
single request gets zero redirects. Exact-cap responses use a one-decoded-byte
lookahead, including gzip trailer validation, to distinguish exact EOF from
overflow without retaining the extra byte. Incomplete or overflow responses
remain incomplete and cannot be recorded as captured.

No Step Right source entry exists in the currently inspected
[`sources.toml`](../../../config/sources.toml), whose SHA-256 is
`acd354d61fc559a0d29198c6521c41278d4d76103dc87fa96fc3db8e4b121ee1`.
The host table hash is `a32134c7dd6891b8a2f7856cf6851651d5617af937b8c45cb63b9541e8be74d1`.
Missing means ordinary source operation remains disabled by the config default;
the fixture must not insert an enabled source entry or activate normal
collection. If an explicit `steprightsolutions.enabled = false` entry is
present when the packet is prepared or executed, fail closed, following the
explicit-disable interlock already used by the DCN runner. Bind the exact
config bytes in the packet and recheck them before each dispatch.

The parsed kind is `steprightsolutions.event`. That kind is still unassessed
for admission and has no accepted policy. H13 source, host, and global pauses
remain binding. Do not claim an exact-kind interlock is effective until the
control registry recognizes and tests that kind. A one-body exception remains
quarantine-only and grants no source-kind admission or historical watch.

## Deployment binding and readiness

The current held production deployment is candidate 006 at schema 29, source
`/nix/store/rgyryll4d55rgzscdqhjcwmkr325a76f-source`, and system
`/nix/store/5d9nlyflv9d4gb89a5wayhiarj01znnh-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
Before an executable body packet is built, the coordinator must verify and
record the active and persistent paths, held state, ordinary service/timer
inactivity, published baseline, current config hashes, and exact shared Archive
state.
The builder must bind those observed values, the live baseline symlink and
publication receipt, the executing runner bytes, and the exact imported
`swingset` helper closure. Preflight also rejects loaded application modules
outside the reviewed source. It reports that its checks passed without calling
that result operation readiness. Until that source freeze, deployment pin,
concrete packet and independent review exist, there is no executable gate and
no claim that the runner is ready or runnable.

D-0087 remains the standing authority for necessary v2 acquisition and
operations, so no separate permission request is needed. That authority does
not relax exact scope, source/config disable behavior, Archive robots policy,
shared limits, controls, or runtime pinning. This design authorizes no network
request by itself. The existing Step Right source modules match the retained
source-003 inventory, which means their bytes are deployed under the operator
hold; the three page kinds remain unassessed and inactive in admission.

## Implemented runner and operation outcome

[`stepright_body_runner.py`](../../tools/admission/stepright_body_runner.py)
implements the exact allowlist, retained-locator check, single-use quarantine,
schema-29 accounting connection, H13 source/kind/host/global scope, shared
Archive budgets and completion-based spacing, bounded gzip streaming, robots
cache verification, exact Memento validation, cookie filtering, dispatch
ambiguity receipts, live publication/system/config/control pins, and sealed
runner/helper provenance.
It checks the ordinary source TOML before constructing its private fixture
configuration and before every debit and dispatch. A missing source entry stays
missing for ordinary collection. An explicit `enabled = false` entry always
stops the fixture and cannot be replaced by its private policy.

The focused offline suite is
[`test_stepright_body_runner.py`](../../../tests/test_stepright_body_runner.py).
It covers the exact locator and gate, foreign URL and redirect rejection,
missing and explicitly disabled source config, fresh/stale/missing/disallowing
robots, source-kind controls, shared request and byte limits, split identity
and gzip exact-cap lookahead, incomplete and empty bodies, exact Memento
validation, runner/helper and loaded-module mismatches, live publication and
system mismatches, subprocess death at dispatch, no retry, and absence of
production watches, snapshots, pending work or parser activity. The corrected
implementation passed 50 tests, Ruff, formatting, mypy and independent source
review. It keeps declared gzip strict and strips `Content-Encoding` only from
synthetic responses built over already-decoded bytes.

The robots operation made one direct request, retained the expected 404 body,
and passed independent audit. The failed body operation 002 made one request
and remains `stopped_incomplete`; its complete 33,253-byte HTML response is not
rewritten as a successful target. Independent review blocked packet 003 before
execution, with zero requests and writes. The owner then accepted D-0118,
which permitted a fresh operation after the decoding correction.

Packet 004 binds runner
`4340e1bc6f5a3b622c893dcfce53f7d4a6ebcaa2fc68a446e296b668dafa6cc7`
and gate
`fe3f622675e6bd531512171a781a230f269b494e00b7c582312d117ffbbec50b`.
It passed source, packet and no-execute review. Its single direct request
returned HTTP 200 with the exact Memento timestamp and the complete body at
SHA-256
`bc699e4e88dd8af53f495276dde4e3a2618e65b1f64cf7932d815cef00012357`.
The operation receipt is
`fce5a3d881dce7326836c1364629a7a3ed34f9f2402648ad55b0c42ba37ebceb`.
Independent post-operation review reconciled the request, 33,253 bytes,
completion-based spacing, robots proof, hold and production isolation. The
captured body is byte-exact with the failed response body, while the operation
statuses remain distinct.

The body is an independently reviewed quarantine capture. It lists six
contests and 12 result-round links, with a responsive sidebar that duplicates
the links. Offline inspection exposed duplicate traversal and event-name bugs
in the local parser. Later local work corrected the parser, added versioned
no-removal admission contracts and integrated conservative projector-20
behavior; those bytes are tested but not deployed. No production fact, watch,
enforced source policy, historical-year acceptance, ordinary collection,
dataset deployment or publication resulted. See the compact
[operation receipt](../../evidence/admission/stepright-body-2026-09-17/receipt.json).
