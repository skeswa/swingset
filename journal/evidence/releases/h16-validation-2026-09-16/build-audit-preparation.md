# Scratch build and independent audit preparation

Reviewed 2026-09-16. Preparation only: no replay, build, audit, deployment, or
publication was launched. These templates still need the new frozen source,
source receipt, accepted bundle, and final replay receipt.

## Verified driver identities

Read directly from the VM without modifying them:

| VM path | SHA-256 |
| --- | --- |
| `/var/tmp/h16-changelog-build-driver.py` | `f0a78d6524e5af9228c8391a9e3585210c9a64a4e1bfbaabafd57bb4b9ce1fbe` |
| `/var/tmp/h16-changelog-build-monitor.py` | `452d75f1023a0085cfdc7c14da0df2142f125ca2c2dbebfc1bf81b8e8bf5cf7f` |
| `/var/tmp/h16-changelog-audit-driver.py` | `9de88bd2b5d988a00ee0529504e301b23c93eaf5018485a3f6f4db0deb2deebf` |

The retained transient unit
`/run/systemd/transient/swingset-h16-closure-build.service` confirms the old
run's environment and service properties. Its paths and source pins are old;
do not restart it.

## Required inputs and path compatibility

- A fresh full replay must finish with `status=current`, no unfinished scopes,
  no unsettled execution admissions, and the exact accepted bundle. Its receipt
  binds the scratch path, source receipt, bundle, and marker hash. A diagnostic
  validation or database-copy completion does not replace this receipt.
- The build requires explicit `--source`, `--source-receipt-sha256`, and
  `--bundle-digest`; driver defaults name an obsolete source. Set `PYTHONPATH`
  to that exact source's `src` directory and root. `SWINGSET_REVISION` must match
  the value used to capture the replay's accepted bundle.
- The build imports `research.replay_derivations`. The audit imports
  `research.v4_candidate_acceptance` and `research.v4_identity_checks`.
  The retained `2d5q3lgljfkmm78hf44g954lzwx79yhv-source` has these files. A new
  source assembled from that historical tree can retain them. A source using
  the reorganized tree needs new copies of the drivers whose imports use
  `journal.tools.runtime.replay_derivations` and
  `journal.tools.releases.v4_candidate_acceptance` / `v4_identity_checks`.
  Do not modify retained drivers or add compatibility imports from the working
  checkout; that would mix source versions. The path map does not provide
  Python import aliases.
- The build's replay helper also imports its matching `accept_h11` helper.
  Freeze the complete helper dependency set. The audit calls only
  `audit_structure` and `audit_identity`; it does not run the legacy V4 main
  command or require its old research data paths.
- Select new build-state and receipt paths outside production, checkpoints,
  the replay directory, and each other. Preserve all prior failed builds.
  The driver copies the verified V4 baseline and accepted input bundle into
  the independent build state. Do not add baseline files to replay state.
- The V4 baseline is fixed at
  `81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`. The audit expects 66 old coverage
  year rows, 4,931 named judges, and 34,955 baseline entry IDs. It explicitly
  preserves named judges with null WSDC IDs. These checks remain appropriate
  to this original H16 release; a changed baseline needs a separately reviewed
  audit.

## Command templates

These are VM root-shell templates, not commands executed by this review.
Set every required `H16_*` variable from reviewed receipts. Use fresh unit and
output names for this attempt. `orb -m swingset -u root` runs commands without
a `--` separator. The VM has no bare `python3` in this shell; use the absolute
venv interpreter below.

Read only the native library variable from the installed service; do not
print or import its credential environment:

```sh
H16_LIBS=$(/var/lib/swingset/venv/bin/python -c '
from pathlib import Path
import shlex
lines = Path("/etc/systemd/system/swingset-cycle.service").read_text().splitlines()
values = [item.split("=", 1)[1] for line in lines if line.startswith("Environment=")
          for item in shlex.split(line.removeprefix("Environment="))
          if item.startswith("LD_LIBRARY_PATH=")]
assert len(values) == 1
print(values[0])
')
```

After verifying no other heavy VM work is active, launch the build with the
same resource and environment settings as the retained full-build attempt:

```sh
systemd-run --unit="${H16_BUILD_UNIT:?}" \
  --property=User=swingset --property=Group=swingset \
  --property=RuntimeMaxSec=35min --property=TimeoutStopSec=3min \
  --property=MemoryAccounting=yes --property=UMask=0077 \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  --setenv="PYTHONPATH=${H16_SOURCE:?}/src:${H16_SOURCE:?}" \
  --setenv="SWINGSET_REVISION=${H16_REVISION:?}" \
  --setenv="LD_LIBRARY_PATH=${H16_LIBS:?}" \
  /var/lib/swingset/venv/bin/python "${H16_BUILD_DRIVER:?}" \
  --replay "${H16_REPLAY:?}" \
  --replay-receipt "${H16_REPLAY_RECEIPT:?}" \
  --state "${H16_BUILD_STATE:?}" --output "${H16_BUILD_RECEIPT:?}" \
  --source "${H16_SOURCE:?}" \
  --source-receipt-sha256 "${H16_SOURCE_RECEIPT_SHA256:?}" \
  --bundle-digest "${H16_BUNDLE:?}"
```

Immediately start the monitor in a durable root-owned session or separate
transient unit, so it can signal the build service:

```sh
systemd-run --unit="${H16_MONITOR_UNIT:?}" \
  --property=UMask=0077 --setenv=PYTHONDONTWRITEBYTECODE=1 \
  /var/lib/swingset/venv/bin/python /var/tmp/h16-changelog-build-monitor.py \
  --unit "${H16_BUILD_UNIT:?}" --output "${H16_RESOURCE_RECEIPT:?}" \
  --max-seconds 2100
```

The monitor output parent must already exist; its script does not create it.
Give its temporary output path a unique name too. The monitor samples every
five seconds and sends SIGTERM above 6 GiB process anonymous memory or its
external deadline. It does not impose an instant allocation limit, and its
own exit status does not establish build success. Inspect its final build
unit status and `terminated_reason`, the build receipt, and the journal.
The VM cap remains 8 GiB; do not overlap other heavy jobs. Preserve the
application's 45-second write deadline.

After the build unit finishes successfully, require the build receipt's
`passed=true`, `semantic_publication_preflight=true`, `network_requests=0`, and
`published=false`. Take candidate and baseline paths from that actual receipt.
Check both are inside the new independent build state. Run the independent
audit serially, under the same frozen source:

```sh
systemd-run --unit="${H16_AUDIT_UNIT:?}" \
  --property=User=swingset --property=Group=swingset \
  --property=MemoryAccounting=yes --property=UMask=0077 \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  --setenv="PYTHONPATH=${H16_SOURCE:?}/src:${H16_SOURCE:?}" \
  --setenv="SWINGSET_REVISION=${H16_REVISION:?}" \
  --setenv="LD_LIBRARY_PATH=${H16_LIBS:?}" \
  /var/lib/swingset/venv/bin/python "${H16_AUDIT_DRIVER:?}" \
  --state "${H16_BUILD_STATE:?}" --candidate "${H16_CANDIDATE:?}" \
  --baseline "${H16_BASELINE:?}" --output "${H16_AUDIT_RECEIPT:?}" \
  --temp-parent /var/tmp
```

This audit checks schemas, row counts, structural keys, current supporting
evidence, durable build completion, identity restrictions, registry-event
mapping, all 17 coverage years, and an independent coverage recount. It uses
DuckDB with a 1 GB memory limit and two threads; Python proof hydration still
adds memory outside that DuckDB limit. Require unit success and every audit
check to pass. The audit creates its final report only after completing its
checks; retain the journal when an exception leaves no report.

## Evidence and operational limits

Save the build, resource, and audit receipts plus service journals under new
names. Hash each retained file and bind the candidate ID and manifest hash to
the successful receipts. `BUILT` alone is insufficient: the failed prior build
also left files after completion rolled back.

The audit opens SQLite read-only without the replay writer lock. Run it only
after build completion with no concurrent writer. A read-only SQLite open can
create empty WAL/SHM sidecars; compare main database metadata separately and
report sidecar changes rather than deleting them to fit an expected result.
The build's early preparation errors occur before its final report handler,
so retain the service journal even when no build receipt exists.

No driver creates a remote client or publishes. None of these steps clears
the production hold, initializes production, activates a system, or authorizes
publication. The previous production pause remains a separate boundary.
