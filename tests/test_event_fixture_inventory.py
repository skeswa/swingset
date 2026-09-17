"""Retained scoring.dance fixtures exercise enumeration and real stage loss."""

from pathlib import Path

from test_admission import Corpus

from swingset.fetch.archive import Archive
from swingset.schedule.event_enumerations import bootstrap
from swingset.schedule.event_inventory import inventory
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.db import open_database


def test_retained_event_listing_does_not_turn_unfetched_rounds_into_results(tmp_path):
    fixtures = Path("src/swingset/sources/scoringdance/fixtures")
    body = (fixtures / "event-2026-09-09.body").read_bytes()
    with open_database(tmp_path) as database:
        corpus = Corpus(database)
        event = WatchSpec(
            "",
            "scoringdance",
            "event",
            "GET",
            "https://scoring.dance/enUS/events/418/results/",
            "scoringdance.event",
            source_ref="scoringdance:418",
        )
        upsert_watch(database.connection, event, corpus.clock.now())
        context = corpus.snapshot("retained-event", body, spec=event)
        generation, interpretation = corpus.stage(context, body=body)
        assert not interpretation.failures
        corpus.review(generation)  # Isolated test admission, never a production review.
        assert corpus.admit(generation) == "accepted"
        bootstrap(database, now=corpus.clock.now())
        with database.transaction(immediate=False) as conn:
            result = inventory(
                conn,
                Archive(tmp_path),
                source=event.source,
                source_ref=event.source_ref,
                now=corpus.clock.now(),
            )
        assert result["listed_pages"] == 12
        assert result["acquired_pages"] == result["interpreted_pages"] == 0
        assert not result["known_pages_accounted_for"]
        assert result["pagination"] == "unknown"
        assert result["published_pages"] is None
        assert all("not_acquired" in member["blockers"] for member in result["members"])

        round_body = (fixtures / "round-6011-2026-09-09.body").read_bytes()
        round_spec = WatchSpec(
            "",
            "scoringdance",
            "round",
            "GET",
            "https://scoring.dance/enUS/events/418/results/6011.html",
            "scoringdance.round",
            source_ref="scoringdance:418",
        )
        upsert_watch(database.connection, round_spec, corpus.clock.now())
        round_context = corpus.snapshot("retained-round", round_body, spec=round_spec)
        round_generation, round_report = corpus.stage(round_context, body=round_body)
        assert not round_report.failures
        corpus.review(round_generation)
        assert corpus.admit(round_generation) == "accepted"
        bootstrap(database, now=corpus.clock.now())
        with database.transaction(immediate=False) as conn:
            advanced = inventory(
                conn,
                Archive(tmp_path),
                source=event.source,
                source_ref=event.source_ref,
                now=corpus.clock.now(),
            )
        assert advanced["listed_pages"] == 12
        assert advanced["acquired_pages"] == advanced["interpreted_pages"] == 1
        assert advanced["published_pages"] is None

        # Losing the parent artifact must also invalidate its support, even
        # though every database row and the enumeration pointer still exist.
        digest = conn.execute(
            "SELECT body_sha256 FROM snapshots WHERE snapshot_id='retained-event'"
        ).fetchone()[0]
        corpus.archive.blob_path(digest).unlink()
        with database.transaction(immediate=False) as conn:
            reopened = inventory(
                conn,
                Archive(tmp_path),
                source=event.source,
                source_ref=event.source_ref,
                now=corpus.clock.now(),
            )
        assert reopened["enumeration_id"] == advanced["enumeration_id"]
        assert reopened["listed_pages"] == 12
        assert "body_artifact_unavailable" in reopened["blockers"]
        assert any(not parent["usable"] for parent in reopened["parent_support"])
