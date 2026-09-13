"""Extraction cache validity follows actual recipes as well as version labels."""

from test_work_isolation import FIXTURE, seed_snapshot

from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.schedule import parse
from swingset.sources.wsdc_calendar.adapter import EventsPage
from swingset.state.db import open_database
from swingset.state.work import enqueue


def test_changed_runtime_reextracts_with_unchanged_labels_and_identical_body(tmp_path, monkeypatch):
    calls = []

    class CountingPage(EventsPage):
        def extract(self, body):
            calls.append(body)
            return super().extract(body)

    monkeypatch.setattr(parse, "get_page_kind", lambda _: CountingPage())
    clock = FakeClock()
    with open_database(tmp_path) as db:
        unit, body_sha, run = seed_snapshot(db, clock, "retained", FIXTURE)
        db.connection.execute(
            "INSERT INTO accepted_inputs VALUES ('pipeline','recipe/runtime','first-artifact')"
        )
        for recipe in ("first-artifact", "first-artifact", "changed-artifact"):
            db.connection.execute(
                "UPDATE accepted_inputs SET digest=? WHERE input_name='recipe/runtime'", (recipe,)
            )
            enqueue(db.connection, (unit,), enqueued_at=clock.now().isoformat())
            assert not parse.parse_snapshot(db, Archive(tmp_path), unit, clock, run).failed
            assert (
                db.connection.execute(
                    "SELECT extract_recipe_sha256 FROM snapshots WHERE snapshot_id=?",
                    (unit.unit_id,),
                ).fetchone()[0]
                == recipe
            )
        assert calls == [FIXTURE, FIXTURE]
        row = db.connection.execute(
            "SELECT body_sha256,extract_version,parser_version FROM snapshots WHERE snapshot_id=?",
            (unit.unit_id,),
        ).fetchone()
        assert tuple(row) == (
            body_sha,
            str(EventsPage.EXTRACT_VERSION),
            str(EventsPage.PARSER_VERSION),
        )
