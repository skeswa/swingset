# WP16 deployment preparation

Prepared only. No WP16 deployment, acquisition, year acceptance, page-kind activation, or publication has run through this helper. The current H16 rollout and its next verified private backup must finish before a concrete gate can be assembled.

`research/accept_wp16.py` accepts exactly schema 14→15. Default invocation performs read-only preflight. `--execute` runs the normal migration opener once, checks all retained data, and compares a fixed-time doctor report with a fresh process. It never accepts runtime inputs, starts a run or worker, creates origin proposals, makes source requests, or publishes. Existing unmaterialized work remains unfinished.

## Required evidence

The future immutable source contains `wp16-source.json` with:

- `format: wp16-reviewed-source-v1`, `schema: 15`;
- `acquisition_enabled: false`, `repairs_activated: false`;
- `files`: every source file's exact SHA256, excluding only this receipt and Python bytecode caches.

The helper verifies full source closure and requires loaded `swingset.state.db` and `research.accept_h11` to come from that source. Include the H11 helper in the frozen source. The acceptance driver may be copied separately into operations; its exact copied bytes must match the gate's `driver_sha256`.

The gate format is `wp16-operational-gate-v1`. Supply actual values for all fields:

| Field                                      | Authority                                                       |
| ------------------------------------------ | --------------------------------------------------------------- |
| `h16_release_verified`, `verified_at`      | Completed H16 release verification and actual review time       |
| `driver_sha256`, `source_receipt_sha256`   | Exact reviewed operations driver and future source receipt      |
| `predecessor_source_receipt_sha256`        | Deployed schema14 source recorded by the new private backup     |
| `checkpoint`, `checkpoint_manifest_sha256` | Latest verified schema14 checkpoint path and root manifest hash |
| `private_backup_commit`                    | Verified private backup commit                                  |
| `public_commit`, `public_manifest_sha256`  | Actual post-H16 public baseline and manifest                    |
| `private_backup_receipt`                   | Relative path to completed backup receipt                       |
| `private_verification_receipt`             | Relative path to successful private remote verification         |
| `public_verification_receipt`              | Relative path to successful public remote verification          |
| `evidence_files`                           | SHA256 map containing every referenced receipt                  |

The backup receipt uses the established fields `commit`, `manifest_hash`, `checkpoint`, `files`, `schema_version: 14`, `baseline_commit`, `finished_at`, and `source_receipt_sha256`. Private remote verification must contain `verified: true`, `private: true`, matching `commit` and `manifest_sha256`, and `verified_at`. Public verification must contain `verified: true`, matching `commit` and `manifest_sha256`, and `verified_at`. Preserve the original verification evidence; a coordinator can record this small normalized verification receipt alongside it when an existing tool emits a larger result.

The current baseline must have a completed H16 closure publication receipt. Its candidate ID, closure digest, evidence cutoff, commit, verified time, manifest hash, and retained files are checked. A prepared candidate or the older correction-only baseline cannot satisfy this gate. There are no fabricated future source, checkpoint, or public commit constants in the driver.

## Invocation

Run with the exact future pin's `src` and repository root on `PYTHONPATH`, the installed service Python and normal runtime libraries. Use the service account and an externally bounded operations unit. Replace `PIN`, `GATE`, and `OUTPUT` with concrete reviewed paths; these examples are templates, not an authorization record.

```sh
/var/lib/swingset/venv/bin/python /var/lib/swingset/operations/wp16/accept_wp16.py --state /var/lib/swingset --source PIN --source-receipt-sha256 EXACT_RECEIPT_SHA --gate GATE --output OUTPUT
/var/lib/swingset/venv/bin/python /var/lib/swingset/operations/wp16/accept_wp16.py --state /var/lib/swingset --source PIN --source-receipt-sha256 EXACT_RECEIPT_SHA --gate GATE --output NEW_OUTPUT --execute --max-seconds 1800
```

Every invocation needs new output names. The main receipt, immutable `.intent.json`, `.sizes.json`, `.doctor.json`, `.doctor.txt`, and temporary receipt paths must all be new, outside source and retained evidence roots. In-state output belongs under `state/operations`. Existing files, symlinks, symlink ancestors, checkpoints, baseline directories, and candidate roots are rejected before writing the size note. Files are created exclusively with `O_NOFOLLOW` and private permissions. The size note precedes expensive checkpoint hashing and reports database/source scale. Full doctor output stays private in operations; only a compact digest comes back from its fresh process.

Execution holds the ordinary writer lock. The migration opener uses its existing short lifecycle/control lock; lengthy hashing and doctor work do not hold the control lock. The persistent filesystem hold and inactive scheduled units are checked before and after. An active admission, running attempt, pending publication, unfinished restore, or retained-state change fails preflight. A control change during verification is detected by final exact state comparison; the helper never overwrites it.

## Preservation checks

Preflight reads the checkpoint with immutable SQLite and validates its complete file manifest, including nested manifests and absence of unexpected WAL/SHM sidecars. Every live table, original column, schema object, autoincrement sequence, and retained authority value must match the checkpoint. Only operational `last_backup*` metadata may differ from the saved checkpoint.

Migration may change the schema version metadata and add exactly the reviewed migration15 declarations and their publication fences. The three tables `history_origin_intents`, `history_origin_requests`, and `history_origin_operator_refs` must remain empty. All preexisting rows, columns, indexes and triggers remain exact, including host budgets, watches, controls, history acceptance, source contracts, identity journal/revisions, accepted inputs, derivation proofs and pending work. The helper derives expected new DDL in a tiny in-memory schema specimen, without copying the production database or fabricating proof.

A fresh-process doctor must read schema15 from the same pin and produce the identical full report at the same explicit instant. Human requirement output must agree with its JSON inventory. The helper then repeats state preservation, FK integrity, baseline and hold checks. No backlog counts are reported as work completed.

Thirty offline cases currently pass: real schema14 migration, deterministic fresh doctor, no activation or service, exact original state preservation, empty new tables, prior/new schema object checks, actual receipt and runtime mismatches, nested checkpoint closure, output symlink guards, crash-after-migration read-only completion, and unchanged intent/preimage authority. Production acceptance remains unexecuted until actual prerequisite evidence exists. The source freeze and final helper hash must be recorded after central formatting and review.

## Completing verification after interruption

Execution writes an immutable `.intent.json` before opening the database for migration. It contains the exact schema14 preimage, source and driver hashes, gate hash, confirmed baseline, and migration-only authorization. The file and parent directory are synced before migration begins. The mutable result receipt can be lost without losing this intent.

If migration committed schema15 but doctor or final verification was interrupted, do not rerun the schema14 preflight or migration opener. Keep the original intent unchanged and use a new output path:

```sh
/var/lib/swingset/venv/bin/python /var/lib/swingset/operations/wp16/accept_wp16.py --state /var/lib/swingset --source PIN --source-receipt-sha256 EXACT_RECEIPT_SHA --gate GATE --output NEW_OUTPUT --execute --resume-intent ORIGINAL_INTENT_JSON --resume-intent-sha256 EXACT_ORIGINAL_INTENT_SHA --max-seconds 1800
```

Resume verifies the exact supplied intent hash and the same reviewed source, driver, gate, backup, and published baseline. Its recorded schema14 preimage must still match the verified checkpoint, apart from the original allowed backup metadata. Current schema15 must match that preimage plus only the expected migration15 objects; every new table stays empty. All database openers in this mode are read-only. The result explicitly reports `migration_executed: false` and `read_only_completion: true`; it performs no second migration, input acceptance, worker, activation, or request.

A changed retained input, operator control, original schema object, nonempty origin table, changed intent, stale source, or superseding public baseline rejects completion. This is recovery of the already authorized migration, not new owner approval. If the original interruption occurred before migration committed, use a new ordinary schema14 preflight/execution output instead; the schema15 resume path rejects schema14.

The backup check also binds the actual retained candidate manifest and full `PUBLISHED` receipt bytes to the verified live baseline. Matching candidate directory names and a backup header alone do not establish that binding. Independent regressions alter these files and recompute the checkpoint and verification receipt hashes; the mismatch is still rejected.
