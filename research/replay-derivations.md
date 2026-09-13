# Scratch-only derivation initialization

`research/replay_derivations.py` measures initialization through the real
`derive_one` project and link workers. It runs finite dependency cohorts:
calendar, dancer, source index, inventory, map, event, source event, history,
then links. It discovers scopes again between cohorts and sweeps. Every unit
must pass currentness, readiness, desired fingerprint, and retry eligibility
checks. It does not call the fairness selector or claim fair scheduling.

Use the reviewed H16 runtime and receipt. Preparation verifies the checkpoint's
SQLite hash and size, then uses SQLite backup from an immutable connection to
create an independent database. It copies no blobs, extracts, candidate files,
or baseline link. A dedicated marker binds the scratch path, predecessor
checkpoint, code receipt, and initial SQLite digest. Production and checkpoint
paths, same-inode databases, and linked evidence roots are rejected.

```sh
PYTHONPATH="$H16_SOURCE/src:$H16_SOURCE" "$H16_PYTHON" "$REPLAY_DRIVER" \
  --scratch /var/tmp/swingset-h16-derivation-replay \
  --source "$H16_SOURCE" --source-receipt-sha256 "$H16_RECEIPT" \
  --prepare-from /var/lib/swingset/checkpoints/h15-before-h16-20260913

PYTHONPATH="$H16_SOURCE/src:$H16_SOURCE" "$H16_PYTHON" "$REPLAY_DRIVER" \
  --scratch /var/tmp/swingset-h16-derivation-replay \
  --source "$H16_SOURCE" --source-receipt-sha256 "$H16_RECEIPT" \
  --config "$H16_SOURCE/config" --overrides "$H16_SOURCE/overrides" \
  --output /var/tmp/swingset-h16-replay-ops/attempt-01.json --max-seconds 600
```

The driver accepts those actual runtime/configuration/override bytes into the
scratch state. It creates an ordinary run and preserves worker transactions,
controls, failed-attempt latches, and retry cooldowns. Abandoned project/link
attempts use the normal interruption recovery and 60-second delay. Unresolved
fetch, parse, or publication actions require explicit reconciliation and stop
the driver. Existing parse work stays pending; no parse, fetch, build,
publication, source activation, or historical acceptance is executed.

Resume with the same scratch marker and a new output filename. Current
immutable generations are skipped; the progress file is never the authority
for completed work. A crash inside a worker rolls back its output; earlier
committed generations survive. Output filenames must be new and outside
production, scratch, source, and checkpoint roots. Atomic receipts are replaced
every 100 attempted units and on exit. They include selection time, worker
time, completed workers, failed outcomes, and final generation count.

The default invocation budget is 600 seconds (60–3600 permitted). The runner
reserves the normal 45-second atomic worker limit plus five seconds before
starting another unit. `--max-units` supports a smaller measured run. A stopped
run retains unfinished work; `current` is reported only after the complete
project/link desired query has no unfinished scopes. This is an isolated replay
measurement, not a production acceptance or publication receipt.
