import json
import sqlite3

from swingset.history.aliases import alias_proposals


def test_alias_packet_groups_editions_preserves_raw_rows_and_never_accepts(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE findings(finding_id,subject_id,evidence_json,snapshot_id,kind,owner_kind,closed_at);
        CREATE TABLE registry_placements(series_id,series_name_raw,event_month,snapshot_id);
        CREATE TABLE observations(observation_id,payload_json,snapshot_id,watch_id,scope_kind,parser_version,seq);
        CREATE TABLE snapshots(snapshot_id,url,archive_url,captured_at,fetched_at,body_sha256,observed_at);
        CREATE TABLE watches(watch_id,source);
        CREATE TABLE source_events(name_raw,start_date,end_date,location_raw,url,source,snapshot_id,parser_version);
    """)
    conn.execute("INSERT INTO watches VALUES ('watch','wsdc_calendar')")
    for year, name in [(2019, "Summer Swing 2019"), (2020, "SUMMER SWING 2020")]:
        snap = f"snapshot-{year}"
        conn.execute(
            "INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)",
            (
                snap,
                "https://example.org/events/",
                f"https://web.archive.org/{year}/events/",
                f"{year}-01-01",
                "2026-01-01",
                "body-digest",
                f"{year}-01-01",
            ),
        )
        conn.execute(
            "INSERT INTO findings VALUES (?,?,?,?,?,?,NULL)",
            (
                str(year),
                f"{year}-07:summer-swing",
                json.dumps({"printed_name": name, "year": year, "candidates": []}),
                snap,
                "series_alias",
                "history_year",
            ),
        )
        raw = {
            "name_raw": name,
            "start_date_raw": f"{year}-07-01",
            "end_date_raw": f"{year}-07-03",
            "location_raw": "Printed city",
        }
        conn.execute(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?)",
            (str(year), json.dumps(raw), snap, "watch", "calendar", "6", 0),
        )
        conn.execute(
            "INSERT INTO registry_placements VALUES (?,?,?,?)",
            ("wsdc-good", "Summer Swing Championships", f"{year}-07", snap),
        )
        conn.execute(
            "INSERT INTO registry_placements VALUES (?,?,?,?)",
            ("wsdc-other", "Summer Swing Championships", f"{year}-02", snap),
        )
    before = conn.total_changes
    assert alias_proposals(conn, tmp_path, now="2026-01-01") == {
        "findings": 2,
        "groups": 1,
        "registry_series": 2,
    }
    assert conn.total_changes == before
    packet = json.loads((tmp_path / "alias-proposals.json").read_text())
    group = packet["groups"][0]
    assert group["candidates"][0]["series_id"] == "wsdc-good"
    assert group["candidates"][0]["same_months"] == ["2019-07", "2020-07"]
    assert group["candidates"][1]["same_months"] == []
    assert len(group["variants"]) == 2
    assert group["evidence"][0]["raw_row"]["location_raw"] == "Printed city"
    assert group["evidence"][0]["body_sha256"] == "body-digest"
    assert "Nothing is selected automatically" in (tmp_path / "alias-review.html").read_text()
    assert not (tmp_path / "series_aliases.csv").exists()
