import json
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from swingset.publish.safety import verify_candidate_files
from swingset.state.db import open_database
from swingset.state.work import unfinished_units

SCRIPT = Path("tests/helpers/crash_cycle.py")
CHECKPOINTS = (
    "inputs_accepted",
    "snapshot_saved",
    "parse_snapshot_completed",
    "project_calendar_completed",
    "project_inventory_completed",
    "project_map_completed",
    "project_event_completed",
    "project_history_completed",
    "link_event_completed",
    "build_completed",
)


def invoke(state, overrides, config, fault="none"):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(state), str(overrides), str(config), fault],
        capture_output=True,
        text=True,
        timeout=15,
    )


def belief(state):
    with open_database(state, lock=False, read_only=True) as db:
        assert db.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0
        assert list(unfinished_units(db.connection)) == []
        builds = db.connection.execute(
            "SELECT r.payload_json FROM derivation_scopes s "
            "JOIN derivation_rows r ON r.generation_id=s.materialized_generation_id "
            "WHERE s.stage='build' AND r.table_name='artifact'"
        ).fetchall()
        assert builds, "recovery must finish a durable build, not only recreate event rows"
        for row in builds:
            artifact = json.loads(row[0])
            candidate = Path(artifact["path"])
            verify_candidate_files(candidate)
            built = json.loads((candidate / "BUILT").read_bytes())
            assert built["manifest_hash"] == artifact["manifest_hash"]
        return [
            tuple(row)
            for row in db.connection.execute(
                "SELECT event_id,series_id,name,year,start_date,end_date,city,region,country,"
                "website,wsdc_status,sources FROM events ORDER BY event_id"
            )
        ]


def setup_inputs(tmp_path):
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (overrides / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    config = tmp_path / "config"
    shutil.copytree("config", config)
    (config / "sources.toml").write_text(
        '[sources.wsdc_calendar]\nenabled = true\nindex_urls = ["https://worldsdc.com/events/"]\n'
    )
    return overrides, config


@pytest.fixture(scope="module")
def uninterrupted_cycle(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("uninterrupted-cycle")
    overrides, config = setup_inputs(tmp_path)
    normal = invoke(tmp_path / "normal", overrides, config)
    assert normal.returncode == 0, normal.stderr
    trace = json.loads(normal.stdout)
    assert set(trace["checkpoints"]) == set(CHECKPOINTS)
    expected = belief(tmp_path / "normal")
    assert len(expected) == 1
    return overrides, config, trace, expected


def assert_recovery(state, overrides, config, boundary, side, expected):
    crashed = invoke(state, overrides, config, f"{side}:{boundary}")
    assert crashed.returncode == 91, (side, boundary, crashed.stderr)
    restarted = invoke(state, overrides, config)
    assert restarted.returncode == 0, restarted.stderr
    assert belief(state) == expected, (side, boundary)


@pytest.mark.core
@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
@pytest.mark.parametrize("side", ("before", "after"))
def test_restart_at_pipeline_checkpoint(tmp_path, uninterrupted_cycle, checkpoint, side):
    overrides, config, trace, expected = uninterrupted_cycle
    assert_recovery(
        tmp_path / "crashed",
        overrides,
        config,
        trace["checkpoints"][checkpoint],
        side,
        expected,
    )


@pytest.mark.extended
def test_restart_before_and_after_remaining_cycle_transactions(tmp_path, uninterrupted_cycle):
    overrides, config, trace, expected = uninterrupted_cycle
    boundaries = trace["transactions"]
    assert boundaries >= 5
    selected = set(trace["checkpoints"].values())
    for boundary in range(1, boundaries + 1):
        if boundary in selected:
            continue
        for side in ("before", "after"):
            state = tmp_path / f"{side}-{boundary}"
            assert_recovery(state, overrides, config, boundary, side, expected)
            # Each copy is about 21 MiB and is needed for one check only; ninety
            # of them per run were most of a 15 GiB temp tree (D-0170).
            shutil.rmtree(state, ignore_errors=True)


@pytest.mark.core
def test_sigterm_stops_at_boundary_and_next_cycle_finishes(tmp_path):
    overrides, config = setup_inputs(tmp_path)
    state = tmp_path / "stopped"
    child = subprocess.Popen(
        [sys.executable, str(SCRIPT), str(state), str(overrides), str(config), "sigterm"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "READY"
    child.send_signal(signal.SIGTERM)
    output, error = child.communicate(timeout=10)
    assert child.returncode == 0, error
    assert json.loads(output)["stopped"] is True
    restarted = invoke(state, overrides, config)
    assert restarted.returncode == 0, restarted.stderr
    assert len(belief(state)) == 1
