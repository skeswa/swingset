import json
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from materialized_fixture import materialize_seeded_outputs
from test_identity_journal import decision
from test_project_event import EVENT, add, seed

from swingset.build.builder import BuildError, BuildMetadata, build_candidate
from swingset.build.identity_policy import baseline_tables
from swingset.build.input import read_build_input
from swingset.build.service import build_release
from swingset.clock import FakeClock
from swingset.config import Config
from swingset.fetch.archive import Archive
from swingset.project.contests import project_event
from swingset.project.writer import replace_scope
from swingset.sources.base import ParseContext
from swingset.sources.eepro import RoundPage
from swingset.sources.records import RoundSheet
from swingset.state.db import open_database
from swingset.state.identity_journal import encode_journal, token
from swingset.state.identity_references import references_for_subject
from swingset.state.inputs import InputBundle, accept
from swingset.state.work import WorkUnit, enqueue

NOW = "2026-09-13T06:00:00+00:00"


def _bundle(*decisions):
    journal = encode_journal(decisions)
    return InputBundle(
        sha256(journal).hexdigest(),
        Path("unused"),
        {
            "overrides/identity_overrides.csv": journal,
            "overrides/suppressions.csv": b"wsdc_id,reason\n",
            "versions.json": b'{"repository":"correction-fixture"}',
        },
        Config({}, {}),
    )


def _published_fixture(database, clock):
    conn = database.connection
    seed(conn)
    conn.execute("UPDATE events SET event_month='2026-08'")
    page = RoundPage()
    url = "https://eepro.com/results/summerhummer2026/jjfinals.html"
    body = Path(
        "src/swingset/sources/eepro/fixtures/round-jjfinals-summerhummer2026-2026-09-09.body"
    ).read_bytes()
    parsed = page.parse(
        page.extract(body),
        ParseContext("snapshot", "watch", url, "eepro", page.kind, "eepro:hummer", NOW),
    )
    sheet = next(
        row.payload
        for row in parsed.observations
        if isinstance(row.payload, RoundSheet)
        and row.payload.contest_name_raw == "Jack & Jill Advanced"
    )
    conn.execute("UPDATE source_event_map SET source_ref=?", (sheet.source_event_ref,))
    add(conn, "watch", "snapshot", sheet, NOW, source_ref=sheet.source_event_ref)
    conn.execute("UPDATE watches SET url=? WHERE watch_id='watch'", (url,))
    conn.execute(
        "UPDATE snapshots SET url=?,body_sha256=?,body_bytes=?,parser_version='1',parse_status='parsed' WHERE snapshot_id='snapshot'",
        (url, Archive(database.state_dir).store_body(body), len(body)),
    )
    prelim_url = "https://eepro.com/results/summerhummer2026/jjprelims.html"
    prelim_body = Path(
        "src/swingset/sources/eepro/fixtures/round-jjprelims-summerhummer2026-2026-09-09.body"
    ).read_bytes()
    preliminary = page.parse(
        page.extract(prelim_body),
        ParseContext(
            "prelim-snapshot", "prelim-watch", prelim_url, "eepro", page.kind, "eepro:hummer", NOW
        ),
    )
    prelim = next(
        row.payload
        for row in preliminary.observations
        if isinstance(row.payload, RoundSheet)
        and row.payload.contest_name_raw == "Jack & Jill Leader Advanced"
    )
    add(conn, "prelim-watch", "prelim-snapshot", prelim, NOW, source_ref=prelim.source_event_ref)
    conn.execute("UPDATE watches SET url=? WHERE watch_id='prelim-watch'", (prelim_url,))
    conn.execute(
        "UPDATE snapshots SET url=?,body_sha256=?,body_bytes=?,parser_version='1',parse_status='parsed' WHERE snapshot_id='prelim-snapshot'",
        (prelim_url, Archive(database.state_dir).store_body(prelim_body), len(prelim_body)),
    )
    replace_scope(
        conn,
        scope_kind="event",
        scope_id=EVENT,
        projection=project_event(conn, EVENT, NOW, "run_a"),
        run_id="run_a",
        projected_at=NOW,
    )
    entry = conn.execute(
        "SELECT e.entry_id,e.name_raw FROM entries e JOIN placements p ON p.leader_entry_id=e.entry_id ORDER BY e.entry_id LIMIT 1"
    ).fetchone()
    identifier = entry["entry_id"]
    binding = references_for_subject(conn, "entry", identifier)[0]
    positive = decision(binding, "same_person")
    bundle = _bundle(positive)
    accept(database, bundle, clock)
    conn.execute(
        "INSERT INTO dancers(wsdc_id,first_name,last_name,name_norm,is_pro,primary_role,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,leader_highest_points,follower_highest_level,follower_highest_points,recent_year,registry_internal_id,registry_fetched_at,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (100,?,'Fixture','fixture',0,'leader','advanced','advanced','novice','novice','advanced',10,'novice',0,2026,100,?,'wsdc_registry','snapshot','1',?,?,'run_a')",
        (entry["name_raw"], NOW, NOW, NOW),
    )
    conn.execute(
        "UPDATE entries SET wsdc_id=100,link_status='confirmed',link_confidence=1 WHERE entry_id=?",
        (identifier,),
    )
    conn.execute(
        "INSERT INTO identity_links VALUES ('link','entry',?,100,'manual','confirmed',1,'[]',?,'run_a','1')",
        (identifier, NOW),
    )
    conn.execute(
        "UPDATE placements SET leader_wsdc_id=100,registry_points_leader=10,registry_confirmed=1,points_matches_expected=1 WHERE leader_entry_id=?",
        (identifier,),
    )
    materialize_seeded_outputs(database, now=NOW, run_id="run_a")
    data = read_build_input(conn, bundle)
    baseline = build_candidate(
        database.state_dir,
        data,
        BuildMetadata(
            "run_a",
            "fixture",
            None,
            1,
            {"repository": "fixture"},
            b"Fixture baseline\n",
            clock.now(),
            "fixture-baseline",
        ),
    )
    (baseline.path / "PUBLISHED").write_text('{"commit":"published-fixture"}')
    (database.state_dir / "baseline").symlink_to(baseline.path)
    return baseline.path, identifier, binding, positive


def test_correction_only_revokes_journal_identity_while_unrelated_parse_remains_blocked(tmp_path):
    clock = FakeClock(datetime(2026, 9, 13, 6, tzinfo=UTC))
    with open_database(tmp_path) as database:
        baseline, identifier, binding, positive = _published_fixture(database, clock)
        original = baseline_tables(baseline)
        assert original["final_marks"] and original["callback_marks"]
        assert any(
            row["leader_wsdc_id"] == 100 and row["registry_points_leader"] == 10
            for row in original["placements"]
        )
        conn = database.connection
        hold = replace(
            decision(binding, "hold_unlinked", "NONE", "hold", positive.decision_id),
            reason="PRIVATE reviewer narrative must not be public",
        )
        bundle = _bundle(positive, hold)
        accept(database, bundle, clock)
        # New current-state evidence and output must never enter a baseline correction.
        conn.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES ('unrelated','eepro','round','GET','https://example.test/unrelated','eepro.round','live')"
        )
        conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification,parse_status) VALUES ('new-snapshot','unrelated','GET','https://example.test/unrelated',?,200,0,1,'run_a','Ok','failed')",
            (NOW,),
        )
        event = dict(conn.execute("SELECT * FROM events WHERE event_id=?", (EVENT,)).fetchone())
        event.update(
            event_id="new-current-event", snapshot_id="new-snapshot", name="Current-only event"
        )
        conn.execute(
            "INSERT INTO events("
            + ",".join(event)
            + ") VALUES ("
            + ",".join("?" for _ in event)
            + ")",
            tuple(event.values()),
        )
        enqueue(conn, (WorkUnit("parse", "snapshot", "new-snapshot"),), enqueued_at=NOW)
        pending = [
            tuple(row)
            for row in conn.execute("SELECT * FROM pending_work ORDER BY stage,unit_kind,unit_id")
        ]
        ordinary = build_release(database, bundle, clock, "run_a")
        import pyarrow.parquet as pq

        ordinary_rows = {
            name: [
                row
                for path in sorted((ordinary.path / "data" / name).glob("*.parquet"))
                for batch in pq.ParquetFile(path).iter_batches()
                for row in batch.to_pylist()
            ]
            for name in ("events", "entries")
        }
        assert not any(row["event_id"] == "new-current-event" for row in ordinary_rows["events"])
        assert all(
            row.get("wsdc_id") is None
            for row in ordinary_rows["entries"]
            if row["entry_id"] == identifier
        )
        result = build_release(database, bundle, clock, "run_a", correction_only=True)
        # Read the actual candidate Parquet, without inventing a publication receipt.
        import pyarrow.parquet as pq

        corrected = {
            name: [
                row
                for path in sorted((result.path / "data" / name).glob("*.parquet"))
                for row in pq.read_table(path).to_pylist()
            ]
            for name in original
        }
        entry = next(row for row in corrected["entries"] if row["entry_id"] == identifier)
        assert entry["wsdc_id"] is None
        for placement in corrected["placements"]:
            if placement["leader_entry_id"] == identifier:
                assert placement["leader_wsdc_id"] is None
                assert placement["registry_points_leader"] is None
                assert placement["points_matches_expected"] is None
                assert placement["registry_confirmed"] is False
        for name in ("final_marks", "callback_marks", "callbacks", "heats"):
            assert corrected[name] == original[name]
        assert {row["event_id"] for row in corrected["events"]} == {
            row["event_id"] for row in original["events"]
        }
        assert {row["snapshot_id"] for row in corrected["snapshots"]} == {
            row["snapshot_id"] for row in original["snapshots"]
        }
        assertion = next(
            row for row in corrected["identity_links"] if row["subject_id"] == identifier
        )
        assert assertion["source_ref_ids"] == [binding.reference.ref_id]
        assert assertion["decision_ids"] == [hold.decision_id]
        assert assertion["acceptance_state"] == "revoked"
        assert assertion["acceptance_policy"]
        assert assertion["journal_digest"] == token(conn).digest
        assert assertion["journal_generation"] == token(conn).generation
        manifest = json.loads((result.path / "_meta/manifest.json").read_bytes())
        assert manifest["release_policy"]["mode"] == "correction_only"
        assert manifest["release_policy"]["pending_work"]["parse"] == 1
        assert [
            tuple(row)
            for row in conn.execute("SELECT * FROM pending_work ORDER BY stage,unit_kind,unit_id")
        ] == pending
        assert (
            conn.execute(
                "SELECT parse_status FROM snapshots WHERE snapshot_id='new-snapshot'"
            ).fetchone()[0]
            == "failed"
        )
        assert (
            conn.execute("SELECT wsdc_id FROM entries WHERE entry_id=?", (identifier,)).fetchone()[
                0
            ]
            == 100
        )
        assert "PRIVATE reviewer" not in json.dumps(manifest)
        assert "PRIVATE reviewer" not in str(corrected)
        ordinary = build_release(database, bundle, clock, "run_a")
        import pyarrow.parquet as pq

        ordinary_rows = {
            name: [
                row
                for path in sorted((ordinary.path / "data" / name).glob("*.parquet"))
                for batch in pq.ParquetFile(path).iter_batches()
                for row in batch.to_pylist()
            ]
            for name in ("events", "entries")
        }
        assert not any(row["event_id"] == "new-current-event" for row in ordinary_rows["events"])
        assert all(
            row.get("wsdc_id") is None
            for row in ordinary_rows["entries"]
            if row["entry_id"] == identifier
        )


def test_correction_retains_reviewed_baseline_identity_when_no_revocation_exists(tmp_path):
    clock = FakeClock(datetime(2026, 9, 13, 6, tzinfo=UTC))
    with open_database(tmp_path) as database:
        _, identifier, _, positive = _published_fixture(database, clock)
        enqueue(
            database.connection,
            (WorkUnit("parse", "snapshot", "unrelated-pending"),),
            enqueued_at=NOW,
        )
        result = build_release(database, _bundle(positive), clock, "run_a", correction_only=True)
        import pyarrow.parquet as pq

        entries = pq.read_table(result.path / "data/entries/entries.parquet").to_pylist()
        assert next(row for row in entries if row["entry_id"] == identifier)["wsdc_id"] == 100


def test_correction_requires_an_acknowledged_baseline(tmp_path):
    clock = FakeClock(datetime(2026, 9, 13, 6, tzinfo=UTC))
    with open_database(tmp_path) as database:
        bundle = _bundle()
        accept(database, bundle, clock)
        with pytest.raises(BuildError, match="requires a published baseline"):
            build_release(database, bundle, clock, "run_a", correction_only=True)


def test_correction_suppression_scrubs_streamed_history_and_keeps_structural_keys(tmp_path):
    import pyarrow.parquet as pq

    clock = FakeClock(datetime(2026, 9, 13, 6, tzinfo=UTC))
    with open_database(tmp_path) as database:
        _, first_entry, _, first_decision = _published_fixture(database, clock)
        conn = database.connection
        second = conn.execute(
            "SELECT e.entry_id,e.name_raw FROM entries e JOIN placements p ON p.leader_entry_id=e.entry_id WHERE e.entry_id<>? ORDER BY e.entry_id LIMIT 1",
            (first_entry,),
        ).fetchone()
        second_binding = references_for_subject(conn, "entry", second["entry_id"])[0]
        second_decision = decision(second_binding, "same_person", "101", "second-person")
        bundle = _bundle(first_decision, second_decision)
        accept(database, bundle, clock)
        dancer = dict(conn.execute("SELECT * FROM dancers WHERE wsdc_id=100").fetchone())
        dancer.update(
            wsdc_id=101,
            registry_internal_id=101,
            first_name=second["name_raw"],
            name_norm=second["name_raw"].casefold(),
        )
        conn.execute(
            "INSERT INTO dancers("
            + ",".join(dancer)
            + ") VALUES ("
            + ",".join("?" for _ in dancer)
            + ")",
            tuple(dancer.values()),
        )
        conn.execute(
            "UPDATE entries SET wsdc_id=101,link_status='confirmed',link_confidence=1 WHERE entry_id=?",
            (second["entry_id"],),
        )
        conn.execute(
            "INSERT INTO identity_links VALUES ('second-link','entry',?,101,'manual','confirmed',1,'[]',?,'run_a','1')",
            (second["entry_id"], NOW),
        )
        conn.execute(
            "UPDATE placements SET leader_wsdc_id=101,registry_points_leader=10,registry_confirmed=1,points_matches_expected=1 WHERE leader_entry_id=?",
            (second["entry_id"],),
        )
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name_raw FROM entries WHERE entry_id IN (?,?)",
                (first_entry, second["entry_id"]),
            )
        ]
        materialize_seeded_outputs(database, now=NOW, run_id="run_a")
        second_baseline = build_candidate(
            database.state_dir,
            read_build_input(conn, bundle),
            BuildMetadata(
                "run_a",
                "fixture",
                "published-fixture",
                1,
                {"repository": "fixture"},
                b"Two-person fixture baseline\n",
                clock.now(),
                "two-person-baseline",
            ),
        )
        (second_baseline.path / "PUBLISHED").write_text('{"commit":"published-two-person-fixture"}')
        (database.state_dir / "baseline").unlink()
        (database.state_dir / "baseline").symlink_to(second_baseline.path)
        original = baseline_tables(second_baseline.path)
        old_history = pq.read_table(
            second_baseline.path / "data/changelog/changelog.parquet"
        ).to_pylist()
        assert old_history
        assert all(name in str(old_history) for name in names)
        assert {row["wsdc_id"] for row in original["dancers"]} == {100, 101}

        private_reason = "PRIVATE operator explanation never suitable for publication"
        suppression = f"wsdc_id,reason,date\n100,{private_reason},2026-09-13\n101,{private_reason},2026-09-13\n".encode()
        files = {**bundle.files, "overrides/suppressions.csv": suppression}
        corrected_bundle = replace(
            bundle, digest=sha256(bundle.digest.encode() + suppression).hexdigest(), files=files
        )
        accept(database, corrected_bundle, clock)
        result = build_release(database, corrected_bundle, clock, "run_a", correction_only=True)
        public = {
            name: [
                row
                for path in sorted((result.path / "data" / name).glob("*.parquet"))
                for row in pq.read_table(path).to_pylist()
            ]
            for name in original
        }
        all_public = str(public).casefold()
        assert private_reason.casefold() not in all_public
        assert all(name.casefold() not in all_public for name in names)
        assert public["dancers"] == []  # Rows are omitted, not collapsed onto duplicate null keys.
        assert public["changelog"]  # Unaffected historical changes remain useful.
        for table, rows in public.items():
            for row in rows:
                for field, value in row.items():
                    if "wsdc_id" in field:
                        assert value not in (100, 101), (table, field)
                if table == "changelog":
                    assert '"wsdc_id": 100' not in str(row)
                    assert '"wsdc_id": 101' not in str(row)
                    assert not (
                        row["table"] == "dancers"
                        and json.loads(row["record_key"]) in ([100], [101])
                    )
        suppressed = [
            row for row in public["entries"] if row["entry_id"] in (first_entry, second["entry_id"])
        ]
        assert len(suppressed) == 2
        assert all(
            row["name_raw"] is None
            and row["wsdc_id"] is None
            and row["link_status"] == "suppressed"
            for row in suppressed
        )
        for table in ("final_marks", "callback_marks", "callbacks", "heats"):
            assert public[table] == original[table]
        entry_ids = {row["entry_id"] for row in public["entries"]}
        judge_ids = {row["judge_id"] for row in public["judges"]}
        placement_ids = {row["placement_id"] for row in public["placements"]}
        assert len(entry_ids) == len(public["entries"])
        for row in public["callback_marks"]:
            assert row["entry_id"] in entry_ids and row["judge_id"] in judge_ids
        for row in public["final_marks"]:
            assert row["placement_id"] in placement_ids and row["judge_id"] in judge_ids
        for row in public["placements"]:
            for role in ("leader", "follower", "couple"):
                assert row.get(f"{role}_entry_id") is None or row[f"{role}_entry_id"] in entry_ids
        manifest = json.loads((result.path / "_meta/manifest.json").read_bytes())
        assert private_reason not in json.dumps(manifest)
        assert manifest["release_policy"]["mode"] == "correction_only"
