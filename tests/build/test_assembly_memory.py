"""The streamed assembly's memory bound on a synthetic release (plan phase 3, WP8).

200 events of 1,000 placements and 2,000 callback marks each (200k and
400k), plus 2,048 dancers with 10,240 registry placements. The build runs in
a subprocess (this file run as a script), so `ru_maxrss` is the build's own.
The bound is 600 MB, scaled from the 2 GB production target; no unit of work
may hold more than 50,000 rows in Python.

The second test runs the whole partitioned build the release loop runs:
stage 1 (`partitioned.prepare`) over real generations whose rows cite
40,000 snapshots, each with an accepted source generation carrying a 4 kB
recipe as registry recipes do, then the support items coverage reads, then
the assembly. The rehearsal's 6.75 GB peak was 3.9 GB of such recipes loaded
whole (F26); this build must stay under `FULL_RSS_LIMIT`.
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

EVENTS = 200
ROUNDS = 20
PLACES = 50
ENTRIES = 2 * PLACES
DANCERS = 2048
REGISTRY_PER_DANCER = 5
RSS_LIMIT = 600 * 1024 * 1024
ROW_LIMIT = 50_000


def _event_tables(index: int) -> dict[str, list[dict[str, object]]]:
    event = f"ev{index:04d}"
    contest = f"{event}/jj"
    common = {"source": "eepro", "snapshot_id": f"snap-{index}", "parser_version": "1"}
    seen = datetime(2026, 5, 1, tzinfo=UTC)
    stamp = {"first_seen_at": seen, "last_seen_at": seen, "run_id": "synthetic"}
    judge = f"{event}/judge/1"
    entries = [
        {
            "entry_id": f"{contest}/E{number}",
            "contest_id": contest,
            "event_id": event,
            "role": "leader" if number % 2 == 0 else "follower",
            "bib": str(number),
            "name_raw": f"Dancer {index} {number}",
            "name_norm": f"dancer {index} {number}",
            "link_status": "unmatched",
            "link_confidence": 0.0,
            **common,
            **stamp,
        }
        for number in range(ENTRIES)
    ]
    rounds = [
        {"round_id": f"{contest}/r{number}", "contest_id": contest, "round_index": number}
        | common
        | stamp
        for number in range(ROUNDS)
    ]
    placements = [
        {
            "placement_id": f"{contest}/r{number}/p{place}",
            "round_id": f"{contest}/r{number}",
            "contest_id": contest,
            "event_id": event,
            "place": place,
            "leader_entry_id": f"{contest}/E{2 * (place - 1)}",
            "follower_entry_id": f"{contest}/E{2 * (place - 1) + 1}",
            "registry_confirmed": False,
            **common,
            **stamp,
        }
        for number in range(ROUNDS)
        for place in range(1, PLACES + 1)
    ]
    marks = [
        {
            "round_id": f"{contest}/r{number}",
            "entry_id": entry["entry_id"],
            "judge_id": judge,
            "mark": "yes" if place % 3 else "no",
            "mark_raw": "Y" if place % 3 else "N",
            "mark_value": 1.0 if place % 3 else 0.0,
            **common,
            **stamp,
        }
        for number in range(ROUNDS)
        for place, entry in enumerate(entries)
    ]
    return {
        "events": [
            {
                "event_id": event,
                "series_id": event,
                "name": f"Synthetic {index}",
                "year": 2026,
                "start_date": date(2026, 5, 1 + index % 20),
                "end_date": date(2026, 5, 2 + index % 20),
                "event_month": "2026-05",
                "date_precision": "day",
                "held": "held",
                "coverage_tier": "sheets_complete",
                "wsdc_status": "registry",
                **common,
                **stamp,
            }
        ],
        "contests": [
            {"contest_id": contest, "event_id": event, "name_raw": "Jack & Jill"} | common | stamp
        ],
        "rounds": rounds,
        "entries": entries,
        "judges": [
            {"judge_id": judge, "event_id": event, "name_raw": f"Judge {index}"} | common | stamp
        ],
        "placements": placements,
        "callback_marks": marks,
    }


def _dancer_tables(batch: int) -> dict[str, list[dict[str, object]]]:
    seen = datetime(2026, 5, 1, tzinfo=UTC)
    common = {
        "source": "wsdc_registry",
        "snapshot_id": f"registry-{batch}",
        "parser_version": "1",
        "first_seen_at": seen,
        "last_seen_at": seen,
        "run_id": "synthetic",
    }
    ids = range(batch * 512, (batch + 1) * 512)
    return {
        "dancers": [
            {"wsdc_id": wsdc_id, "first_name": "Dancer", "last_name": str(wsdc_id)} | common
            for wsdc_id in ids
        ],
        "registry_placements": [
            {
                "wsdc_id": wsdc_id,
                "role": "leader",
                "dance_style": "wcs",
                "division": "novice",
                "series_id": f"series-{number}",
                "event_month": date(2026, 5, 1),
                "event_id": f"ev{(wsdc_id + number) % EVENTS:04d}",
                "result": "1",
                "points": 1,
            }
            | common
            for wsdc_id in ids
            for number in range(REGISTRY_PER_DANCER)
        ],
    }


def fixture(directory: Path) -> None:
    """Write the synthetic artifacts and their manifest, one partition at a time."""
    import pyarrow as pa

    from swingset.build import artifact_store
    from swingset.build.schema import SCHEMAS
    from swingset.state.db import open_database

    built: list[list[str]] = []
    with open_database(directory / "state") as database:
        names = [f"dancers/{batch}" for batch in range(DANCERS // 512)]
        names += [f"event/ev{index:04d}" for index in range(EVENTS)]
        for name in names:
            kind, _, key = name.partition("/")
            tables = _dancer_tables(int(key)) if kind == "dancers" else _event_tables(int(key[2:]))
            digest, _size = artifact_store.write(
                database.state_dir,
                {
                    table: pa.Table.from_pylist(rows, schema=SCHEMAS[table])
                    for table, rows in tables.items()
                },
                {"partition": name, "withheld_subjects": {}},
            )
            built.append([name, digest])
    (directory / "built.json").write_text(json.dumps(built))


def build(directory: Path) -> dict[str, object]:
    """Assemble and write one candidate from `fixture`'s artifacts."""
    from swingset.build import artifact_store
    from swingset.build.assembly import Assembly
    from swingset.build.builder import BuildInput, BuildMetadata
    from swingset.build.manifest import ReleaseManifest
    from swingset.build.schema import PRIMARY_KEYS, SCHEMAS
    from swingset.publish.card import render_card
    from swingset.state.db import open_database

    built = json.loads((directory / "built.json").read_text())
    with open_database(directory / "state") as database:
        state_dir = database.state_dir
        sidecars = {digest: artifact_store.sidecar(state_dir, digest) for _, digest in built}
        manifest = ReleaseManifest("synthetic", {"built": built, "withheld": []}, 0, {}, sidecars)
        cutoff = datetime(2026, 9, 1, tzinfo=UTC)
        assembly = Assembly(
            state_dir,
            manifest,
            baseline=None,
            suppressions=(),
            history_start=date(2010, 1, 1),
            cutoff=cutoff,
        )
        try:
            with database.transaction(immediate=False) as conn:
                data = assembly.run(
                    conn,
                    support=[],
                    data=BuildInput(
                        {}, SCHEMAS, PRIMARY_KEYS, {}, {}, "synthetic", date(2010, 1, 1)
                    ),
                    policy={"mode": "partitioned", "path": "partitioned"},
                )
            meta = BuildMetadata(
                "synthetic", "synthetic", None, 1, {"repository": "synthetic"}, b"", cutoff
            )
            result = assembly.write(meta, render_card)
        finally:
            assembly.close()
        manifest_json = json.loads((result.path / "_meta/manifest.json").read_bytes())
        return {
            "peak_rows": assembly.meter.peak,
            "units": assembly.meter.units,
            "row_counts": manifest_json["row_counts"],
            "release_policy_mode": data.release_policy["mode"] if data.release_policy else None,
            "scratch_left": sorted(path.name for path in (state_dir / "tmp").glob("assembly-*")),
        }


#: The whole-build fixture: events, and snapshots each event's rows cite.
FULL_EVENTS = 200
FULL_SNAPSHOTS = 40_000
#: About one registry recipe: the accepted inputs are most of its bytes.
RECIPE_INPUTS = 48
FULL_RSS_LIMIT = 900 * 1024 * 1024


def _canonical(value: object) -> str:
    from swingset.build.closure_manifest import canonical

    return canonical(value)


def full_fixture(directory: Path) -> None:
    """Accepted source generations, live observations and event generations, in SQL."""
    import hashlib

    from swingset.state.db import open_database

    now = "2026-09-01T00:00:00+00:00"
    inputs = [
        ["pipeline", f"version/extract/synthetic.kind{number}", "x" * 40 + str(number)]
        for number in range(RECIPE_INPUTS)
    ]
    with open_database(directory / "state") as database, database.transaction() as conn:
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('synthetic',?,1)", (now,))
        conn.execute(
            "INSERT INTO admission_policies(page_kind,contract_version,policy_revision) "
            "VALUES ('synthetic.round','1','r1')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES ('empty','[]')"
        )
        for number in range(FULL_SNAPSHOTS):
            watch, snapshot = f"w{number}", f"s{number}"
            event = f"ev{number % FULL_EVENTS:04d}"
            conn.execute(
                "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) "
                "VALUES (?,'eepro','round','GET',?,'synthetic.round','live')",
                (watch, f"https://example.test/{number}"),
            )
            conn.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
                "body_bytes,content_changed,run_id,classification) "
                "VALUES (?,?,'GET',?,?,200,1,1,'synthetic','Ok')",
                (snapshot, watch, f"https://example.test/{number}", now),
            )
            payload = {"round": number, "event": event, "marks": list(range(12))}
            observation = {"kind": "round_sheet", "scope": {"kind": "event", "ref": event}}
            conn.execute(
                "INSERT INTO observations VALUES (?,?,?,'round_sheet','event',?,0,'1','1',?)",
                (f"o{number}", watch, snapshot, event, _canonical(payload)),
            )
            recipe = {
                "context": {"snapshot_id": snapshot, "source": "eepro", "watch_id": watch},
                "parser_version": "1",
                "extract_version": "1",
                "contract_version": "1",
                "accepted_inputs": inputs,
            }
            conn.execute(
                "INSERT INTO source_units(unit_key,watch_id,page_kind,accepted_generation_id) "
                "VALUES (?,?,'synthetic.round',NULL)",
                (f"u{number}", watch),
            )
            generation = f"gen_{number:06d}"
            conn.execute(
                "INSERT INTO source_generations(generation_id,unit_key,page_kind,contract_version,"
                "input_fingerprint,manifest_json,recipe_json,result_json,report_json,created_at,"
                "run_id,state,removal_authority) VALUES (?,?,'synthetic.round','1',?,'[]',?,?,"
                "'{\"failures\":[]}',?,'synthetic','accepted','none')",
                (
                    generation,
                    f"u{number}",
                    generation,
                    _canonical(recipe),
                    _canonical({"observations": [{**observation, "payload": payload}]}),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO admission_decisions(generation_id,state,reason,decided_at,"
                "policy_revision) VALUES (?,'accepted','synthetic',?,'r1')",
                (generation, now),
            )
        for index in range(FULL_EVENTS):
            event = f"ev{index:04d}"
            contest = f"{event}/jj"
            common = {"source": "eepro", "parser_version": "1"}
            output = [
                (
                    "events",
                    _canonical([event]),
                    {
                        "event_id": event,
                        "series_id": event,
                        "name": f"Synthetic {index}",
                        "year": 2026,
                        "start_date": "2026-05-01",
                        "end_date": "2026-05-02",
                        "event_month": "2026-05",
                        "date_precision": "day",
                        "wsdc_status": "registry",
                        "snapshot_id": f"s{index}",
                        **common,
                    },
                ),
                (
                    "contests",
                    _canonical([contest]),
                    {
                        "contest_id": contest,
                        "event_id": event,
                        "name_raw": "Jack & Jill",
                        "snapshot_id": f"s{index}",
                        **common,
                    },
                ),
            ]
            output += [
                (
                    "rounds",
                    _canonical([f"{contest}/r{number}"]),
                    {
                        "round_id": f"{contest}/r{number}",
                        "contest_id": contest,
                        "round_index": 1,
                        "snapshot_id": f"s{number}",
                        **common,
                    },
                )
                for number in range(index, FULL_SNAPSHOTS, FULL_EVENTS)
            ]
            digest = hashlib.sha256()
            created = f"dg_{index:04d}"
            for ordinal, (table, key, payload) in enumerate(output):
                stored = _canonical(payload)
                sha = hashlib.sha256(stored.encode()).hexdigest()
                conn.execute(
                    "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (sha, stored)
                )
                conn.execute(
                    "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
                    (created, ordinal, table, key, sha),
                )
                digest.update(_canonical((table, key, stored)).encode() + b"\n")
            conn.execute(
                "INSERT INTO derivation_generations VALUES "
                "(?,'project','event',?,'f','{}','empty',NULL,?,?,?,'synthetic')",
                (created, event, digest.hexdigest(), len(output), now),
            )
            # The canonical row the generation materialized: selection
            # partitions only events state holds (D-0262).
            conn.execute(
                "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,"
                "wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,"
                "last_seen_at,run_id) VALUES (?,?,?,2026,'2026-05-01','2026-05-02',"
                "'registry','[\"eepro\"]','eepro',?,'1',?,?,'synthetic')",
                (event, event, f"Synthetic {index}", f"s{index}", now, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
                "VALUES ('project','event',?,?)",
                (event, now),
            )
            conn.execute(
                "UPDATE derivation_scopes SET materialized_generation_id=? "
                "WHERE stage='project' AND unit_kind='event' AND unit_id=?",
                (created, event),
            )


def full_build(directory: Path) -> dict[str, object]:
    """Stage 1, the manifest, the support items and the streamed assembly."""
    from swingset.build import partitioned, partitions
    from swingset.build.assembly import Assembly
    from swingset.build.builder import BuildInput, BuildMetadata
    from swingset.build.schema import PRIMARY_KEYS, SCHEMAS
    from swingset.publish.card import render_card
    from swingset.state.db import open_database

    # The synthetic units have no recipes, so currency cannot be formed: take
    # them as current (a patch in this subprocess only).
    partitions._stale_units = lambda conn, partition: []  # type: ignore[attr-defined]
    cutoff = datetime(2026, 9, 1, tzinfo=UTC)
    with open_database(directory / "state") as database:
        release = partitioned.prepare(
            database,
            {},
            history_start=date(2010, 1, 1),
            baseline=None,
            run_id="synthetic",
            now=cutoff,
        )
        state_dir = database.state_dir
        assembly = Assembly(
            state_dir,
            release,
            baseline=None,
            suppressions=(),
            history_start=date(2010, 1, 1),
            cutoff=cutoff,
        )
        try:
            with database.transaction(immediate=False) as conn:
                support = partitioned.source_support(conn, release.support)
                data = assembly.run(
                    conn,
                    support=support,
                    data=BuildInput(
                        {}, SCHEMAS, PRIMARY_KEYS, {}, {}, "synthetic", date(2010, 1, 1)
                    ),
                    policy={"mode": "partitioned", "path": "partitioned"},
                )
            meta = BuildMetadata(
                "synthetic", "synthetic", None, 1, {"repository": "synthetic"}, b"", cutoff
            )
            result = assembly.write(meta, render_card)
        finally:
            assembly.close()
        manifest_json = json.loads((result.path / "_meta/manifest.json").read_bytes())
        return {
            "report": {
                key: value
                for key, value in release.report.items()
                if key in {"built", "withheld", "rebuilt", "partitions"}
            },
            "support": len(release.support),
            "accepted": sum(1 for item in release.support if item[2] == "accepted"),
            "row_counts": manifest_json["row_counts"],
            "mode": data.release_policy["mode"] if data.release_policy else None,
        }


def main(step: str, directory: str) -> None:
    if step == "fixture":
        fixture(Path(directory))
        return
    if step == "full_fixture":
        full_fixture(Path(directory))
        return
    report = full_build(Path(directory)) if step == "full_build" else build(Path(directory))
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes; macOS reports bytes.
    report["max_rss_bytes"] = usage if sys.platform == "darwin" else usage * 1024
    print(json.dumps(report))


def test_a_synthetic_release_streams_under_the_memory_bound(tmp_path):
    for step in ("fixture", "build"):
        # Separate processes: the build's peak excludes writing the fixture.
        process = subprocess.run(
            [sys.executable, __file__, step, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert process.returncode == 0, process.stderr[-4000:]
    report = json.loads(process.stdout.strip().splitlines()[-1])
    counts = report["row_counts"]
    assert counts["placements"] == EVENTS * ROUNDS * PLACES == 200_000
    assert counts["callback_marks"] == EVENTS * ROUNDS * ENTRIES == 400_000
    assert counts["registry_placements"] == DANCERS * REGISTRY_PER_DANCER
    assert counts["changelog"] > counts["placements"]
    assert report["release_policy_mode"] == "partitioned" and report["scratch_left"] == []
    assert report["peak_rows"] < ROW_LIMIT, report["units"]
    assert report["max_rss_bytes"] < RSS_LIMIT, report


def test_the_whole_partitioned_build_stays_under_its_memory_bound(tmp_path):
    """F26: stage 1, the support items and the assembly in one process, as the
    release loop runs them, with 40,000 accepted recipes of about 4 kB."""
    for step in ("full_fixture", "full_build"):
        process = subprocess.run(
            [sys.executable, __file__, step, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        assert process.returncode == 0, process.stderr[-4000:]
    report = json.loads(process.stdout.strip().splitlines()[-1])
    assert report["report"]["built"] == FULL_EVENTS, report
    assert report["support"] == report["accepted"] == FULL_SNAPSHOTS
    assert report["row_counts"]["rounds"] == FULL_SNAPSHOTS
    assert report["mode"] == "partitioned"
    assert report["max_rss_bytes"] < FULL_RSS_LIMIT, report


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
