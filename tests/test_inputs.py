import shutil
from pathlib import Path

from swingset.clock import FakeClock
from swingset.fetch.archive import digest
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture


def test_removed_override_invalidates_links_once_and_survives_restart(tmp_path):
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    state = tmp_path / "state"
    clock = FakeClock()
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "2026-09-example",
                "slug-example",
                "Example",
                2026,
                "2026-09-01",
                "2026-09-02",
                "registry",
                "[]",
                "wsdc_calendar",
                "snapshot",
                "1",
                clock.now().isoformat(),
                clock.now().isoformat(),
                "run",
            ),
        )
        accept(db, capture(Path("config"), overrides, state, {}), clock)
        db.connection.execute("DELETE FROM pending_work")
        (overrides / "nicknames.csv").unlink()
        assert "overrides/nicknames.csv" in accept(
            db, capture(Path("config"), overrides, state, {}), clock
        )
    with open_database(state) as db:
        assert tuple(
            db.connection.execute(
                "SELECT stage,unit_kind,unit_id FROM pending_work WHERE stage='link'"
            ).fetchone()
        ) == ("link", "event", "2026-09-example")
        assert db.connection.execute(
            "SELECT digest FROM accepted_inputs WHERE input_name='overrides/nicknames.csv'"
        ).fetchone()[0] == digest(b"")
        db.connection.execute("DELETE FROM pending_work")
        assert not accept(db, capture(Path("config"), overrides, state, {}), clock)
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0


def test_invalid_override_is_rejected_before_acceptance(tmp_path):
    import pytest

    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    state = tmp_path / "state"
    clock = FakeClock()
    with open_database(state) as db:
        original = capture(Path("config"), overrides, state, {})
        accept(db, original, clock)
        db.connection.execute("DELETE FROM pending_work")
        (overrides / "identity_overrides.csv").write_text(
            "entry_id,wsdc_id,reason,author,date\nentry,not-a-number,correction,owner,2026-09-09\n"
        )
        with pytest.raises(ValueError, match="convert legacy overrides first"):
            capture(Path("config"), overrides, state, {})
        assert (
            db.connection.execute(
                "SELECT value FROM meta WHERE key='input_bundle_hash'"
            ).fetchone()[0]
            == original.digest
        )
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0
