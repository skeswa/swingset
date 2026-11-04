"""Measure a manifest reader in a fresh process, independently of pytest's RSS."""

import hashlib
import json
import resource
import sys
import time
from pathlib import Path

from swingset.state import engine
from swingset.state.large_values import column_reader

state, limit = Path(sys.argv[1]), float(sys.argv[2])
started = time.monotonic()
read = 0
checksum = hashlib.sha256()
with engine.connect(engine.resolve_dsn(state), read_only=True) as conn:
    row = conn.execute(
        "SELECT rowid FROM derivation_dependency_sets WHERE dependency_set_id='large-value-test'"
    ).fetchone()
    with column_reader(conn, "derivation_dependency_sets", "manifest_json", row[0]) as source:
        expected = len(source)
        while time.monotonic() - started < limit:
            block = source.read(65536)
            if not block:
                break
            checksum.update(block)
            read += len(block)
rusage_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
if sys.platform == "linux":
    # ru_maxrss carries the fork parent's pre-exec memory high-water mark.
    # VmHWM belongs to this client's new address space, including its imports.
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    peak_bytes = int(status["VmHWM"].split()[0]) * 1024
    metric = "proc-self-status-VmHWM"
else:
    peak_bytes = rusage_peak if sys.platform == "darwin" else rusage_peak * 1024
    metric = "getrusage-ru_maxrss"
print(
    json.dumps(
        {
            "bytes": read,
            "expected_bytes": expected,
            "complete": read == expected,
            "sha256": checksum.hexdigest(),
            "seconds": time.monotonic() - started,
            "peak_rss_bytes": peak_bytes,
            "rss_metric": metric,
            "rusage_peak_rss_bytes": rusage_peak
            if sys.platform == "darwin"
            else rusage_peak * 1024,
        }
    )
)
