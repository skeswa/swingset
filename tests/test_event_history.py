"""Receipt pages preserve their cutoff and distinguish records from lifetime history."""

import json
import sqlite3

import pytest

from swingset.schedule.event_history import report
from swingset.state.db import open_database


def append(conn, *, source_ref="eepro:test", transition="assessment_changed", prior=None):
    return conn.execute(
        "INSERT INTO event_accounting_receipts(source,source_ref,previous_receipt_id,assessment,transition,observed_at,token_json,summary_json) VALUES('eepro',?,?,'unassessed',?,'2026-09-17T00:00:00+00:00','{}','{}')",
        (source_ref, prior, transition),
    ).lastrowid


def test_keyset_pages_pin_highwater_and_do_not_count_later_or_other_events(tmp_path):
    with open_database(tmp_path) as db:
        first = append(db.connection, transition="initial_observation")
        second = append(db.connection, transition="reopened", prior=first)
        third = append(db.connection, transition="availability_restored", prior=second)
        append(db.connection, source_ref="eepro:other")
        before = db.connection.total_changes
        page1 = report(db.connection, source="eepro", source_ref="eepro:test", limit=2)
        assert db.connection.total_changes == before
        assert [r["receipt_id"] for r in page1["rows"]] == [first, second]
        assert page1["through"] == third and page1["next_cursor"] == second
        append(db.connection, prior=third)
        page2 = report(
            db.connection,
            source="eepro",
            source_ref="eepro:test",
            limit=2,
            after=second,
            through=page1["through"],
        )
        assert [r["receipt_id"] for r in page2["rows"]] == [third]
        assert page2["reached_high_water"] and not page2["complete_lifetime_history"]
        assert page2["page_counts"] == {"availability_restored": 1}
        assert page1["recording_started_at"] == "2026-09-17T00:00:00+00:00"
        assert not page2["recorded_prefix_included"]


def test_missing_legacy_streams_and_empty_new_streams_are_explicit(tmp_path):
    with sqlite3.connect(":memory:") as conn:
        assert not report(conn, source="s", source_ref="r")["supported"]
    with open_database(tmp_path) as db:
        value = report(db.connection, source="s", source_ref="r", stream="source_event_retirement")
        assert value["supported"] and value["reached_high_water"] and value["through"] == 0
        assert value["recording_started_at"] is None and not value["complete_lifetime_history"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 101},
        {"limit": True},
        {"after": -1},
        {"after": 2, "through": 1},
        {"stream": "unknown"},
    ],
)
def test_history_rejects_unbounded_or_incoherent_cursors(tmp_path, kwargs):
    with open_database(tmp_path) as db, pytest.raises(ValueError):
        report(db.connection, source="s", source_ref="r", **kwargs)


def test_unknown_future_highwater_is_rejected(tmp_path):
    with open_database(tmp_path) as db:
        append(db.connection)
        with pytest.raises(ValueError, match="high-water"):
            report(db.connection, source="eepro", source_ref="eepro:test", through=999)


def test_command_reads_while_writer_lock_is_held(tmp_path, monkeypatch, capsys):
    from swingset.schedule.event_history import main

    with open_database(tmp_path) as db:
        append(db.connection)
        monkeypatch.setattr(
            "sys.argv",
            [
                "event_history",
                "--state",
                str(tmp_path),
                "--source",
                "eepro",
                "--source-ref",
                "eepro:test",
            ],
        )
        main()
    result = json.loads(capsys.readouterr().out)
    assert len(result["rows"]) == 1 and result["reached_high_water"]
