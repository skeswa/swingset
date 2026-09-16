# Existing fixture runner: H13 operational adapter draft

No owner approval, source request, deployment or live execution occurred. The
existing exact manifest and transport remain authoritative. Do not duplicate the
five-body proposal, issue a broader request, activate a parser, accept a year or
use this fixture capture as production source admission.

The prepared repository tooling already provides:

- research/fixture_exception.py: canonical manifest hash and retained-evidence
  verification, specific owner authorization, current execution window, raw
  existing-state writer lock without migrations, default no-write dry run,
  and a POSIX hard elapsed-time interrupt.
- research/fixture_transport.py: five exact replay URLs plus the DCN exact-year
  probe/page0, archive-only same-resource redirects, shared Gate/200-per-day
  quota, ten-second floor, 12 total requests, 8 MiB response/32 MiB total
  conservative byte reservations, exclusive fixture quarantine and receipts.
- tests/test_fixture_exception.py and the existing runner/proposal documents.

One real gap appeared after H13: the old direct Gate call sees all/source/host
pauses but not requirement-kind pauses, and has no execution admission records
for visible pause draining. The old SQLite authorizer forbids those lifecycle
records. The new /tmp/fixture_exception_h13.py adapts only these seams; it does
not implement another downloader.

The adapter wraps each original resource request with H13 operation and each
actual host debit with H13 admission. SRS round507/508 use round_observations;
SRS index/event and DCN metadata/CDX use source_event_mapping. Robots inherit the
current resource's kind. All scopes bind the actual source and Archive host.
Pauses during HTTP persist without blocking retention, then settle to paused;
subsequent requests stop. Failed reservations retain charged debits but release
inflight/admission claims. The SQLite authorizer permits the five H13 lifecycle
control tables in addition to existing host accounting; it still forbids domain
facts, watches, snapshots, generations, policies, runs and schema changes.

Execution prerequisites remain actual evidence, never placeholders:

1. The existing owner authorization record with the exact proposal manifest,
   owner decision reference and time, current bounded execution window,
   exact state/quarantine, and verified V2/V4 publication reference.
2. A separate coordinator gate, format fixture-h13-coordinator-v1, with exact
   driver_sha256, actual deployed source path, relative source_receipt filename
   and source_receipt_sha256, actual schema, state, resolved baseline_path,
   published_sha256 and manifest_sha256. It pins the copied adapter and verifies
   every recorded runtime source file. This gate is engineering review, not a
   replacement for the owner's still-pending exception decision.
3. Shared quota capacity. September 13 is exhausted. Earliest next quota window
   is September 14 00:00 UTC; other work may consume that day's capacity too.
4. A fresh exclusive quarantine path. The existing one-time cap and stopped-run
   rules remain unchanged; this adapter does not add automatic continuation.

Dry run uses the original arguments and remains pure:

    PYTHONPATH=ACTUAL_PIN:ACTUAL_PIN/src PYTHON /OPS/fixture_exception_h13.py --manifest ACTUAL_PIN/research/v2-new-source-fixture-targets-2026-09-13.json --repo ACTUAL_PIN --state /var/lib/swingset --quarantine EXACT_NEW_QUARANTINE

Only after actual owner approval and coordinator review, execution adds:

    --execute --authorization ACTUAL_OWNER_RECORD --execution-gate ACTUAL_COORDINATOR_GATE --execution-gate-sha256 EXACT_GATE_HASH

No pending authorization JSON is manufactured here. The adapter does not invoke
input acceptance, migration, pipeline workers, publication or activation. The
scheduled-service hold stays untouched. The operations driver is separate from
both the immutable source pin and fixture quarantine. Final capture receipts
retain raw transport provenance from the original runner; independent parser
and contract review must follow any approved capture.

Eighteen offline integration/guard cases cover mapping-kind and round-kind pause
scope, pause persisted during HTTP with pausing→paused drain and retained body,
the full exact five-body/zero-page-probe recipe, forbidden production writes,
and reservation failure with durable debit and settled claims. They use fake
HTTP/clock and disposable SQLite. They do not assert any production authorization
or deployed source gate succeeded. Coordinator review remains pending.

Review corrections retain the original hard wall_limit path. When its remaining
POSIX alarm is already at most 45 seconds, adapter-local FixtureDatabase uses
that earlier alarm unchanged, caps SQLite busy waiting to its remaining time,
and adds the same deadline as a SQLite progress check. It does not install a
new signal handler or move the timer. Commit/rollback remain atomic; normal
H13 transactions, nested savepoints and read-only transactions retain their
existing paths. Tests exercise both successful settlement with a five-second
alarm and an actual expiry with rollback and restored SQLite handlers. Once
the alarm has fired, the original runner prohibits further acquisition; durable
cleanup may use ordinary bounded bookkeeping. SIGKILL still leaves explicit
abandoned admissions for normal recovery, never a fabricated settled receipt.

The copied driver also rejects a different --repo or any imported swingset
module/fixture module outside the pinned source. Disk inventory validation alone
is insufficient. Request action IDs survive a failed settlement until its retry
commits. A failed refund leaves the conservative byte reservation charged.
Request byte reservation and refund use the actual grant's captured UTC day,
including when midnight passes before retention; the receipt names that day.
These negative cases are tested without pretending main's production pin or
owner-approval gate has ever passed.

Effective CLI options are parsed once with abbreviation disabled and duplicate
options rejected, including mixed --repo VALUE / --repo=VALUE forms. The original
runner receives only canonical unique options; its actual selected state is
rechecked before opening the database. Tests cover duplicate repo/state inputs,
equals-form normalization, and a different selected state.

The chosen source pin must include both original research fixture modules. If a
frozen runtime intentionally omitted them, do not mix in an unverified worktree
module to make imports succeed: prepare a reviewed helper-inclusive source or a
separately designed hashed supplemental helper closure first. This draft's strict
source check does not claim such a deployment bundle already exists.
