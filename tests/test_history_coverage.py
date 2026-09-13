import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
from materialized_fixture import materialize_seeded_outputs

from swingset.build.builder import BuildMetadata, build_candidate
from swingset.build.input import read_build_input
from swingset.model.canonical import Contest, Event, Round
from swingset.project.coverage import rebuild_coverage
from swingset.project.history import accept_year
from swingset.project.writer import Projection, replace_scope
from swingset.publish.card import render_card
from swingset.state.db import open_database

NOW = "2026-09-12T00:00:00Z"


def test_archive_results_have_separate_coverage_and_stable_repeated_build(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run',?,1)", (NOW,))
        for source, via, snapshot in (
            ("wsdc_registry", "origin", "registry"),
            ("eepro", "wayback", "round"),
        ):
            conn.execute(
                "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES (?,?, 'event','GET','https://example.test','test','sealed')",
                (snapshot, source),
            )
            conn.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification,via,captured_at,observed_at) "
                "VALUES (?,?,'GET','https://example.test',?,200,0,1,'run','Ok',?,'2019-09-01T00:00:00Z','2019-09-01T00:00:00Z')",
                (snapshot, snapshot, NOW, via),
            )
        provenance = dict(
            source="wsdc_registry",
            snapshot_id="registry",
            parser_version="1",
            first_seen_at=NOW,
            last_seen_at=NOW,
            run_id="run",
        )
        event = Event(
            event_id="2019-08-example",
            series_id="wsdc-1",
            name="Example",
            year=2019,
            start_date=None,
            end_date=None,
            event_month="2019-08",
            date_precision="month",
            held="held",
            coverage_tier="registry_only",
            history_source=("registry",),
            **provenance,
        )
        sheet = {**provenance, "source": "eepro", "snapshot_id": "round"}
        contest = Contest(
            contest_id="contest",
            event_id=event.event_id,
            name_raw="Novice",
            division="novice",
            age_division="none",
            contest_type="jack_and_jill",
            partner_mode="random_partner",
            dance_style="wcs",
            wsdc_points_eligible=True,
            combined_from=(),
            parse_status="parsed",
            source_contest_ref="contest",
            **sheet,
        )
        round_row = Round(
            round_id="round",
            contest_id="contest",
            round_type="final",
            round_index=1,
            name_raw="Finals",
            scoring_method="relative_placement",
            callback_legend="unknown",
            judge_count=0,
            chief_judge_id=None,
            entry_count=0,
            promoted_count=None,
            source_round_ref="round",
            score_sheet_url=None,
            **sheet,
        )
        with database.transaction():
            replace_scope(
                conn,
                scope_kind="event",
                scope_id=event.event_id,
                projection=Projection((event, contest, round_row)),
                run_id="run",
                projected_at=NOW,
                enqueue_links=False,
            )
            rebuild_coverage(conn, now=NOW)
        rows = {
            (row["source"], row["via"]): dict(row) for row in conn.execute("SELECT * FROM coverage")
        }
        assert rows["wsdc_registry", "origin"]["rounds"] == 0
        assert rows["eepro", "wayback"]["rounds"] == 1
        assert rows["eepro", "wayback"]["events_sheets_partial"] == 1
        assert rows["eepro", "wayback"]["events_sheets_complete"] == 0
        assert rows["eepro", "wayback"]["expected_rounds"] is None
        assert not any(row["events_accepted"] for row in rows.values())
        before = conn.execute("SELECT value FROM revisions WHERE name='canonical'").fetchone()[0]
        rebuild_coverage(conn, now="2026-09-13T00:00:00Z")
        assert (
            before
            == conn.execute("SELECT value FROM revisions WHERE name='canonical'").fetchone()[0]
        )
        assert {row[0] for row in conn.execute("SELECT last_changed_at FROM coverage")} == {NOW}
        materialize_seeded_outputs(database, now=NOW, run_id="run")
        accept_year(conn, 2019, accepted_by="reviewer", accepted_at=NOW)
        assert all(row[0] for row in conn.execute("SELECT events_accepted FROM coverage"))
        materialize_seeded_outputs(database, now=NOW, run_id="run")
        data = read_build_input(conn, SimpleNamespace(file_hashes={}, digest="test"))
        meta = BuildMetadata(
            "run", "code", None, 1, {}, render_card(data), datetime(2026, 9, 12, tzinfo=UTC)
        )
        result = build_candidate(database.state_dir, data, meta, card_renderer=render_card)
        published = pq.read_table(result.path / "data/coverage").to_pylist()
        assert len(published) == 2
        card = (result.path / "README.md").read_text()
        assert "| eepro | wayback | 2019 |" in card
        manifest = json.loads((result.path / "_meta/manifest.json").read_text())
        assert manifest["row_counts"]["coverage"] == 2
