# Production build and publication after initializer 002

Offline draft, 2026-09-16. No gate, successful receipt, staging operation, or
production launch is created by this document. The owner has authorized release
after validation; the coordinator reviews the actual serial gates below.

## Fixed inputs and actual evidence

`OPS` is `/var/lib/swingset/operations/h16-event-preservation-release-20260916`.
The production state is `/var/lib/swingset`. Source, bundle, checkpoint and
baseline remain the same as the accepted scratch release.
In command shapes, `python` means `/var/lib/swingset/venv/bin/python`.

| Input | Exact value |
| --- | --- |
| Source | `/nix/store/z689qy41inndill3d92ym8im852x3649-source` |
| Source receipt SHA256 | `0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb` |
| Accepted bundle | `fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a` |
| `OPS/initialize-002.py` | `c3259a023436e2cc3ee56e1ad83f3170b455f42187ff71421eeac25387bf96d7` |
| `OPS/supervise-initialization-002.py` | `7d2746bd079a0e0fb608ae666f4cec2146ab23561c46e2d808f0f2ba2216324f` |
| `OPS/final-verification/verify-initialization-002.py` | `de8708f555c34a97afcdae2085c266eac3d4e1230e6879d4069cd8a7bd7410db` |
| Unchanged `OPS/production-release.py` | `f8cd9ec23953600d3c9445a765df97eb894fd5363cea3f1136446c67a4f902cc` |
| Independent audit driver | `9de88bd2b5d988a00ee0529504e301b23c93eaf5018485a3f6f4db0deb2deebf` |
| Acknowledged parent commit | `81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653` |

The audit bytes are retained at
`journal/evidence/releases/h16/h16-candidate-audit-driver-20260913.py`; the
reviewed VM copy is `/var/tmp/h16-changelog-audit-driver.py`. Recheck its hash
before use. A private copy may be staged later under a new reviewed filename.

Required order: successful prepare002 → successful verifier002 anchor → guarded
supervisor current → successful verifier002 final → production build →
independent production audit → publication. The failed original prepare receipt
is never substituted for successful preparation. Final initialization proof must
bind the actual gate002, marker002, prepared anchor, supervisor preflight/final,
and terminal raw run receipt. Their hashes and paths are intentionally absent
here because those outcomes must exist first.

## Build gate construction

Create a fresh private JSON gate only after reviewing the actual final proof.
Every file reference below has shape `{"path": actual_path, "sha256": actual_sha}`.
Hash the existing regular file bytes; do not insert a guessed hash or a successful
status for work not yet completed. Keep all referenced scratch artifacts because
the initializer rechecks the original rehearsal through `gate_inputs()`.

| Required field | Value or source |
| --- | --- |
| `format` | `h16-production-release-v1-gate` |
| `mode` | `build` |
| `authorized` | `true`, after coordinator review under owner authority |
| `reviewed_by`, `reviewed_at` | Actual reviewer and timezone-aware review time |
| `state`, `source`, `source_receipt_sha256`, `input_bundle_hash` | Fixed values above |
| `driver_sha256` | Hash of unchanged `production-release.py` above |
| `initializer_driver` | Reference to actual staged `initialize-002.py` |
| `initialization_gate` | Reference to actual reviewed gate002 |
| `initialization_marker` | Reference to actual immutable marker002 |
| `initialization_receipt` | Reference to final production **run** receipt |

The run receipt must have format `h16-production-initialization-receipt-v1`, mode
`run`, status `current`, finished time, empty `unfinished_by_scope`, exact
gate/marker/driver/source hashes, zero requests, and false parse/build/published
flags. Its `protected_unchanged`, `parse_tokens_unchanged`, `controls_unchanged`,
and `input_authority_unchanged` fields must all be true; its final baseline must
match the marker. The independent verifier adds the actual ledger, integrity,
parse-anchor, inherited-run, and batch-accounting checks.

The unchanged release driver does **not** interpret the independent verifier
receipt. Coordinator review of that successful receipt is therefore an explicit
build-launch prerequisite. Add `initialization_verification` and
`prepared_verification_anchor` file references to the gate for traceability if
desired: the driver's generic protected-reference check validates their bytes
and forbids output overlap, but does not validate their semantic contents.

Run as `swingset`, with the exact frozen environment from the parent README:

```text
python OPS/production-release.py build
  --gate OPS/ACTUAL-REVIEWED-BUILD-GATE.json
  --output OPS/NEW-PRODUCTION-BUILD-RECEIPT.json
```

Use the actual new filenames selected by the coordinator. The driver takes the
existing writer lock, rechecks current project/link work and authority, captures
without accepting inputs, and calls ordinary closure build with `remote=None`.
It does not replay, parse, publish, or reconcile publication. Its receipt must
report `status=built`, `passed=true`, `published=false`, zero network requests,
`semantic_publication_preflight=true`, and all five preservation fields true:
protected, controls, input authority, parse tokens, and revisions. Require the
correct source/bundle/driver/build-gate hashes and actual candidate path, ID,
manifest hash and `expected_parent`. The candidate must be directly under
production `candidates`; a scratch candidate cannot substitute.

## Independent production audit

Run alone after the build passes. Obtain the candidate path from its actual
receipt and resolve the still-acknowledged V4 baseline before publication. The
audit output must be a new file **outside `/var/lib/swingset`**; the unchanged
auditor rejects even `OPS/audit.json`.

```text
python /var/tmp/h16-changelog-audit-driver.py
  --state /var/lib/swingset
  --candidate ACTUAL-PRODUCTION-CANDIDATE
  --baseline ACTUAL-V4-BASELINE-CANDIDATE
  --output NEW-PRIVATE-VM-TEMP-DIRECTORY/audit.json
  --temp-parent /var/tmp
```

Use a service-owned private directory under `/var/tmp`, with umask 0077. The
auditor uses normal read-only SQLite including WAL, DuckDB with a 1GB limit and
two threads, and a disposable spool directory. It checks actual committed build
proof and files. Require all 54 checks true, including all 4,931 named judges,
named null-ID judge preservation, the baseline count of 34,955 default entry IDs and supported identity retention, occurrence
and registry association, all 17 years, all 66 coverage-year transitions,
identity/support/journal rules, table counts, uniqueness and references.
Compare the receipt's state/candidate/ID/manifest/parent to the production build.

After review, copy the exact successful audit bytes into a fresh private OPS
file and compare hashes. The audit format does not embed the executable hash;
retain the launch argv, audited driver hash, direct process exit and resource
receipt to establish that provenance. Bind the driver separately in the publish
gate. Keep the original VM-local output and logs.

## Publish gate and execution

The publish gate has the same header and four initialization references as the
build gate, with `mode=publish`. Add these references to actual successful files:

| Required field | Evidence |
| --- | --- |
| `build_gate` | Exact reviewed production build gate |
| `build_receipt` | Actual successful production build receipt |
| `audit` | Exact independent production audit receipt |
| `audit_driver` | Actual separately reviewed audit executable bytes |

The driver requires the same four initialization reference paths in build and
publish gates, validates their hashes, and checks all required audit fields.
All audit checks present must be true, including every emitted table count.

```text
python OPS/production-release.py publish
  --gate OPS/ACTUAL-REVIEWED-PUBLISH-GATE.json
  --output OPS/NEW-PUBLISH-RECEIPT.json
```

Supply `HF_TOKEN` through the existing private systemd `EnvironmentFile`; use
only its verified path in launch arguments and never expose its contents. Read
the installed unit's `EnvironmentFiles` property to identify the actual
configured path; `/etc/swingset.env` is only the documentation example. The
repository is fixed to `skeswa/swingset`. No network is needed for build/audit;
publication uses network for parent inspection, uploads, and full remote file
hash verification. `HuggingFaceHub.inspect()` downloads all manifest-listed
files, not just the manifest. Allow for that transfer when bounding publication.
The publish receipt's request counter is `null`; do not claim measured request
counts from it.

Require successful direct exit, `passed=true`, `published=true`, a real
`publication_receipt.commit`, matching manifest/candidate, preserved authority,
and `final_baseline` pointing to that same acknowledged candidate and commit.
The driver verifies remote files and parent and preserves normal late-pause,
correction, admission and publication fences. An unpublished `unchanged` result
is not success. Do not clear collection holds as part of this release.

## Launch containment and interrupted publication

Use a fresh transient unit per operation, `Type=exec`, `User=swingset`,
`Group=swingset`, umask 0077, memory accounting, the exact frozen working directory,
PYTHONPATH, revision and native library paths. Use a finite coordinator-reviewed
runtime and normal SIGTERM/180-second stop bound; the earlier reviewed procedure
uses 3,600 seconds. Launch the external 6 GiB anonymous-memory monitor immediately
and keep heavy VM work serial. Match its deadline to the chosen runtime; do not
accidentally use a shorter monitor deadline. Retain live logs on the VM outside
the checkout. Direct `systemd-run --wait` exit status is authoritative if the
successful transient unit is garbage-collected. Verify monitor termination,
actual running PID samples, raw result and bound artifact before advancing.

On an uncertain/lost publication response, preserve the same candidate and
publish gate and use `publish --resume` with a fresh unit/output receipt. This
permits reconciliation only for that candidate. A landed commit is verified and
promoted without another submission; an unlanded attempt still goes through the
normal submission boundary. The helper's `reconcile(dry_run=True)` may settle
admissions or promote a verified landed receipt; it is not a read-only probe.
Never infer that an interrupted receipt means no remote commit occurred. A
publication can land before a later preservation check fails. Unrelated intents,
remote-head drift, changed controls, unfinished work, or a draining publication
must stop for coordinator reconciliation rather than creating another candidate.

No code blocker was found in the unchanged release interface. Outstanding gates
are actual successful initialization002 and independent final verification,
actual production build/audit, current credentials/remote parent, and a reviewed
finite monitored publication launch. No unknown receipt hash, candidate ID or
remote commit has been assigned by this draft.
