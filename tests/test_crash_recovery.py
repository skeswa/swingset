import json
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from swingset.state.db import open_database

SCRIPT = Path("tests/helpers/crash_cycle.py")


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
        return [
            tuple(row)
            for row in db.connection.execute(
                "SELECT event_id,name,start_date,end_date,wsdc_status FROM events ORDER BY event_id"
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


def test_restart_before_and_after_every_cycle_transaction(tmp_path):
    overrides, config = setup_inputs(tmp_path)
    normal = invoke(tmp_path / "normal", overrides, config)
    assert normal.returncode == 0, normal.stderr
    boundaries = json.loads(normal.stdout)["transactions"]
    expected = belief(tmp_path / "normal")
    assert boundaries >= 5
    for boundary in range(1, boundaries + 1):
        for side in ("before", "after"):
            state = tmp_path / f"{side}-{boundary}"
            crashed = invoke(state, overrides, config, f"{side}:{boundary}")
            assert crashed.returncode == 91, (side, boundary, crashed.stderr)
            restarted = invoke(state, overrides, config)
            assert restarted.returncode == 0, restarted.stderr
            assert belief(state) == expected, (side, boundary)


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
