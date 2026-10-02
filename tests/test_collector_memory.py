"""The collector's memory bound (architecture phase 4, WP5; invariant I10).

A synthetic state of 200,000 labels, 100,000 objects and 300,000 dependency
sets: 150,000 sets named by labels, 50,000 named only through another set's
JSON, and 100,000 nothing names; half the objects are rooted. The mark and
sweep run in a subprocess (this file run as a script), so `ru_maxrss` is the
collector's own. It stays under 300 MB and within 1.5 times a run ten times
smaller: no Python set holds a class's roots.
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

LABELS = 200_000
OBJECTS = 100_000
SETS = 300_000
RSS_LIMIT = 300 * 1024 * 1024
RATIO = 1.5
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
OLD = (NOW - timedelta(days=3)).isoformat()


def fixture(directory: Path, scale: int) -> None:
    from swingset.build.closure_manifest import canonical
    from swingset.state.db import open_database

    labels, count, sets = LABELS // scale, OBJECTS // scale, SETS // scale
    named, children = sets // 2, sets // 6
    with open_database(directory) as database:
        run = database.start_run(NOW)
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
                "VALUES ('project','event','u',?)",
                (OLD,),
            )
            conn.executemany(
                "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
                "VALUES (?,?)",
                (
                    (
                        f"set{index:07d}",
                        canonical(
                            [
                                {
                                    "fingerprint": f"child{index:07d}",
                                    "key": "g",
                                    "kind": "dependency_set",
                                }
                            ]
                            if index < children
                            else [{"kind": "input_row", "row": {"index": index}}]
                        ),
                    )
                    for index in range(named)
                ),
            )
            conn.executemany(
                "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
                "VALUES (?,?)",
                ((f"child{index:07d}", "[]") for index in range(children)),
            )
            conn.executemany(
                "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
                "VALUES (?,?)",
                (
                    (f"orphan{index:07d}", canonical([{"kind": "input_row", "row": {"i": index}}]))
                    for index in range(sets - named - children)
                ),
            )
            conn.executemany(
                "INSERT INTO derivation_generations VALUES (?,'project','event','u','f','{}',?,"
                "NULL,?,0,?,?)",
                (
                    (f"dg{index:07d}", f"set{index % named:07d}", f"{index:064x}", OLD, run)
                    for index in range(labels)
                ),
            )
            conn.executemany(
                "INSERT INTO objects(class,digest,raw_bytes,stored_bytes,stored_at) "
                "VALUES ('derivation_output',?,1,1,?)",
                # Even ones name a label's output; odd ones nothing.
                (
                    (f"{index if index % 2 == 0 else labels + index:064x}", OLD)
                    for index in range(count)
                ),
            )


def collect(directory: Path) -> dict[str, object]:
    from swingset.state import collector
    from swingset.state.db import open_database

    with open_database(directory) as database:
        result = collector.sweep(database, now=NOW)
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "removed": {cls: entry["removed"] for cls, entry in result["classes"].items()},
        # Linux reports kilobytes; macOS reports bytes.
        "max_rss_bytes": usage if sys.platform == "darwin" else usage * 1024,
    }


def main(step: str, directory: str, scale: str) -> None:
    if step == "fixture":
        fixture(Path(directory), int(scale))
        return
    print(json.dumps(collect(Path(directory))))


def run(tmp_path: Path, scale: int) -> dict[str, object]:
    directory = tmp_path / f"state-{scale}"
    for step in ("fixture", "collect"):
        process = subprocess.run(
            [sys.executable, __file__, step, str(directory), str(scale)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        assert process.returncode == 0, process.stderr[-4000:]
    report: dict[str, object] = json.loads(process.stdout.strip().splitlines()[-1])
    return report


def test_the_collector_streams_under_its_memory_bound(tmp_path):
    small = run(tmp_path, 10)
    large = run(tmp_path, 1)
    # Each orphan set but the highest goes; every odd object goes.
    assert large["removed"]["dependency_set"] == SETS - SETS // 2 - SETS // 6 - 1, large
    assert large["removed"]["derivation_output"] == OBJECTS // 2, large
    assert small["removed"]["dependency_set"] == (SETS - SETS // 2 - SETS // 6) // 10 - 1, small
    assert large["max_rss_bytes"] < RSS_LIMIT, large
    assert large["max_rss_bytes"] < RATIO * small["max_rss_bytes"], (large, small)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
