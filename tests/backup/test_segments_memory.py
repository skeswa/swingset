"""A backup set's memory bound (architecture phase 4, WP6; invariant I10).

A synthetic state of 50,000 objects (25,000 bodies a host row names and
25,000 `derivation_output` objects) and a database of about 200 MB. The set
is written in a subprocess (this file run as a script), so `ru_maxrss` is the
writer's own: the database streams through zstd in chunks and the segments
stream file by file, so it stays under 300 MB.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import resource
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

OBJECTS = 50_000
DATABASE_BYTES = 200 * 1024 * 1024
RSS_LIMIT = 300 * 1024 * 1024
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def fixture(state: Path) -> None:
    from swingset.state.db import open_database

    bodies = OBJECTS // 2
    with open_database(state) as database:
        conn = database.connection
        hosts = []
        for index in range(bodies):
            body = f"body {index}\n".encode() * 8
            digest = hashlib.sha256(body).hexdigest()
            path = state / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(body, mtime=0))
            hosts.append((f"h{index}.example", digest))
        outputs = []
        for index in range(OBJECTS - bodies):
            raw = f"row {index}\n".encode() * 8
            digest = hashlib.sha256(raw).hexdigest()
            path = state / "objects" / "derivation_output" / digest[:2] / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(raw, mtime=0))
            outputs.append((digest, len(raw), path.stat().st_size, NOW.isoformat()))
        with database.transaction():
            conn.executemany(
                "INSERT INTO hosts(host,pause_streak,robots_sha256) VALUES (?,0,?)", hosts
            )
            conn.executemany(
                "INSERT INTO objects(class,digest,raw_bytes,stored_bytes,stored_at) "
                "VALUES ('derivation_output',?,?,?,?)",
                outputs,
            )
            conn.execute("CREATE TABLE filler(x BLOB)")
        chunk = 4 * 1024 * 1024
        for _ in range(DATABASE_BYTES // chunk):
            with database.transaction():
                conn.execute("INSERT INTO filler VALUES (?)", (os.urandom(chunk),))


def write(state: Path) -> dict[str, object]:
    from swingset.backup.segments import LOCAL_TRANSPORT, create_set
    from swingset.backup.transport import LocalDirectoryTransport
    from swingset.state.db import open_database

    with open_database(state) as database:
        result = create_set(
            database,
            LocalDirectoryTransport(state / "checkpoints", name=LOCAL_TRANSPORT),
            now=NOW,
            versions={},
            part_bytes=64 * 1024 * 1024,
            segment_bytes=32 * 1024 * 1024,
        )
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "items": result["items"],
        "parts": result["parts"],
        "db_raw_bytes": result["db_raw_bytes"],
        # Linux reports kilobytes; macOS reports bytes.
        "max_rss_bytes": usage if sys.platform == "darwin" else usage * 1024,
    }


def main(step: str, directory: str) -> None:
    if step == "fixture":
        fixture(Path(directory))
        return
    print(json.dumps(write(Path(directory))))


def test_a_set_streams_under_its_memory_bound(tmp_path: Path) -> None:
    state = tmp_path / "state"
    for step in ("fixture", "write"):
        process = subprocess.run(
            [sys.executable, __file__, step, str(state)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        assert process.returncode == 0, process.stderr[-4000:]
    report = json.loads(process.stdout.strip().splitlines()[-1])
    assert report["items"] == OBJECTS, report
    assert report["db_raw_bytes"] >= DATABASE_BYTES, report
    assert report["parts"] >= 1, report
    assert report["max_rss_bytes"] < RSS_LIMIT, report


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
