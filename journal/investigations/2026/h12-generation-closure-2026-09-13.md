# Checking that backups include saved output files (H12)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Work isolation lets one task fail without stopping unrelated tasks. This record concerns retries, saved artifacts, or migration checks. The original work ID is H12.

The verified V4 checkpoint contains every body and extract named by its retained
source generations. This audit found no omitted paths and made no changes to
the checkpoint or live state.

The checkpoint is
`/var/lib/swingset/checkpoints/v2-v4-published-cand_7f8cf9bcbf7e4a60`.
Its 32,189 generations comprise 31,276 accepted, 894 needing review,
12 superseded, and seven waiting for inputs. Their manifests reference 61,012
unique artifact paths, all present in the saved file manifest and directory.
There were no omitted digests requiring a live-file availability check.

The SQLite SHA-256 was
`fc05bfc79c57a153f6f5fa9de21438aa432e57952d02b88b92f1a685f3cf2b92`,
matching the checkpoint manifest. The reader used `mode=ro&immutable=1` and
explicitly closed the connection. No network requests were made.

The code review still found a latent omission: checkpoint creation selected
artifacts from snapshots, findings, and bundles, but did not inspect generation
manifests. A staged or blocked generation can refer to an extract that never
became `snapshots.extract_sha256`. The closure now includes exact body and
extract digests from every retained generation. Missing required files fail
checkpoint creation or verification. Checkpoints without generation tables
retain their previous format and behavior.

Regression tests create actual staged and blocked generations whose extracts
are absent from snapshot pointers, then create and restore real SQLite
checkpoints. They verify the extracts survive, checkpoint verification creates
no sidecars, missing referenced extracts reject backup creation, and an older
incomplete manifest cannot disguise omitted generation support.

Machine evidence is in
[the audit receipt](../../evidence/runtime/h12/h12-generation-closure-20260913.json).
[The audit script](../../tools/runtime/audit_checkpoint_generation_closure.py) reproduces the
comparison without opening the checkpoint through a migrating runtime.
