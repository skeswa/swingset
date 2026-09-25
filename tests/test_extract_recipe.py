"""Extraction cache validity follows actual recipes as well as version labels."""

from test_work_isolation import FIXTURE, seed_snapshot

from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.schedule import parse
from swingset.sources.wsdc_calendar.adapter import EventsPage
from swingset.state.db import open_database
from swingset.state.work import enqueue

KIND = EventsPage.kind


def _extract_calls(tmp_path, monkeypatch, name, digests, *, other=None):
    """Parse one snapshot once per digest of `name`; return the extracted bodies."""
    calls = []

    class CountingPage(EventsPage):
        def extract(self, body):
            calls.append(body)
            return super().extract(body)

    monkeypatch.setattr(parse, "get_page_kind", lambda _: CountingPage())
    clock = FakeClock()
    with open_database(tmp_path) as db:
        unit, body_sha, run = seed_snapshot(db, clock, "retained", FIXTURE)
        for index, value in enumerate(digests):
            db.connection.execute(
                "INSERT INTO accepted_inputs VALUES ('pipeline',?,?) "
                "ON CONFLICT(consumer,input_name) DO UPDATE SET digest=excluded.digest",
                (name, value),
            )
            if other is not None:
                db.connection.execute(
                    "INSERT INTO accepted_inputs VALUES ('pipeline',?,?) "
                    "ON CONFLICT(consumer,input_name) DO UPDATE SET digest=excluded.digest",
                    (other, f"other-{index}"),
                )
            enqueue(db.connection, (unit,), enqueued_at=clock.now().isoformat())
            assert not parse.parse_snapshot(db, Archive(tmp_path), unit, clock, run).failed
            assert (
                db.connection.execute(
                    "SELECT extract_recipe_sha256 FROM snapshots WHERE snapshot_id=?",
                    (unit.unit_id,),
                ).fetchone()[0]
                == value
            )
        row = db.connection.execute(
            "SELECT body_sha256,extract_version,parser_version FROM snapshots WHERE snapshot_id=?",
            (unit.unit_id,),
        ).fetchone()
        assert tuple(row) == (
            body_sha,
            str(EventsPage.EXTRACT_VERSION),
            str(EventsPage.PARSER_VERSION),
        )
    return calls


def test_changed_extract_scope_reextracts_with_unchanged_labels_and_identical_body(
    tmp_path, monkeypatch
):
    calls = _extract_calls(
        tmp_path,
        monkeypatch,
        f"recipe/code/extract/{KIND}",
        ("first-artifact", "first-artifact", "changed-artifact"),
    )
    assert calls == [FIXTURE, FIXTURE]


def test_parse_scope_or_environment_change_reuses_the_extract(tmp_path, monkeypatch):
    """Only the kind's extract scope decides extract reuse (D-0207)."""
    calls = _extract_calls(
        tmp_path,
        monkeypatch,
        f"recipe/code/extract/{KIND}",
        ("same-extract",) * 3,
        other=f"recipe/code/parse/{KIND}",
    )
    assert calls == [FIXTURE]


def test_another_kinds_extract_scope_is_not_this_kinds_recipe(tmp_path, monkeypatch):
    calls = _extract_calls(
        tmp_path,
        monkeypatch,
        f"recipe/code/extract/{KIND}",
        ("same-extract",) * 2,
        other="recipe/code/extract/eepro.round",
    )
    assert calls == [FIXTURE]


def test_a_bundle_from_before_scopes_still_keys_extracts_on_its_runtime(tmp_path, monkeypatch):
    calls = _extract_calls(
        tmp_path,
        monkeypatch,
        "recipe/runtime",
        ("first-artifact", "first-artifact", "changed-artifact"),
    )
    assert calls == [FIXTURE, FIXTURE]
