"""The weekly verify sample finds a read-set miss and is quiet otherwise (plan section 10).

The fixture is test_link_read_sets's: twenty events whose links read a small
registry, with real dancer, event, year and link units.
"""

import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest
import test_link_read_sets as linked

from swingset.clock import FakeClock
from swingset.loops import maintain
from swingset.loops.runtime import tick
from swingset.project.process import process_unit
from swingset.state import derivations, read_index, verify
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

NOW = linked.NOW


@pytest.fixture
def world(tmp_path):
    with open_database(tmp_path / "state", lock=False) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run',?,1)", (NOW.isoformat(),)
        )
        linked._events(conn)
        w = linked.World(db=db, conn=conn, clock=FakeClock(NOW), people={}, pages=0)
        for wsdc_id, (first, last) in linked.REGISTRY.items():
            w.registry(wsdc_id, first=first, last=last, pro=wsdc_id == 12)
        for unit in (
            *(WorkUnit("project", "event", event) for event in linked.EVENTS),
            WorkUnit("project", "year", "2026"),
        ):
            process_unit(db, unit, linked.Bundle(), w.clock, "run")
        w.link_all()
        yield w


def _state(conn):
    """Everything a verify must leave as it found it."""
    return {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "derivation_generations",
            "derivation_equivalences",
            "derivation_reads",
            "pending_work",
            "identity_links",
            "link_candidates",
            "runs",
        )
    } | {
        "pointers": conn.execute(
            "SELECT group_concat(materialized_generation_id) FROM ("
            "SELECT materialized_generation_id FROM derivation_scopes ORDER BY stage,unit_kind,unit_id)"
        ).fetchone()[0],
        "links": conn.execute(
            "SELECT group_concat(subject_id||':'||coalesce(wsdc_id,'')) FROM ("
            "SELECT subject_id,wsdc_id FROM identity_links ORDER BY subject_id)"
        ).fetchone()[0],
    }


def test_a_clean_sample_is_quiet_and_leaves_nothing_behind(world, tmp_path):
    w = world
    before = _state(w.conn)
    summary = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    assert summary["mismatches"] == [] and summary["failures"] == []
    assert summary["kinds"]["link"]["verified"] == len(linked.EVENTS)
    assert summary["kinds"]["link"]["matched"] == len(linked.EVENTS)
    assert summary["kinds"]["year"] == {
        "units": 1,
        "target": 1,
        "verified": 1,
        "matched": 1,
        "not_current": 0,
        "superseded": 0,
        "failed": 0,
    }
    assert _state(w.conn) == before
    assert not (w.db.state_dir / "ALERTS.log").exists()
    assert w.stale() == set()


def test_a_dancer_row_changed_without_a_generation_is_a_read_set_miss(world):
    w = world
    # A dancer whose name some link's candidates matched, renamed in place:
    # no dancer generation moves, so every link still reads as current.
    wsdc_id, event = w.conn.execute(
        "SELECT c.wsdc_id,e.event_id FROM link_candidates c JOIN entries e "
        "ON e.entry_id=c.subject_id ORDER BY c.wsdc_id,e.event_id LIMIT 1"
    ).fetchone()
    w.conn.execute(
        "UPDATE dancers SET first_name='Zebulon',last_name='Quixote' WHERE wsdc_id=?",
        (wsdc_id,),
    )
    assert w.stale() == set()
    summary = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    missed = {item["unit"] for item in summary["mismatches"]}
    assert f"link/event/{event}" in missed
    item = next(i for i in summary["mismatches"] if i["unit"] == f"link/event/{event}")
    assert item["live"] == item["expected"] != item["found"]
    assert summary["alerted"] is True
    lines = (w.db.state_dir / "ALERTS.log").read_text().splitlines()
    assert any(f"[read-set-miss] link/event/{event}: pointer " in line for line in lines)
    assert lines[0][:20] == NOW.strftime("%Y-%m-%dT%H:%M:%SZ")[:20]
    # Rolled back: the rename is still the only change.
    assert w.stale() == set()


def test_the_sample_is_deterministic_by_week_and_verifies_only_current_units(world):
    w = world
    seed = verify.week(NOW)
    assert seed == "2026-W09"
    order = verify.ranked(w.conn, "link", "event", seed=seed)
    assert order == verify.ranked(w.conn, "link", "event", seed=seed)
    assert order != verify.ranked(w.conn, "link", "event", seed="2026-W10")
    assert verify.target(2934, 0.01) == 30
    assert verify.target(21, 0.01) == 1
    assert verify.target(0, 0.01) == 0
    # The first unit of the week is stale: the sample skips it and takes the next.
    first = order[0]
    dancer = next(
        key for key in sorted(read_index.keys(w.conn, first)) if key.startswith("project/dancer/")
    )
    w.registry(int(dancer.rsplit("/", 1)[1]), recent=2020)
    assert not derivations.current(w.conn, first)
    summary = verify.run(
        w.db, linked.Bundle(), w.clock, "verify_run", fraction=0.05, kinds=["link"]
    )
    counts = summary["kinds"]["link"]
    assert counts["target"] == 1 and counts["verified"] == 1
    assert counts["not_current"] >= 1
    assert summary["mismatches"] == []


def test_kinds_are_checked():
    assert verify.parse_kinds("link, year,link") == ("link", "year")
    with pytest.raises(ValueError):
        verify.parse_kinds("map")
    with pytest.raises(ValueError):
        verify.parse_kinds("")


def test_maintain_verifies_once_a_week(tmp_path):
    config = tmp_path / "config"
    shutil.copytree("config", config)
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    state = tmp_path / "state"
    clock = FakeClock(datetime(2026, 9, 29, 3, 30, tzinfo=UTC))

    def once():
        with tick(
            "maintain",
            state=state,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            budget=60,
        ) as current:
            maintain.run(current)
        assert current.record is not None and not current.record["failed"]
        return current.record

    first = once()
    assert first["verify"]["week"] == "2026-W40"
    assert first["verify"]["mismatches"] == []
    clock.current += timedelta(days=1)
    assert "verify" not in once()
    clock.current += timedelta(days=6)
    assert "verify" in once()
    with open_database(state, lock=False) as db:
        rows = db.connection.execute(
            "SELECT count(*) FROM runs WHERE loop='maintain' "
            "AND json_extract(summary_json,'$.verify.week') IS NOT NULL"
        ).fetchone()[0]
    assert rows == 2


def test_the_cli_prints_the_summary(tmp_path, capsys):
    from swingset.cli import main

    state = tmp_path / "state"
    with open_database(state):
        pass
    assert main(["verify-derivations", "--state", str(state), "--sample", "0.5"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert set(summary["kinds"]) == {"link", "year"}
    assert main(["verify-derivations", "--state", str(state), "--kinds", "map"]) == 1


def test_partitions_withheld_counts_by_reason(tmp_path, capsys):
    from swingset.cli import main
    from swingset.state import release_store

    state = tmp_path / "state"
    with open_database(state) as db, db.transaction() as conn:
        for name, reason in (("event/a", "source_revoked"), ("event/b", "dangling_reference")):
            release_store.upsert(
                conn,
                release_store.PartitionRow(
                    name,
                    "d",
                    "withheld",
                    NOW.isoformat(),
                    "run",
                    reason=reason,
                    detail_json='{"events":["a"]}',
                ),
            )
        release_store.upsert(
            conn,
            release_store.PartitionRow(
                "event/c", "d", "built", NOW.isoformat(), "run", artifact_digest="x"
            ),
        )
        conn.execute(
            "INSERT INTO release_partitions VALUES ('event/d','d',NULL,'withheld',"
            "'source_revoked',NULL,?,'run')",
            (NOW.isoformat(),),
        )
    assert main(["partitions", "--state", str(state), "--withheld"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["withheld"] == 3
    assert report["by_reason"] == {"source_revoked": 2, "dangling_reference": 1}
    assert [item["partition"] for item in report["partitions"]] == ["event/a", "event/b", "event/d"]
    assert report["partitions"][0]["detail"] == {"events": ["a"]}
    assert main(["partitions", "--state", str(state)]) == 1
    assert main(["partitions", "list", "--state", str(state), "--withheld"]) == 1


def test_a_rerun_that_raises_nondeterministic_output_is_a_read_set_miss(world, monkeypatch):
    """F29: on real data the seeded miss raised NondeterministicOutputError,
    which the sample counted as a failure, so no alert fired. The unit is
    current, so its inputs are the recorded ones: other output is a miss."""
    import swingset.link as link

    w = world
    victim = linked.EVENTS[3]
    original = link.link_event

    def raising(database, event_id, *args, **kwargs):
        if event_id == victim:
            raise derivations.NondeterministicOutputError("derivation output is not deterministic")
        return original(database, event_id, *args, **kwargs)

    monkeypatch.setattr(link, "link_event", raising)
    logged = []
    monkeypatch.setattr(verify, "log", lambda event, **fields: logged.append((event, fields)))
    summary = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    assert summary["failures"] == []
    item = next(i for i in summary["mismatches"] if i["unit"] == f"link/event/{victim}")
    assert item["reason"] == "nondeterministic_output"
    assert item["found"] is None and item["expected"] == item["live"]
    assert summary["kinds"]["link"]["verified"] == len(linked.EVENTS)
    assert ("read_set_miss", item) in logged
    lines = (w.db.state_dir / "ALERTS.log").read_text().splitlines()
    assert any(
        f"[read-set-miss] link/event/{victim}: pointer " in line
        and "nondeterministic_output" in line
        for line in lines
    )


def test_only_a_complete_sample_makes_the_next_one_wait(world, monkeypatch):
    w = world
    calls = iter(range(1000))
    summary = verify.run(
        w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0, stop=lambda: next(calls) > 3
    )
    assert summary["stopped"] is True and summary["complete"] is False
    assert summary["peak_rss_bytes"] > 0 and summary["longest_unit_seconds"] > 0
    full = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    assert full["complete"] is True

    def record(run_id, value):
        w.conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,loop,summary_json) "
            "VALUES (?,?,?,1,'maintain',?)",
            (run_id, NOW.isoformat(), NOW.isoformat(), json.dumps({"verify": value})),
        )

    later = NOW + timedelta(hours=1)
    assert verify.due(w.conn, now=later)
    record("partial", summary)
    assert verify.due(w.conn, now=later)
    # Not stopped, but short of the target: failed units verify nothing.
    with monkeypatch.context() as patch:
        patch.setattr(
            verify, "verify_unit", lambda *a, **k: {"unit": "u", "state": "failed", "error": "E"}
        )
        short = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    assert short["stopped"] is False and short["failures"]
    assert any(counts["verified"] < counts["target"] for counts in short["kinds"].values())
    assert short["complete"] is False
    record("short", short)
    assert verify.due(w.conn, now=later)
    record("whole", full)
    assert not verify.due(w.conn, now=later)
    assert verify.due(w.conn, now=NOW + verify.INTERVAL + timedelta(seconds=1))
